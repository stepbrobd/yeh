import hashlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from yeh import routes
from yeh.mailbox import EmailSummary, HeyClient, InboxPage, TopicPayload
from yeh.storage import SessionRecord, Storage, TopicSyncState


@dataclass(frozen=True)
class MailboxRefreshResult:
    mailbox: routes.Mailbox
    pages_scanned: int
    topics_seen: int
    new_topics: int
    messages_synced: int
    messages_updated: int


def refresh_mailbox(
    storage: Storage,
    client: HeyClient,
    hey_email: str,
    mailbox: routes.Mailbox,
    *,
    sync_max_age: timedelta = timedelta(minutes=15),
    max_pages: int | None = None,
    progress: Callable[[str], None] | None = None,
    workers: int = 4,
    consecutive_skip_threshold: int = 10,
) -> MailboxRefreshResult:
    """Scan the mailbox page-by-page and deep-fetch topics that need it.

    Early-exit heuristic
    --------------------
    When the IMAP client is checking for new mail the most-recently-active
    topics appear first.  Once we have seen *consecutive_skip_threshold*
    topics in a row that are already up-to-date in the database (non-empty
    messages AND summary hash unchanged AND synced recently) we stop paging —
    everything older is almost certainly already current too.
    """
    mailbox_path = routes.MAILBOX_PATHS[mailbox]
    mailbox_url = routes.mailbox_url(client.account.hey_host, mailbox)

    next_url: str | None = mailbox_path
    visited: set[str] = set()
    pages_scanned = 0
    topics_seen = 0
    new_topics = 0
    messages_synced = 0
    messages_updated = 0
    consecutive_skipped = 0

    while next_url:
        if next_url in visited:
            break
        if max_pages is not None and pages_scanned >= max_pages:
            break
        visited.add(next_url)

        page = client.fetch_page(next_url)
        pages_scanned += 1
        if progress is not None:
            progress(
                f"refresh mailbox={mailbox.value} page={pages_scanned} topics={topics_seen} messages={messages_synced}"
            )

        deep_topics: list[EmailSummary] = []
        page_early_exit = False

        for topic in page.emails:
            topic_id = parse_topic_id(topic.topic_url)
            if topic_id is None:
                continue

            summary_hash = topic_summary_hash(topic)
            state = storage.topic_sync_state(hey_email=hey_email, topic_id=topic_id)
            if state is None:
                new_topics += 1

            storage.upsert_topic(
                hey_email=hey_email,
                topic_id=topic_id,
                topic_url=topic.topic_url,
                sender=topic.sender,
                subject=topic.subject,
                snippet=topic.snippet,
                when_text=topic.when,
                summary_hash=summary_hash,
            )
            storage.assign_topic_mailbox(
                hey_email=hey_email,
                topic_id=topic_id,
                mailbox=mailbox,
                mailbox_url=mailbox_url,
            )
            topics_seen += 1

            message_count = storage.topic_message_count(
                hey_email=hey_email, topic_id=topic_id
            )
            if needs_deep_sync(
                state=state,
                current_summary_hash=summary_hash,
                message_count=message_count,
                sync_max_age=sync_max_age,
            ):
                deep_topics.append(topic)
                consecutive_skipped = 0
            else:
                consecutive_skipped += 1
                if consecutive_skipped >= consecutive_skip_threshold:
                    page_early_exit = True
                    break

        payloads = _fetch_payloads_parallel(
            client=client,
            topics=deep_topics,
            workers=workers,
            progress=progress,
        )
        for payload in payloads:
            for message in payload.messages:
                changed = storage.upsert_message_text(
                    hey_email=hey_email,
                    topic_id=payload.topic_id,
                    message_id=message.message_id,
                    source_url=message.source_url,
                    content_text=message.content_text,
                )
                messages_synced += 1
                if changed:
                    messages_updated += 1
            storage.mark_topic_synced(hey_email=hey_email, topic_id=payload.topic_id)

        if progress is not None:
            progress(
                f"refresh mailbox={mailbox.value} page_done={pages_scanned} topics={topics_seen} messages={messages_synced}"
            )

        if page_early_exit:
            break

        next_url = page.next_page_url

    return MailboxRefreshResult(
        mailbox=mailbox,
        pages_scanned=pages_scanned,
        topics_seen=topics_seen,
        new_topics=new_topics,
        messages_synced=messages_synced,
        messages_updated=messages_updated,
    )


