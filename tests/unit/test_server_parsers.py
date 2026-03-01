from yeh import routes
from yeh.server import (
    _expand_uid_set,
    _imap_date,
    _is_empty_mailbox_arg,
    _parse_fetch_args,
    _parse_select_mailbox,
    _parse_status_args,
    _parse_store_command,
)


def test_parse_select_mailbox_understands_labels() -> None:
    assert _parse_select_mailbox('"Inbox"') == routes.Mailbox.IMBOX
    assert _parse_select_mailbox('"Previously Seen"') == routes.Mailbox.PREVIOUSLY_SEEN
    assert _parse_select_mailbox("spam") == routes.Mailbox.SPAM


def test_uid_set_expansion_handles_ranges_and_star() -> None:
    assert _expand_uid_set("1:3,2,5", max_uid=9) == [1, 2, 3, 5]
    assert _expand_uid_set("8:*", max_uid=10) == [8, 9, 10]
    assert _expand_uid_set("*", max_uid=7) == [7]


def test_parse_fetch_status_and_store_commands() -> None:
    seq, attrs = _parse_fetch_args("1:2 (UID FLAGS BODY.PEEK[HEADER])")
    assert seq == "1:2"
    assert attrs == ["UID", "FLAGS", "BODY.PEEK[HEADER]"]

    mailbox, items = _parse_status_args('"Everything" (MESSAGES UIDNEXT UNSEEN)')
    assert mailbox == routes.Mailbox.EVERYTHING
    assert items == ["MESSAGES", "UIDNEXT", "UNSEEN"]

    sequence_set, mode, flags = _parse_store_command("1:3 +FLAGS.SILENT (\\Seen)")
    assert sequence_set == "1:3"
    assert mode == "+FLAGS.SILENT"
    assert "\\SEEN" in flags


def test_empty_mailbox_and_date_normalization_helpers() -> None:
    assert _is_empty_mailbox_arg('""')
    assert not _is_empty_mailbox_arg("Inbox")
    assert _imap_date("Sun, 1 Mar 2026 00:42:11 +0100") == "01-Mar-2026 00:42:11 +0100"
