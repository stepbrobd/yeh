from pathlib import Path

from yeh import routes
from yeh.imap import ReadOnlyClient
from yeh.storage import Storage


def test_imap_client_select_search_and_fetch(sample_db: Path, hey_email: str) -> None:
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)
        count = client.select(routes.Mailbox.IMBOX)
        assert count == 1
        assert client.search_all() == [1]
        envelope = client.fetch_envelope(1)
        assert envelope.topic_id == "t1"
        assert "alice@example.com" in envelope.sender.lower()
        raw = client.fetch_latest_rfc822(1)
        assert "Reply-To: Alice Reply <reply@example.com>" in raw
    finally:
        storage.close()


def test_imap_client_can_select_spam_mailbox(sample_db: Path, hey_email: str) -> None:
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)
        count = client.select(routes.Mailbox.SPAM)
        assert count == 1
        envelope = client.fetch_envelope(1)
        assert envelope.topic_id == "t2"
        assert envelope.subject == "Spam Topic"
    finally:
        storage.close()


def test_list_mailboxes_returns_all_known_mailboxes(
    sample_db: Path, hey_email: str
) -> None:
    """list_mailboxes always returns every mailbox from MAILBOX_LABELS."""
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)
        infos = client.list_mailboxes()
        returned = {info.mailbox for info in infos}
        expected = set(routes.MAILBOX_LABELS)
        assert expected == returned
    finally:
        storage.close()


def test_list_mailboxes_inbox_label_maps_to_imbox(
    sample_db: Path, hey_email: str
) -> None:
    """The IMBOX mailbox is exposed with the label 'Inbox'."""
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)
        infos = client.list_mailboxes()
        imbox_info = next(i for i in infos if i.mailbox == routes.Mailbox.IMBOX)
        assert routes.mailbox_label(imbox_info.mailbox) == "Inbox"
    finally:
        storage.close()


def test_list_mailboxes_counts_from_db_where_available(
    sample_db: Path, hey_email: str
) -> None:
    """Mailboxes with synced topics show the DB count; others show 0."""
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)
        infos = client.list_mailboxes()
        by_mailbox = {i.mailbox: i.count for i in infos}
        # sample_db seeds t1→IMBOX+EVERYTHING, t2→SPAM+EVERYTHING
        assert by_mailbox[routes.Mailbox.IMBOX] == 1
        assert by_mailbox[routes.Mailbox.SPAM] == 1
        assert by_mailbox[routes.Mailbox.EVERYTHING] == 2
        # mailboxes with no topics get count 0
        assert by_mailbox[routes.Mailbox.DRAFTS] == 0
        assert by_mailbox[routes.Mailbox.TRASH] == 0
    finally:
        storage.close()
