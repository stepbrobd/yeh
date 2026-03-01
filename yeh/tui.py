from collections.abc import Callable
from email import policy
from email.parser import Parser
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from yeh import routes
from yeh.mailbox import (
    AuthenticationRequiredError,
    EmailSummary,
    HeyClient,
    InboxPage,
)
from yeh.storage import MailboxSummary, SessionRecord, StoredMessage, StoredTopicPage


class EmailListApp(App[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "next_page", "Next page"),
    ]

    def __init__(
        self,
        inbox_client: HeyClient,
        initial_page: InboxPage,
        reauth_callback: Callable[[], SessionRecord],
        persist_callback: Callable[[str, str | None, str], None],
        sync_callback: Callable[[InboxPage], str] | None = None,
        initial_sync_status: str | None = None,
    ) -> None:
        super().__init__()
        self.inbox_client = inbox_client
        self.initial_page = initial_page
        self.next_page_url: str | None = initial_page.next_page_url
        self.reauth_callback = reauth_callback
        self.persist_callback = persist_callback
        self.sync_callback = sync_callback
        self.initial_sync_status = initial_sync_status

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Loading...", id="status")
        table = DataTable(id="emails")
        table.cursor_type = "row"
        yield table
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#emails", DataTable)
        table.add_columns("Sender", "Subject", "Snippet", "When", "Topic")
        self._render_rows(self.initial_page.emails)
        status = f"Loaded {len(self.initial_page.emails)} threads"
        if self.initial_sync_status:
            status += f" | {self.initial_sync_status}"
        if self.next_page_url:
            status += " | press n for next page"
        self.query_one("#status", Static).update(status)

    def action_refresh(self) -> None:
        page, sync_status = self._fetch_with_auto_reauth(self.inbox_client.fetch_imbox)
        self._render_rows(page.emails)
        self.next_page_url = page.next_page_url
        status = f"Loaded {len(page.emails)} threads"
        if sync_status:
            status += f" | {sync_status}"
        if self.next_page_url:
            status += " | press n for next page"
        self.query_one("#status", Static).update(status)

    def action_next_page(self) -> None:
        if not self.next_page_url:
            self.query_one("#status", Static).update("No next page")
            return
        page, sync_status = self._fetch_with_auto_reauth(
            lambda: self.inbox_client.fetch_page(self.next_page_url or "")
        )
        self._render_rows(page.emails)
        self.next_page_url = page.next_page_url
        status = f"Loaded {len(page.emails)} threads"
        if sync_status:
            status += f" | {sync_status}"
        if self.next_page_url:
            status += " | press n for next page"
        self.query_one("#status", Static).update(status)

    def _render_rows(self, emails: list[EmailSummary]) -> None:
        table = self.query_one("#emails", DataTable)
        table.clear(columns=False)
        for email in emails:
            table.add_row(
                email.sender,
                email.subject,
                email.snippet,
                email.when,
                email.topic_url,
            )

    def _fetch_with_auto_reauth(
        self, fetcher: Callable[[], InboxPage]
    ) -> tuple[InboxPage, str | None]:
        try:
            page = fetcher()
            sync_status = self.sync_callback(page) if self.sync_callback else None
        except AuthenticationRequiredError:
            session: SessionRecord = self.reauth_callback()
            self.inbox_client.replace_session(session)
            page = fetcher()
            sync_status = self.sync_callback(page) if self.sync_callback else None

        cookie_jar_json, csrf_token, final_url = (
            self.inbox_client.export_session_state()
        )
        self.persist_callback(cookie_jar_json, csrf_token, final_url)
        return page, sync_status


