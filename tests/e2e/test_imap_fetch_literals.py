from io import BytesIO
from typing import cast

from yeh.imap import Envelope, ReadOnlyClient
from yeh.server import _ImapsHandler
from yeh.storage import Storage


class _FakeImapClient:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def search_all(self) -> list[int]:
        return [1]

    def fetch_envelope(self, uid: int) -> Envelope:
        assert uid == 1
        return Envelope(
            uid=1,
            topic_id="topic-1",
            sender="Sender <sender@example.com>",
            subject="Subject",
            date="Sun, 1 Mar 2026 00:42:11 +0100",
            has_attachments=False,
        )

    def fetch_latest_rfc822(self, uid: int) -> str:
        assert uid == 1
        return self._raw


class _FakeStorage:
    def topic_seen_map(self, hey_email: str, topic_ids: list[str]) -> dict[str, bool]:
        assert hey_email == "user@hey.com"
        assert topic_ids == ["topic-1"]
        return {"topic-1": True}


class _Harness:
    def __init__(self) -> None:
        self.wire = BytesIO()

    def _send(self, line: str) -> None:
        self.wire.write((line + "\r\n").encode("utf-8"))

    def _write_bytes(self, data: bytes) -> None:
        self.wire.write(data)


def test_fetch_can_emit_header_and_text_literals() -> None:
    raw = (
        "From: Example <example@example.com>\r\n"
        "Reply-To: reply@example.com\r\n"
        "Subject: Test\r\n"
        "\r\n"
        "hello world"
    )
    handler = _Harness()
    _ImapsHandler._handle_fetch(
        handler,  # type: ignore[arg-type]
        tag="A1",
        sequence_set="1",
        attributes=["UID", "BODY.PEEK[HEADER]", "BODY.PEEK[TEXT]<0.5>"],
        imap_client=cast(ReadOnlyClient, _FakeImapClient(raw)),
        storage=cast(Storage, _FakeStorage()),
        hey_email="user@hey.com",
        emit_tagged_ok=True,
    )
    wire = handler.wire.getvalue().decode("utf-8", errors="ignore")
    assert "BODY[HEADER]" in wire
    assert "BODY[TEXT]<0>" in wire
    assert "From: Example <example@example.com>" in wire
    assert "hello" in wire
    assert "A1 OK FETCH completed" in wire


def test_fetch_supports_body_part_section_literal() -> None:
    raw = "From: Example <example@example.com>\r\nSubject: Part\r\n\r\npart body"
    handler = _Harness()
    _ImapsHandler._handle_fetch(
        handler,  # type: ignore[arg-type]
        tag="A2",
        sequence_set="1",
        attributes=["UID", "BODY.PEEK[1]"],
        imap_client=cast(ReadOnlyClient, _FakeImapClient(raw)),
        storage=cast(Storage, _FakeStorage()),
        hey_email="user@hey.com",
        emit_tagged_ok=True,
    )
    wire = handler.wire.getvalue().decode("utf-8", errors="ignore")
    assert "BODY[1]" in wire
    assert "part body" in wire
    assert "A2 OK FETCH completed" in wire
