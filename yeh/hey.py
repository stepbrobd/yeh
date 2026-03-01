from collections.abc import Callable
from pathlib import Path

from yeh import routes
from yeh.imap import ReadOnlyClient
from yeh.importer import ImportStats, import_mbox_file
from yeh.mailbox import HeyClient, InboxPage
from yeh.send import (
    load_compose_defaults,
    load_new_mail_compose_defaults,
    send_new,
    send_reply,
)
from yeh.smtp import MailAction, NewMail, ReplyMail
from yeh.storage import (
    MailboxSummary,
    Storage,
    StoredMessage,
    StoredTopicPage,
)
from yeh.sync import (
    MailboxRefreshResult,
    refresh_mailbox,
    sync_page,
)


class Client:
    def __init__(self, storage: Storage, hey_email: str, web: HeyClient | None) -> None:
        self.s = storage
        self.e = hey_email
        self.w = web

    def import_mbox(self, path: Path, mailbox: routes.Mailbox) -> ImportStats:
        return import_mbox_file(
            storage=self.s,
            hey_email=self.e,
            mbox_path=path,
            mailbox=mailbox,
        )

    def refresh(
        self,
        mailbox: routes.Mailbox,
        max_pages: int | None = None,
        *,
        progress: Callable[[str], None] | None = None,
        workers: int = 4,
    ) -> MailboxRefreshResult:
        w = self._web()
        return refresh_mailbox(
            storage=self.s,
            client=w,
            hey_email=self.e,
            mailbox=mailbox,
            max_pages=max_pages,
            progress=progress,
            workers=workers,
        )

    def refresh_all(
        self,
        max_pages: int | None = None,
        *,
        progress: Callable[[str], None] | None = None,
        workers: int = 4,
    ) -> list[MailboxRefreshResult]:
        """Sync every individual mailbox so topics are tagged with their real mailbox key.

        Syncing only ``Mailbox.EVERYTHING`` tags every topic as ``"everything"``,
        which makes per-mailbox IMAP SELECT return zero results.  This method
        iterates each concrete mailbox (all entries in ``MAILBOX_PATHS`` except
        ``EVERYTHING``) so the correct ``mailbox_key`` is stored per topic.
        """
        w = self._web()
        results: list[MailboxRefreshResult] = []
        for mailbox in routes.MAILBOX_PATHS:
            if mailbox is routes.Mailbox.EVERYTHING:
                continue
            result = refresh_mailbox(
                storage=self.s,
                client=w,
                hey_email=self.e,
                mailbox=mailbox,
                max_pages=max_pages,
                progress=progress,
                workers=workers,
            )
            results.append(result)
        return results

    def sync_page(
        self, page: InboxPage, mailbox: routes.Mailbox = routes.Mailbox.IMBOX
    ) -> tuple[int, int, int]:
        w = self._web()
        return sync_page(
            storage=self.s,
            client=w,
            hey_email=self.e,
            page=page,
            mailbox=mailbox,
        )

    def mailboxes(self, host: str) -> list[MailboxSummary]:
        existing = {x.mailbox: x for x in self.s.list_mailboxes(self.e)}
        out: list[MailboxSummary] = []
        for m, p in routes.MAILBOX_PATHS.items():
            x = existing.get(m)
            out.append(
                MailboxSummary(
                    mailbox=m,
                    mailbox_url=x.mailbox_url
                    if x is not None
                    else routes.https_url(host, p),
                    topic_count=x.topic_count if x is not None else 0,
                )
            )
        return out

    def topics(
        self, mailbox: routes.Mailbox | None, limit: int, offset: int
    ) -> StoredTopicPage:
        return self.s.list_topics_page(self.e, mailbox, limit, offset)

    def thread(self, topic_id: str) -> list[StoredMessage]:
        return self.s.load_topic_messages(self.e, topic_id)

    def imap(self) -> ReadOnlyClient:
        return ReadOnlyClient(storage=self.s, hey_email=self.e)

    def smtp_submit(self, action: MailAction):
        web = self._web()
        if isinstance(action, NewMail):
            # HEY does not expose a standalone /messages/new page; fetch the
            # imbox to get a fresh CSRF token and extract acting_sender_id from
            # a topic page.
            defaults = load_new_mail_compose_defaults(web, self.e)
            return send_new(
                client=web,
                defaults=defaults,
                to=action.to,
                cc=action.cc,
                bcc=action.bcc,
                subject=action.subject,
                html=action.html,
            )
        if isinstance(action, ReplyMail):
            # Fetch the reply-compose page for this specific entry so that we
            # get a fresh CSRF token AND the correct acting_sender_id /
            # acting_sender_email values.  Using /messages/new would return a
            # compose form that lacks the acting_sender fields for replies.
            defaults = load_compose_defaults(
                web,
                compose_url=f"/entries/{action.entry_id}/replies/new",
            )
            return send_reply(
                client=web,
                entry_id=action.entry_id,
                defaults=defaults,
                to=action.to,
                cc=action.cc,
                bcc=action.bcc,
                subject=action.subject,
                html=action.html,
            )
        raise TypeError("unsupported smtp action")

    def _web(self) -> HeyClient:
        w = self.w
        if w is None:
            raise RuntimeError("operation requires live hey web client")
        return w
