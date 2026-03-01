import hashlib
import mailbox as mboxlib
import re
from dataclasses import dataclass
from datetime import UTC
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import cast

from yeh import routes
from yeh.storage import Storage


@dataclass(frozen=True)
class ImportStats:
    total_messages: int
    imported_messages: int
    updated_messages: int
    topics: int
    mailbox: routes.Mailbox


@dataclass(frozen=True)
class _ParsedMessage:
    message_id: str
    parent_id: str | None
    subject: str
    sender: str
    when_text: str
    snippet: str
    content_text: str
    source_url: str


def import_mbox_file(
    storage: Storage,
    hey_email: str,
    mbox_path: Path,
    mailbox: routes.Mailbox,
) -> ImportStats:
    mbox_path = mbox_path.expanduser().resolve()
    mbox_obj = mboxlib.mbox(str(mbox_path), create=False)
    parsed: list[_ParsedMessage] = []
    try:
        for index, msg in enumerate(mbox_obj):
            parsed.append(_parse_message(msg, mbox_path=mbox_path, index=index))
    finally:
        mbox_obj.close()

    topic_ids = _build_topic_ids(parsed)

    imported_messages = 0
    updated_messages = 0
    topics_seen: set[str] = set()
    topic_summaries: dict[str, _ParsedMessage] = {}
    mailbox_url = routes.mailbox_url(routes.HOST, mailbox)

    for parsed_message, topic_id in zip(parsed, topic_ids, strict=True):
        if topic_id not in topic_summaries:
            topic_summaries[topic_id] = parsed_message
        imported_messages += 1
        changed = storage.upsert_message_text(
            hey_email=hey_email,
            topic_id=topic_id,
            message_id=parsed_message.message_id,
            source_url=parsed_message.source_url,
            content_text=parsed_message.content_text,
        )
        if changed:
            updated_messages += 1
        topics_seen.add(topic_id)

    for topic_id in topics_seen:
        summary = topic_summaries[topic_id]
        summary_hash = hashlib.sha256(
            f"{summary.subject}\n{summary.sender}\n{summary.snippet}\n{summary.when_text}\n{summary.source_url}".encode()
        ).hexdigest()
        storage.upsert_topic(
            hey_email=hey_email,
            topic_id=topic_id,
            topic_url=f"mbox://{mbox_path.name}#{topic_id}",
            sender=summary.sender,
            subject=summary.subject,
            snippet=summary.snippet,
            when_text=summary.when_text,
            summary_hash=summary_hash,
        )
        storage.mark_topic_synced(hey_email=hey_email, topic_id=topic_id)
        storage.assign_topic_mailbox(
            hey_email=hey_email,
            topic_id=topic_id,
            mailbox=mailbox,
            mailbox_url=mailbox_url,
        )

    return ImportStats(
        total_messages=len(parsed),
        imported_messages=imported_messages,
        updated_messages=updated_messages,
        topics=len(topics_seen),
        mailbox=mailbox,
    )


def _parse_message(msg: Message, mbox_path: Path, index: int) -> _ParsedMessage:
    message_id = _normalize_message_id(msg.get("Message-ID"))
    if not message_id:
        message_id = _synthetic_message_id(msg)

    references = _extract_message_ids(msg.get("References", ""))
    in_reply_to = _extract_message_ids(msg.get("In-Reply-To", ""))
    parent_id = (
        references[-1] if references else (in_reply_to[-1] if in_reply_to else None)
    )

    subject = _decode_header_value(msg.get("Subject")) or "(no subject)"
    sender = _decode_sender(msg.get("From")) or "(unknown)"
    when_text = _normalize_date(msg.get("Date"))
    snippet = _extract_snippet(msg)
    content_text = msg.as_string()
    source_url = f"mbox://{mbox_path.name}#{index + 1}"

    return _ParsedMessage(
        message_id=message_id,
        parent_id=parent_id,
        subject=subject,
        sender=sender,
        when_text=when_text,
        snippet=snippet,
        content_text=content_text,
        source_url=source_url,
    )


def _build_topic_ids(messages: list[_ParsedMessage]) -> list[str]:
    id_to_index = {message.message_id: idx for idx, message in enumerate(messages)}
    cache: dict[str, str] = {}

    def root_for(message: _ParsedMessage) -> str:
        cached = cache.get(message.message_id)
        if cached is not None:
            return cached

        seen: set[str] = set()
        current = message
        while current.parent_id and current.parent_id in id_to_index:
            if current.message_id in seen:
                break
            seen.add(current.message_id)
            current = messages[id_to_index[current.parent_id]]

        root_key = current.message_id
        cache[message.message_id] = root_key
        return root_key

    topic_ids: list[str] = []
    for message in messages:
        root_key = root_for(message)
        digest = hashlib.sha1(root_key.encode("utf-8")).hexdigest()[:16]
        topic_ids.append(f"mbox-{digest}")
    return topic_ids


def _normalize_message_id(value: str | None) -> str:
    if value is None:
        return ""
    match = re.search(r"<([^>]+)>", value)
    if match is not None:
        return match.group(1).strip().lower()
    return value.strip().strip("<>").lower()


def _extract_message_ids(value: str) -> list[str]:
    found = re.findall(r"<([^>]+)>", value)
    if found:
        return [item.strip().lower() for item in found if item.strip()]
    normalized = _normalize_message_id(value)
    return [normalized] if normalized else []


def _normalize_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError:
        return value
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _extract_snippet(msg: Message) -> str:
    text = _first_text_part(msg)
    if not text:
        return ""
    collapsed = " ".join(text.split())
    return collapsed[:240]


def _first_text_part(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            if part.get("Content-Disposition", "").lower().startswith("attachment"):
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, (bytes, bytearray)):
                continue
            payload_bytes = cast(bytes, bytes(payload))
            charset = part.get_content_charset() or "utf-8"
            try:
                return payload_bytes.decode(charset, errors="replace")
            except LookupError:
                return payload_bytes.decode("utf-8", errors="replace")
        return ""

    payload = msg.get_payload(decode=True)
    if payload is None:
        raw = msg.get_payload()
        return raw if isinstance(raw, str) else ""
    if not isinstance(payload, (bytes, bytearray)):
        return ""
    payload_bytes = cast(bytes, bytes(payload))
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload_bytes.decode(charset, errors="replace")
    except LookupError:
        return payload_bytes.decode("utf-8", errors="replace")


def _synthetic_message_id(msg: Message) -> str:
    raw = msg.as_bytes()
    digest = hashlib.sha1(raw).hexdigest()[:24]
    return f"mbox-msg-{digest}"


def _decode_header_value(value: str | None) -> str:
    if value is None:
        return ""
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            enc = encoding or "utf-8"
            try:
                parts.append(chunk.decode(enc, errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _decode_sender(value: str | None) -> str:
    decoded = _decode_header_value(value)
    if not decoded:
        return ""
    name, address = parseaddr(decoded)
    clean_name = name.strip()
    clean_address = address.strip().lower()
    if clean_name and clean_address:
        return f"{clean_name} <{clean_address}>"
    if clean_address:
        return clean_address
    return clean_name
