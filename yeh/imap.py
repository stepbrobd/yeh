from dataclasses import dataclass
from email import policy
from email.parser import Parser

from yeh import routes
from yeh.storage import Storage, StoredMessage, StoredTopicSummary


@dataclass(frozen=True)
class MailboxInfo:
    mailbox: routes.Mailbox
    count: int


@dataclass(frozen=True)
class Envelope:
    uid: int
    topic_id: str
    sender: str
    subject: str
    date: str
    has_attachments: bool


class ReadOnlyClient:
    def __init__(self, storage: Storage, hey_email: str) -> None:
        self.s = storage
        self.e = hey_email
        self._selected: routes.Mailbox | None = None
        self._rows: list[StoredTopicSummary] = []

    def list_mailboxes(self) -> list[MailboxInfo]:
        db_rows = self.s.list_mailboxes(self.e)
        counts = {r.mailbox: r.topic_count for r in db_rows}
        return [
            MailboxInfo(mailbox=m, count=counts.get(m, 0))
            for m in routes.MAILBOX_LABELS
        ]

    def select(self, mailbox: routes.Mailbox) -> int:
        self._selected = mailbox
        # keep this client read-only and simple; page size can be tuned later
        page = self.s.list_topics_page(self.e, mailbox, limit=5000, offset=0)
        self._rows = page.topics
        return len(self._rows)

    def search_all(self) -> list[int]:
        return [i for i in range(1, len(self._rows) + 1)]

    def fetch_envelope(self, uid: int) -> Envelope:
        row = self._topic(uid)
        # parse From: and Date: from the latest stored message body only.
        latest = self.s.load_latest_message_text(self.e, row.topic_id)
        sender = row.sender
        date = row.when_text
        if latest:
            try:
                msg = Parser(policy=policy.compat32).parsestr(latest, headersonly=True)
                from_header = msg.get("From")
                if from_header and from_header.strip():
                    sender = from_header.strip()
                date_header = msg.get("Date")
                if date_header and date_header.strip():
                    date = date_header.strip()
            except (TypeError, ValueError, LookupError):  # fmt: skip
                pass
        return Envelope(
            uid=uid,
            topic_id=row.topic_id,
            sender=sender,
            subject=row.subject,
            date=date,
            has_attachments=row.has_attachments,
        )

    def fetch_thread(self, uid: int) -> list[StoredMessage]:
        row = self._topic(uid)
        return self.s.load_topic_messages(self.e, row.topic_id)

    def fetch_latest_rfc822(self, uid: int) -> str:
        thread = self.fetch_thread(uid)
        if not thread:
            return ""
        return thread[-1].content_text

    def _topic(self, uid: int) -> StoredTopicSummary:
        if uid < 1 or uid > len(self._rows):
            raise IndexError("uid out of range")
        return self._rows[uid - 1]
