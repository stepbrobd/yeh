from pathlib import Path

import pytest

from yeh import routes
from yeh.storage import Storage


@pytest.fixture
def hey_email() -> str:
    return "user@hey.com"


@pytest.fixture
def sample_db(tmp_path: Path, hey_email: str) -> Path:
    db_path = tmp_path / "sample.sqlite3"
    storage = Storage(db_path)
    try:
        storage.save_session(
            hey_email=hey_email,
            cookie_jar_json='{"a": 1}',
            csrf_token="csrf",
            final_url="https://app.hey.com/imbox",
        )

        storage.upsert_topic(
            hey_email=hey_email,
            topic_id="t1",
            topic_url="https://app.hey.com/topics/t1",
            sender="Alice <alice@example.com>",
            subject="Topic One",
            snippet="snippet one",
            when_text="Sun, 1 Mar 2026 00:00:00 +0100",
            summary_hash="h1",
        )
        storage.assign_topic_mailbox(
            hey_email,
            "t1",
            routes.Mailbox.IMBOX,
            routes.mailbox_url(routes.HOST, routes.Mailbox.IMBOX),
        )
        storage.assign_topic_mailbox(
            hey_email,
            "t1",
            routes.Mailbox.EVERYTHING,
            routes.mailbox_url(routes.HOST, routes.Mailbox.EVERYTHING),
        )
        storage.upsert_message_text(
            hey_email,
            "t1",
            "m1",
            routes.message_text_url(routes.HOST, "m1"),
            "From: Alice <alice@example.com>\r\n"
            "Reply-To: Alice Reply <reply@example.com>\r\n"
            "Subject: Topic One\r\n"
            "Date: Sun, 1 Mar 2026 00:00:00 +0100\r\n"
            "\r\n"
            "hello from topic one",
        )

        storage.upsert_topic(
            hey_email=hey_email,
            topic_id="t2",
            topic_url="https://app.hey.com/topics/t2",
            sender="Spam Sender <spam@example.com>",
            subject="Spam Topic",
            snippet="spammy",
            when_text="Sun, 1 Mar 2026 01:00:00 +0100",
            summary_hash="h2",
        )
        storage.assign_topic_mailbox(
            hey_email,
            "t2",
            routes.Mailbox.SPAM,
            routes.mailbox_url(routes.HOST, routes.Mailbox.SPAM),
        )
        storage.assign_topic_mailbox(
            hey_email,
            "t2",
            routes.Mailbox.EVERYTHING,
            routes.mailbox_url(routes.HOST, routes.Mailbox.EVERYTHING),
        )
        storage.upsert_message_text(
            hey_email,
            "t2",
            "m2",
            routes.message_text_url(routes.HOST, "m2"),
            "From: Spam Sender <spam@example.com>\r\n"
            "Subject: Spam Topic\r\n"
            "Date: Sun, 1 Mar 2026 01:00:00 +0100\r\n"
            "\r\n"
            "buy now",
        )
    finally:
        storage.close()
    return db_path
