from yeh.server import (
    _extract_raw_body,
    _extract_raw_headers,
    _format_status,
    _imap_date,
    _is_empty_mailbox_arg,
    _rfc2822_date,
)


def test_extract_raw_headers_preserves_from_header() -> None:
    raw = (
        b"From: Example Sender <sender@example.com>\r\n"
        b"Reply-To: reply@example.com\r\n"
        b"Subject: hi\r\n"
        b"\r\n"
        b"body"
    )
    headers = _extract_raw_headers(raw)
    assert headers is not None
    text = headers.decode("utf-8")
    assert "From: Example Sender <sender@example.com>" in text
    assert "Reply-To: reply@example.com" in text
    assert text.endswith("\r\n\r\n")


def test_extract_raw_headers_fallback_parser_mode() -> None:
    raw = b"From: Name <name@example.com>\nSubject: X\nBody without separator"
    headers = _extract_raw_headers(raw)
    assert headers is not None
    text = headers.decode("utf-8", errors="ignore")
    assert "From: Name <name@example.com>" in text


def test_empty_mailbox_arg_matches_select_empty_string() -> None:
    assert _is_empty_mailbox_arg('""')
    assert not _is_empty_mailbox_arg("INBOX")


def test_imap_date_normalizes_rfc2822() -> None:
    out = _imap_date("Sun, 1 Mar 2026 00:42:11 +0100")
    assert out == "01-Mar-2026 00:42:11 +0100"


def test_extract_raw_body_splits_headers_from_text() -> None:
    raw = b"From: x@example.com\r\nSubject: hi\r\n\r\nhello"
    assert _extract_raw_body(raw) == b"hello"


def test_status_formatter_uses_requested_fields() -> None:
    out = _format_status("Inbox", 10, 3, ["MESSAGES", "UIDNEXT", "UNSEEN"])
    assert out == '* STATUS "Inbox" (MESSAGES 10 UIDNEXT 11 UNSEEN 3)'


def test_rfc2822_date_produces_proper_header_format() -> None:
    # Input is RFC 2822 with named weekday — should round-trip cleanly
    out = _rfc2822_date("Sun, 1 Mar 2026 00:42:11 +0100")
    assert out == "Sun, 01 Mar 2026 00:42:11 +0100"


def test_rfc2822_date_fallback_on_empty_input() -> None:
    assert _rfc2822_date("") == "Thu, 01 Jan 1970 00:00:00 +0000"


def test_rfc2822_date_differs_from_imap_date_format() -> None:
    value = "Sun, 1 Mar 2026 12:00:00 +0000"
    rfc = _rfc2822_date(value)
    imap = _imap_date(value)
    # RFC 2822 has weekday prefix; IMAP INTERNALDATE does not
    assert rfc.startswith("Sun,")
    assert not imap.startswith("Sun")