class EmailDatabaseApp(App[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("]", "next_mailbox", "Next mailbox"),
        Binding("[", "prev_mailbox", "Prev mailbox"),
        Binding("a", "all_mailboxes", "All mailboxes"),
        Binding("n", "next_page", "Next page"),
        Binding("p", "prev_page", "Prev page"),
    ]

    def __init__(
        self,
        load_mailboxes: Callable[[], list[MailboxSummary]],
        load_topics: Callable[[routes.Mailbox | None, int, int], StoredTopicPage],
        load_thread: Callable[[str], list[StoredMessage]],
    ) -> None:
        super().__init__()
        self.load_mailboxes = load_mailboxes
        self.load_topics = load_topics
        self.load_thread = load_thread
        self.mailboxes: list[MailboxSummary] = []
        self.mailbox_index: int | None = None
        self.page_index: int = 0
        self.page_size: int = 100
        self.rows: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Loading...", id="status")
        with Vertical():
            table = DataTable(id="topics")
            table.cursor_type = "row"
            yield table
            yield Static("", id="thread")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#topics", DataTable)
        table.add_columns(
            "Sender", "Subject", "Snippet", "When", "Msgs", "Att", "Inboxes"
        )
        self._refresh_data()

    def action_refresh(self) -> None:
        self._refresh_data()

    def action_next_mailbox(self) -> None:
        if not self.mailboxes:
            self._set_status("No mailbox memberships found")
            return
        if self.mailbox_index is None:
            self.mailbox_index = 0
        else:
            self.mailbox_index = (self.mailbox_index + 1) % len(self.mailboxes)
        self.page_index = 0
        self._refresh_topics()

    def action_prev_mailbox(self) -> None:
        if not self.mailboxes:
            self._set_status("No mailbox memberships found")
            return
        if self.mailbox_index is None:
            self.mailbox_index = len(self.mailboxes) - 1
        else:
            self.mailbox_index = (self.mailbox_index - 1) % len(self.mailboxes)
        self.page_index = 0
        self._refresh_topics()

    def action_all_mailboxes(self) -> None:
        self.mailbox_index = None
        self.page_index = 0
        self._refresh_topics()

    def action_next_page(self) -> None:
        self.page_index += 1
        self._refresh_topics()

    def action_prev_page(self) -> None:
        if self.page_index == 0:
            self._set_status("Already at first page")
            return
        self.page_index -= 1
        self._refresh_topics()

    def _refresh_data(self) -> None:
        self.mailboxes = self.load_mailboxes()
        if self.mailbox_index is not None and self.mailbox_index >= len(self.mailboxes):
            self.mailbox_index = None
        self._refresh_topics()

    def _refresh_topics(self) -> None:
        mailbox_key = self._selected_mailbox_key()
        page = self.load_topics(
            mailbox_key, self.page_size, self.page_index * self.page_size
        )
        if self.page_index > 0 and not page.topics:
            self.page_index -= 1
            page = self.load_topics(
                mailbox_key, self.page_size, self.page_index * self.page_size
            )

        table = self.query_one("#topics", DataTable)
        table.clear(columns=False)
        self.rows = []
        for topic in page.topics:
            self.rows.append(topic.topic_id)
            table.add_row(
                topic.sender,
                topic.subject,
                topic.snippet,
                topic.when_text,
                str(topic.message_count),
                "yes" if topic.has_attachments else "no",
                ", ".join(x.value for x in topic.mailboxes),
            )

        if self.rows:
            self._render_thread(self.rows[0])
        else:
            self.query_one("#thread", Static).update("(no thread)")

        mailbox_label = mailbox_key.value if mailbox_key is not None else "all"
        total_pages = (page.total_count + self.page_size - 1) // self.page_size
        current_page = self.page_index + 1 if total_pages > 0 else 0
        self._set_status(
            f"Loaded {len(page.topics)} topics ({page.total_count} total) | mailbox={mailbox_label} | page {current_page}/{total_pages} | [ ] mailbox | n/p page | a all"
        )

    def _selected_mailbox_key(self) -> routes.Mailbox | None:
        if self.mailbox_index is None:
            return None
        if not self.mailboxes:
            return None
        return self.mailboxes[self.mailbox_index].mailbox

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if idx < 0 or idx >= len(self.rows):
            return
        self._render_thread(self.rows[idx])

    def _render_thread(self, topic_id: str) -> None:
        messages = self.load_thread(topic_id)
        if not messages:
            self.query_one("#thread", Static).update("(empty thread)")
            return

        lines: list[str] = []
        for i, msg in enumerate(messages, start=1):
            marker = "yes" if msg.has_attachment else "no"
            lines.append(f"--- message {i} id={msg.message_id} attachments={marker}")
            lines.append(_render_full_text(msg.content_text))
            lines.append("")
        self.query_one("#thread", Static).update("\n".join(lines))


def _render_full_text(raw: str) -> str:
    if "\n" not in raw and "\r" not in raw:
        return raw
    try:
        msg = Parser(policy=policy.default).parsestr(raw)
    except (TypeError, ValueError, LookupError):  # fmt: skip
        return raw

    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            disp = str(part.get("Content-Disposition", "")).lower()
            if disp.startswith("attachment"):
                continue
            try:
                text = part.get_content()
            except (TypeError, ValueError, LookupError):  # fmt: skip
                text = ""
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    else:
        try:
            text = msg.get_content()
        except (TypeError, ValueError, LookupError):  # fmt: skip
            text = raw
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())

    if chunks:
        return "\n\n".join(chunks)
    return raw
