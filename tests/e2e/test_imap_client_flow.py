from pathlib import Path

from yeh import routes
from yeh.imap import ReadOnlyClient
from yeh.server import _extract_raw_headers
from yeh.storage import Storage


def test_imap_flow_reads_inbox_and_spam_from_temp_db(
    sample_db: Path, hey_email: str
) -> None:
    storage = Storage(sample_db)
    try:
        client = ReadOnlyClient(storage=storage, hey_email=hey_email)

        inbox_count = client.select(routes.Mailbox.IMBOX)
        assert inbox_count == 1
        inbox_raw = client.fetch_latest_rfc822(1)
        inbox_headers = _extract_raw_headers(inbox_raw.encode("utf-8"))
        assert inbox_headers is not None
        headers_text = inbox_headers.decode("utf-8", errors="ignore")
        assert "From: Alice <alice@example.com>" in headers_text
        assert "Reply-To: Alice Reply <reply@example.com>" in headers_text

        spam_count = client.select(routes.Mailbox.SPAM)
        assert spam_count == 1
        spam_raw = client.fetch_latest_rfc822(1)
        assert "Subject: Spam Topic" in spam_raw
    finally:
        storage.close()
