from pathlib import Path

from yeh import routes
from yeh.storage import Storage


def test_storage_session_topic_and_mailbox_queries(
    sample_db: Path, hey_email: str
) -> None:
    storage = Storage(sample_db)
    try:
        session = storage.load_session(hey_email)
        assert session is not None
        assert session.csrf_token == "csrf"

        mailboxes = storage.list_mailboxes(hey_email)
        keys = {m.mailbox for m in mailboxes}
        assert routes.Mailbox.IMBOX in keys
        assert routes.Mailbox.SPAM in keys
        assert routes.Mailbox.EVERYTHING in keys

        imbox_page = storage.list_topics_page(
            hey_email=hey_email,
            mailbox=routes.Mailbox.IMBOX,
            limit=50,
            offset=0,
        )
        assert imbox_page.total_count == 1
        assert imbox_page.topics[0].topic_id == "t1"

        everything_page = storage.list_topics_page(
            hey_email=hey_email,
            mailbox=routes.Mailbox.EVERYTHING,
            limit=50,
            offset=0,
        )
        assert everything_page.total_count == 2

        thread = storage.load_topic_messages(hey_email, "t1")
        assert len(thread) == 1
        assert "From: Alice" in thread[0].content_text
    finally:
        storage.close()


def test_storage_seen_flags_and_unseen_counts(sample_db: Path, hey_email: str) -> None:
    storage = Storage(sample_db)
    try:
        assert storage.count_unseen_topics(hey_email, routes.Mailbox.EVERYTHING) == 2
        storage.set_topic_seen(hey_email, "t1", True)
        seen = storage.topic_seen_map(hey_email, ["t1", "t2"])
        assert seen["t1"] is True
        assert seen.get("t2", False) is False
        assert storage.count_unseen_topics(hey_email, routes.Mailbox.EVERYTHING) == 1
    finally:
        storage.close()
