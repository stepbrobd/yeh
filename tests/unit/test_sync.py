"""Tests for sync.needs_deep_sync and refresh_mailbox early-exit heuristic."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from yeh import routes
from yeh.mailbox import EmailSummary, HeyClient, MessagePayload, TopicPayload
from yeh.storage import Storage, TopicSyncState
from yeh.sync import needs_deep_sync, refresh_mailbox, topic_summary_hash

# ---------------------------------------------------------------------------
# needs_deep_sync unit tests
# ---------------------------------------------------------------------------


def test_needs_deep_sync_no_state_returns_true() -> None:
    assert needs_deep_sync(state=None, current_summary_hash="abc", message_count=5)


def test_needs_deep_sync_zero_messages_returns_true() -> None:
    """Always deep-fetch when no messages are stored, even if hash is current."""
    now_iso = datetime.now(tz=UTC).isoformat()
    state = TopicSyncState(summary_hash="abc", last_synced_at=now_iso)
    assert needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=0,
    )


def test_needs_deep_sync_zero_messages_overrides_fresh_sync() -> None:
    """message_count=0 forces deep fetch even when synced 1 second ago."""
    just_now = (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
    state = TopicSyncState(summary_hash="same", last_synced_at=just_now)
    assert needs_deep_sync(
        state=state,
        current_summary_hash="same",
        message_count=0,
        sync_max_age=timedelta(minutes=15),
    )


def test_needs_deep_sync_hash_changed_returns_true() -> None:
    now_iso = datetime.now(tz=UTC).isoformat()
    state = TopicSyncState(summary_hash="old", last_synced_at=now_iso)
    assert needs_deep_sync(
        state=state,
        current_summary_hash="new",
        message_count=3,
    )


def test_needs_deep_sync_no_last_synced_at_returns_true() -> None:
    state = TopicSyncState(summary_hash="abc", last_synced_at=None)
    assert needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=1,
    )


def test_needs_deep_sync_stale_returns_true() -> None:
    old_iso = (datetime.now(tz=UTC) - timedelta(minutes=30)).isoformat()
    state = TopicSyncState(summary_hash="abc", last_synced_at=old_iso)
    assert needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=2,
        sync_max_age=timedelta(minutes=15),
    )


def test_needs_deep_sync_fresh_with_messages_returns_false() -> None:
    """When messages exist, hash unchanged, and synced recently: skip."""
    just_now = (datetime.now(tz=UTC) - timedelta(seconds=30)).isoformat()
    state = TopicSyncState(summary_hash="abc", last_synced_at=just_now)
    assert not needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=1,
        sync_max_age=timedelta(minutes=15),
    )


def test_needs_deep_sync_naive_datetime_treated_as_utc() -> None:
    just_now = (
        (datetime.now(UTC) - timedelta(seconds=30)).replace(tzinfo=None).isoformat()
    )
    state = TopicSyncState(summary_hash="abc", last_synced_at=just_now)
    assert not needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=1,
        sync_max_age=timedelta(minutes=15),
    )


def test_needs_deep_sync_invalid_timestamp_returns_true() -> None:
    state = TopicSyncState(summary_hash="abc", last_synced_at="not-a-date")
    assert needs_deep_sync(
        state=state,
        current_summary_hash="abc",
        message_count=1,
    )


# ---------------------------------------------------------------------------
# refresh_mailbox early-exit heuristic integration tests
# ---------------------------------------------------------------------------


def _make_topic(n: int) -> EmailSummary:
    return EmailSummary(
        topic_url=f"https://app.hey.com/topics/{n}",
        sender=f"sender{n}@example.com",
        subject=f"Subject {n}",
        snippet=f"snippet {n}",
        when=f"2026-01-{n:02d}",
    )


def _make_payload(topic_id: str) -> TopicPayload:
    msg = MessagePayload(
        message_id=f"msg-{topic_id}",
        source_url=f"https://app.hey.com/messages/{topic_id}.text",
        content_text=f"From: sender@example.com\r\nSubject: s\r\n\r\nbody {topic_id}",
    )
    return TopicPayload(
        topic_id=topic_id,
        topic_url=f"https://app.hey.com/topics/{topic_id}",
        sender="sender@example.com",
        subject=f"Subject {topic_id}",
        snippet=f"snippet {topic_id}",
        when="2026-01-01",
        messages=[msg],
    )


class _FakePage:
    def __init__(self, emails: list[EmailSummary], next_page_url: str | None = None):
        self.emails = emails
        self.next_page_url = next_page_url


class _FakeHeyClient:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = iter(pages)
        self.fetched_topic_ids: list[str] = []
        self.account = MagicMock()
        self.account.hey_host = "app.hey.com"

    def fetch_page(self, _url: str) -> _FakePage:
        return next(self._pages)

    def fetch_topic_payload(self, topic: EmailSummary) -> TopicPayload:
        from yeh.sync import parse_topic_id

        tid = parse_topic_id(topic.topic_url) or topic.topic_url
        self.fetched_topic_ids.append(tid)
        return _make_payload(tid)

    def export_session_state(self):
        return "{}", "csrf", "https://app.hey.com"

    def close(self) -> None:
        pass


def test_refresh_mailbox_fetches_empty_message_topics(tmp_path: Path) -> None:
    """Topics with no stored messages must always be deep-fetched."""
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        hey_email = "user@hey.com"
        topics = [_make_topic(i) for i in range(1, 4)]
        page = _FakePage(topics)
        client = _FakeHeyClient([page])

        result = refresh_mailbox(
            storage=storage,
            client=cast(HeyClient, client),
            hey_email=hey_email,
            mailbox=routes.Mailbox.EVERYTHING,
            workers=1,
        )

        assert result.topics_seen == 3
        assert len(client.fetched_topic_ids) == 3
    finally:
        storage.close()


def test_refresh_mailbox_skips_up_to_date_topics(tmp_path: Path) -> None:
    """Topics that have messages and a current hash should not be re-fetched."""
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        hey_email = "user@hey.com"
        topic = _make_topic(1)
        topic_id = "1"
        summary_hash = topic_summary_hash(topic)

        # pre-seed the DB with a message and mark as synced
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
        storage.upsert_message_text(
            hey_email=hey_email,
            topic_id=topic_id,
            message_id="msg-1",
            source_url="https://app.hey.com/messages/msg-1.text",
            content_text="From: x@x.com\r\nSubject: s\r\n\r\nbody",
        )
        storage.mark_topic_synced(hey_email=hey_email, topic_id=topic_id)

        page = _FakePage([topic])
        client = _FakeHeyClient([page])

        result = refresh_mailbox(
            storage=storage,
            client=cast(HeyClient, client),
            hey_email=hey_email,
            mailbox=routes.Mailbox.EVERYTHING,
            sync_max_age=timedelta(minutes=15),
            workers=1,
        )

        assert result.topics_seen == 1
        # topic is up to date: no deep fetch
        assert client.fetched_topic_ids == []
    finally:
        storage.close()


def test_refresh_mailbox_early_exit_after_consecutive_skips(tmp_path: Path) -> None:
    """After threshold consecutive skipped topics, paging stops early."""
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        hey_email = "user@hey.com"
        n_topics = 15  # more than the default threshold of 10
        topics = [_make_topic(i) for i in range(1, n_topics + 1)]

        # Pre-seed all topics as up-to-date so none need a deep fetch.
        for t in topics:
            from yeh.sync import parse_topic_id

            tid = parse_topic_id(t.topic_url) or t.topic_url
            sh = topic_summary_hash(t)
            storage.upsert_topic(
                hey_email=hey_email,
                topic_id=tid,
                topic_url=t.topic_url,
                sender=t.sender,
                subject=t.subject,
                snippet=t.snippet,
                when_text=t.when,
                summary_hash=sh,
            )
            storage.upsert_message_text(
                hey_email=hey_email,
                topic_id=tid,
                message_id=f"msg-{tid}",
                source_url=f"https://app.hey.com/messages/msg-{tid}.text",
                content_text="From: x@x.com\r\nSubject: s\r\n\r\nbody",
            )
            storage.mark_topic_synced(hey_email=hey_email, topic_id=tid)

        # Single page with all 15 topics.
        page = _FakePage(topics)
        client = _FakeHeyClient([page])

        result = refresh_mailbox(
            storage=storage,
            client=cast(HeyClient, client),
            hey_email=hey_email,
            mailbox=routes.Mailbox.EVERYTHING,
            sync_max_age=timedelta(minutes=15),
            workers=1,
            consecutive_skip_threshold=10,
        )

        # Should stop after exactly 10 consecutive skips — topics_seen == 10
        assert result.topics_seen == 10
        assert client.fetched_topic_ids == []
    finally:
        storage.close()


def test_refresh_mailbox_resets_consecutive_counter_on_deep_fetch(
    tmp_path: Path,
) -> None:
    """A deep-fetched topic resets the consecutive-skip counter."""
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        hey_email = "user@hey.com"
        n = 12
        topics = [_make_topic(i) for i in range(1, n + 1)]

        # Pre-seed all EXCEPT topic 5 as up-to-date.
        for i, t in enumerate(topics, start=1):
            from yeh.sync import parse_topic_id

            tid = parse_topic_id(t.topic_url) or t.topic_url
            sh = topic_summary_hash(t)
            storage.upsert_topic(
                hey_email=hey_email,
                topic_id=tid,
                topic_url=t.topic_url,
                sender=t.sender,
                subject=t.subject,
                snippet=t.snippet,
                when_text=t.when,
                summary_hash=sh,
            )
            if i != 5:
                # topic 5 stays message-less → always deep-fetched
                storage.upsert_message_text(
                    hey_email=hey_email,
                    topic_id=tid,
                    message_id=f"msg-{tid}",
                    source_url=f"https://app.hey.com/messages/msg-{tid}.text",
                    content_text="From: x@x.com\r\nSubject: s\r\n\r\nbody",
                )
                storage.mark_topic_synced(hey_email=hey_email, topic_id=tid)

        page = _FakePage(topics)
        client = _FakeHeyClient([page])

        result = refresh_mailbox(
            storage=storage,
            client=cast(HeyClient, client),
            hey_email=hey_email,
            mailbox=routes.Mailbox.EVERYTHING,
            sync_max_age=timedelta(minutes=15),
            workers=1,
            consecutive_skip_threshold=10,
        )

        # Topics 1-4 skipped (4), topic 5 deep-fetched (counter reset to 0),
        # topics 6-12 skipped: that's 7 skips < threshold of 10 → all 12 seen.
        assert result.topics_seen == 12
        assert "5" in client.fetched_topic_ids
        assert len(client.fetched_topic_ids) == 1
    finally:
        storage.close()
