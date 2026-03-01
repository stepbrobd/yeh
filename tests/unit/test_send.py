from yeh.send import _extract_message_id


def test_extract_message_id_supports_non_numeric_ids() -> None:
    value = "https://app.hey.com/messages/abc123-def456?x=1"
    assert _extract_message_id(value) == "abc123-def456"


def test_extract_message_id_returns_none_for_missing_path() -> None:
    assert _extract_message_id("https://app.hey.com/messages") is None


def test_extract_message_id_supports_escaped_slashes() -> None:
    value = '{"location":"https:\\/\\/app.hey.com\\/messages\\/1234567890"}'
    assert _extract_message_id(value) == "1234567890"