def _fetch_payloads_parallel(
    client: HeyClient,
    topics: list[EmailSummary],
    workers: int,
    progress: Callable[[str], None] | None = None,
) -> list[TopicPayload]:
    if not topics:
        return []
    if workers <= 1 or len(topics) == 1:
        out: list[TopicPayload] = []
        for topic in topics:
            try:
                out.append(_fetch_topic_payload_with_retry(client, topic))
            except httpx.HTTPStatusError as exc:
                if progress is not None:
                    progress(
                        f"refresh skip topic={topic.topic_url} status={exc.response.status_code}"
                    )
            except (httpx.HTTPError, RuntimeError, ValueError):  # fmt: skip
                if progress is not None:
                    progress(f"refresh skip topic={topic.topic_url} error=fetch_failed")
        return out

    cookie_jar_json, csrf_token, final_url = client.export_session_state()
    base_session = SessionRecord(
        cookie_jar_json=cookie_jar_json,
        csrf_token=csrf_token,
        final_url=final_url,
        authenticated_at="",
    )
    account = client.account

    # each worker gets its own HeyClient with an independent httpx.Client and a
    # snapshot of the cookie jar.  cookie updates received by workers are
    # discarded; the caller is responsible for persisting the primary client's
    # session after the pool completes.
    def fetch(topic: EmailSummary):
        worker_client = HeyClient(account=account, session=base_session)
        try:
            return _fetch_topic_payload_with_retry(worker_client, topic)
        finally:
            worker_client.close()

    out = []
    with ThreadPoolExecutor(max_workers=min(workers, len(topics))) as executor:
        futures = {executor.submit(fetch, topic): topic for topic in topics}
        for future in as_completed(futures):
            topic = futures[future]
            try:
                out.append(future.result())
            except httpx.HTTPStatusError as exc:
                if progress is not None:
                    progress(
                        f"refresh skip topic={topic.topic_url} status={exc.response.status_code}"
                    )
            except (httpx.HTTPError, RuntimeError, ValueError):  # fmt: skip
                if progress is not None:
                    progress(f"refresh skip topic={topic.topic_url} error=fetch_failed")
    return out


def _fetch_topic_payload_with_retry(
    client: HeyClient, topic: EmailSummary
) -> TopicPayload:
    delay = 1.0
    for attempt in range(1, 6):
        try:
            return client.fetch_topic_payload(topic)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 5:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None and retry_after.isdigit():
                sleep_s = max(1.0, float(retry_after))
            else:
                sleep_s = delay
            time.sleep(sleep_s)
            delay = min(delay * 2.0, 30.0)
    raise RuntimeError("unreachable")


def sync_page(
    storage: Storage,
    client: HeyClient,
    hey_email: str,
    page: InboxPage,
    mailbox: routes.Mailbox,
    *,
    sync_max_age: timedelta = timedelta(minutes=15),
) -> tuple[int, int, int]:
    mailbox_url = routes.mailbox_url(client.account.hey_host, mailbox)
    topics = 0
    messages = 0
    updates = 0

    for topic in page.emails:
        topic_id = parse_topic_id(topic.topic_url)
        if topic_id is None:
            continue
        summary_hash = topic_summary_hash(topic)
        state = storage.topic_sync_state(hey_email=hey_email, topic_id=topic_id)
        message_count = storage.topic_message_count(
            hey_email=hey_email, topic_id=topic_id
        )
        deep = needs_deep_sync(
            state=state,
            current_summary_hash=summary_hash,
            message_count=message_count,
            sync_max_age=sync_max_age,
        )

        storage.upsert_topic(
            hey_email=hey_email,
            topic_id=topic_id,
            topic_url=topic.topic_url,
            sender=topic.sender,
            subject=topic.subject,
            snippet=topic.snippet,
            when_text=topic.when,
            summary_hash=summary_hash,
        )
        storage.assign_topic_mailbox(
            hey_email=hey_email,
            topic_id=topic_id,
            mailbox=mailbox,
            mailbox_url=mailbox_url,
        )
        topics += 1

        if not deep:
            continue
        payload = client.fetch_topic_payload(topic)
        for message in payload.messages:
            changed = storage.upsert_message_text(
                hey_email=hey_email,
                topic_id=payload.topic_id,
                message_id=message.message_id,
                source_url=message.source_url,
                content_text=message.content_text,
            )
            messages += 1
            if changed:
                updates += 1
        storage.mark_topic_synced(hey_email=hey_email, topic_id=payload.topic_id)

    return topics, messages, updates


def topic_summary_hash(topic: EmailSummary) -> str:
    raw = f"{topic.topic_url}\n{topic.sender}\n{topic.subject}\n{topic.snippet}\n{topic.when}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_topic_id(topic_url: str) -> str | None:
    """Extract the topic ID from a topic URL.

    Parses the URL path and returns the segment immediately after ``/topics/``,
    or ``None`` if the URL does not contain a topics path segment.
    """
    path_parts = [p for p in urlparse(topic_url).path.split("/") if p]
    try:
        idx = path_parts.index("topics")
    except ValueError:
        return None
    if idx + 1 >= len(path_parts):
        return None
    return path_parts[idx + 1]


def needs_deep_sync(
    state: TopicSyncState | None,
    current_summary_hash: str,
    message_count: int = 0,
    sync_max_age: timedelta = timedelta(minutes=15),
) -> bool:
    """Return True when the topic needs a full message fetch.

    Always fetches when:
    - the topic has never been synced (state is None)
    - the topic has no stored messages (message_count == 0)
    - the listing summary changed (new/updated message in thread)
    - the topic has not been deep-synced yet (last_synced_at is None)
    - the last deep-sync is older than *sync_max_age*
    """
    if state is None:
        return True
    if message_count == 0:
        return True
    if state.summary_hash != current_summary_hash:
        return True
    if state.last_synced_at is None:
        return True
    try:
        synced_at = datetime.fromisoformat(state.last_synced_at)
    except ValueError:
        return True

    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=UTC)
    now = datetime.now(tz=UTC)
    return now - synced_at >= sync_max_age
