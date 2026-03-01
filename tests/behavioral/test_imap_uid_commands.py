from io import BytesIO
from typing import cast

from yeh.imap import Envelope, ReadOnlyClient
from yeh.server import _ImapsHandler
from yeh.storage import Storage


class _Client:
    def __init__(self):
        self._raw = (
            "From: One <one@example.com>\r\n"
            "Subject: Hi\r\n"
            "Date: Sun, 1 Mar 2026 00:00:00 +0100\r\n"
            "\r\n"
            "body"
        )

    def search_all(self) -> list[int]:
        return [1]

    def fetch_envelope(self, uid: int) -> Envelope:
        assert uid == 1
        return Envelope(
            uid=uid,
            topic_id="t1",
            sender="One <one@example.com>",
            subject="Hi",
            date="Sun, 1 Mar 2026 00:00:00 +0100",
            has_attachments=False,
        )

    def fetch_latest_rfc822(self, uid: int) -> str:
        assert uid == 1
        return self._raw


class _Storage:
    def __init__(self):
        self.seen: dict[str, bool] = {}

    def topic_seen_map(self, _email: str, _topic_ids: list[str]) -> dict[str, bool]:
        return {"t1": self.seen.get("t1", False)}

    def set_topic_seen(self, _email: str, topic_id: str, seen: bool) -> None:
        self.seen[topic_id] = seen


class _Harness:
    def __init__(self):
        self.w = BytesIO()

    def _send(self, line: str) -> None:
        self.w.write((line + "\r\n").encode("utf-8"))

    def _write_bytes(self, data: bytes) -> None:
        self.w.write(data)

    def _handle_fetch(self, *args, **kwargs):
        return _ImapsHandler._handle_fetch(cast(_ImapsHandler, self), *args, **kwargs)

    def _apply_seen_store(self, **kwargs):
        return _ImapsHandler._apply_seen_store(cast(_ImapsHandler, self), **kwargs)


def test_uid_search_fetch_and_store_flow() -> None:
    h = _Harness()
    c = _Client()
    s = _Storage()

    _ImapsHandler._handle_uid(
        cast(_ImapsHandler, h),
        "A1",
        "SEARCH ALL",
        cast(ReadOnlyClient, c),
        storage=cast(Storage, s),
        hey_email="user@hey.com",
    )
    _ImapsHandler._handle_uid(
        cast(_ImapsHandler, h),
        "A2",
        "STORE 1 +FLAGS.SILENT (\\Seen)",
        cast(ReadOnlyClient, c),
        storage=cast(Storage, s),
        hey_email="user@hey.com",
    )
    _ImapsHandler._handle_uid(
        cast(_ImapsHandler, h),
        "A3",
        "FETCH 1 (UID FLAGS BODY.PEEK[HEADER])",
        cast(ReadOnlyClient, c),
        storage=cast(Storage, s),
        hey_email="user@hey.com",
    )

    wire = h.w.getvalue().decode("utf-8", errors="ignore")
    assert "* SEARCH 1" in wire
    assert "A1 OK UID SEARCH completed" in wire
    assert "A2 OK UID STORE completed" in wire
    assert "FLAGS (\\Seen)" in wire
    assert "From: One <one@example.com>" in wire
    assert "A3 OK UID FETCH completed" in wire
