import json
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from yeh import routes
from yeh.config import ResolvedAccount
from yeh.storage import SessionRecord

_ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_ACCEPT_TEXT = "text/plain,*/*;q=0.8"


def _data_to_multipart(
    data: Mapping[str, str | list[str]],
) -> list[tuple[str, tuple[None, str, None]]]:
    """Convert a flat form-data mapping to an httpx multipart ``files=`` list.

    httpx accepts ``files=[(field, (filename, value, content_type)), ...]``.
    Using ``(None, value, None)`` sends a plain text part without a filename,
    which is what browsers send for non-file ``<input>`` fields.
    """
    parts: list[tuple[str, tuple[None, str, None]]] = []
    for key, value in data.items():
        if isinstance(value, list):
            parts.extend((key, (None, item, None)) for item in value)
        else:
            parts.append((key, (None, value, None)))
    return parts


@dataclass(frozen=True)
class EmailSummary:
    sender: str
    subject: str
    snippet: str
    when: str
    topic_url: str


@dataclass(frozen=True)
class InboxPage:
    emails: list[EmailSummary]
    next_page_url: str | None


@dataclass(frozen=True)
class MessagePayload:
    message_id: str
    source_url: str
    content_text: str


@dataclass(frozen=True)
class TopicPayload:
    topic_id: str
    topic_url: str
    sender: str
    subject: str
    snippet: str
    when: str
    messages: list[MessagePayload]


class AuthenticationRequiredError(Exception):
    pass


class HeyClient:
    def __init__(self, account: ResolvedAccount, session: SessionRecord) -> None:
        self.account = account
        self.base_url = routes.https_url(account.hey_host)
        self.last_url: str | None = None
        self.last_html: str = ""
        self.last_csrf_token: str | None = None
        _ensure_allowed(self.base_url, account.hey_host)
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            },
            follow_redirects=True,
            timeout=20.0,
        )
        self.replace_session(session)

    def close(self) -> None:
        self.client.close()

    def fetch_imbox(self) -> InboxPage:
        return self.fetch_page(routes.IMBOX)

    def fetch_sender_contact_id(self, hey_email: str) -> str | None:
        """Return the HEY contact ID for *hey_email* by fetching the imbox.

        The method fetches the imbox page (refreshing ``last_csrf_token`` as a
        side effect), then fetches the first topic page found there and searches
        for ``<a href="/contacts/<id>" title="<email>">`` links.  If
        *hey_email* is found, the numeric contact ID string is returned.

        Returns ``None`` if no matching link can be found.
        """
        imbox = self.fetch_imbox()
        for summary in imbox.emails:
            topic_html = self._get(summary.topic_url, accept=_ACCEPT_HTML).text
            soup = BeautifulSoup(topic_html, "html.parser")
            contact_id = _extract_contact_id_for_email(soup, hey_email)
            if contact_id is not None:
                return contact_id
        return None

    def fetch_html(self, path_or_url: str) -> str:
        response = self._get(path_or_url, accept=_ACCEPT_HTML)
        self.last_html = response.text
        self.last_csrf_token = _extract_csrf_token(self.last_html)
        return self.last_html

    def fetch_page(self, path_or_url: str) -> InboxPage:
        response = self._get(path_or_url, accept=_ACCEPT_HTML)
        self.last_html = response.text
        self.last_csrf_token = _extract_csrf_token(self.last_html)
        final_url = str(response.url)
        return parse_imbox_page(final_url, self.last_html)

    def fetch_topic_payload(self, summary: EmailSummary) -> TopicPayload:
        topic_id = _extract_topic_id(summary.topic_url)
        topic_response = self._get(summary.topic_url, accept=_ACCEPT_HTML)
        topic_html = topic_response.text
        message_ids = _extract_message_ids(topic_html)
        messages: list[MessagePayload] = []
        for message_id in message_ids:
            source_url = routes.message_text_url(self.account.hey_host, message_id)
            text_response = self._get(source_url, accept=_ACCEPT_TEXT)
            messages.append(
                MessagePayload(
                    message_id=message_id,
                    source_url=source_url,
                    content_text=text_response.text,
                )
            )

        self.last_html = topic_html
        self.last_csrf_token = _extract_csrf_token(topic_html)
        return TopicPayload(
            topic_id=topic_id,
            topic_url=summary.topic_url,
            sender=summary.sender,
            subject=summary.subject,
            snippet=summary.snippet,
            when=summary.when,
            messages=messages,
        )

    def replace_session(self, session: SessionRecord) -> None:
        self.client.cookies.clear()
        self._load_cookies(session.cookie_jar_json)
        self.last_url = session.final_url
        self.last_csrf_token = session.csrf_token

    def export_session_state(self) -> tuple[str, str | None, str]:
        cookies = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": bool(cookie.secure),
                "expiry": cookie.expires,
            }
            for cookie in self.client.cookies.jar
        ]
        cookie_jar_json = json.dumps(cookies, separators=(",", ":"))
        final_url = self.last_url or self.base_url
        return cookie_jar_json, self.last_csrf_token, final_url

    def post_form(
        self,
        path_or_url: str,
        data: Mapping[str, str | list[str]],
        *,
        accept: str | None = None,
        xhr: bool = True,
        multipart: bool = False,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        url = path_or_url
        if not path_or_url.startswith("http"):
            url = urljoin(self.base_url, path_or_url)
        _ensure_allowed(url, self.account.hey_host)
        accept_header = accept if accept is not None else _ACCEPT_HTML
        extra: dict[str, str] = {"Accept": accept_header}
        if xhr:
            extra["X-Requested-With"] = "XMLHttpRequest"
            if self.last_csrf_token:
                extra["X-CSRF-Token"] = self.last_csrf_token
        if multipart:
            files = _data_to_multipart(data)
            response = self.client.post(
                url,
                files=files,
                headers=extra,
                follow_redirects=follow_redirects,
            )
        else:
            response = self.client.post(
                url,
                data=data,
                headers=extra,
                follow_redirects=follow_redirects,
            )
        if not follow_redirects and response.status_code in (301, 302, 303, 307, 308):
            # Caller wants the raw redirect — skip raise_for_status and sign-in check.
            return response
        response.raise_for_status()
        final_url = str(response.url)
        _ensure_allowed(final_url, self.account.hey_host)
        if urlparse(final_url).path.startswith(routes.SIGN_IN):
            raise AuthenticationRequiredError("session appears expired")
        self.last_url = final_url
        return response

    def _load_cookies(self, cookie_jar_json: str) -> None:
        cookies = json.loads(cookie_jar_json)
        if not isinstance(cookies, list):
            return
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            domain = cookie.get("domain")
            path = cookie.get("path") or "/"
            if not isinstance(path, str):
                path = "/"
            if isinstance(domain, str) and domain:
                self.client.cookies.set(name, value, domain=domain, path=path)
            else:
                self.client.cookies.set(name, value, path=path)

    def _get(self, path_or_url: str, accept: str) -> httpx.Response:
        url = path_or_url
        if not path_or_url.startswith("http"):
            url = urljoin(self.base_url, path_or_url)
        _ensure_allowed(url, self.account.hey_host)
        response = self.client.get(url, headers={"Accept": accept})
        response.raise_for_status()
        final_url = str(response.url)
        _ensure_allowed(final_url, self.account.hey_host)
        if urlparse(final_url).path.startswith(routes.SIGN_IN):
            raise AuthenticationRequiredError("session appears expired")
        self.last_url = final_url
        return response


def parse_imbox_page(base_url: str, html: str) -> InboxPage:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content") or soup

    topic_articles = main.select("article.posting[data-topic='true']")
    if not topic_articles:
        topic_articles = [
            article
            for article in main.select("article")
            if article.select_one("a[href*='/topics/']") is not None
        ]

    emails: list[EmailSummary] = []
    for article in topic_articles:
        topic_link = article.select_one("a.permalink[href]") or article.select_one(
            "a[href*='/topics/']"
        )
        if topic_link is None:
            continue
        href = topic_link.get("href")
        if not isinstance(href, str) or not href:
            continue
        topic_url = urljoin(base_url, href)

        sender = _extract_sender(article)
        subject = _extract_subject(article)
        snippet = _extract_snippet(article)
        when = _extract_when(article)

        emails.append(
            EmailSummary(
                sender=sender,
                subject=subject,
                snippet=snippet,
                when=when,
                topic_url=topic_url,
            )
        )

    next_page_url = None
    next_link = main.select_one(
        "a.pagination-link[data-pagination-target='nextPageLink']"
    )
    if next_link is None:
        next_link = main.select_one("a.pagination-link[href]")
    if next_link is not None:
        href = next_link.get("href")
        if isinstance(href, str) and href:
            next_page_url = urljoin(base_url, href)

    return InboxPage(emails=emails, next_page_url=next_page_url)


def _extract_sender(article: Tag) -> str:
    candidate = article.select_one(".posting__detail")
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    candidate = article.select_one("[data-sender], .sender, .thread-sender, strong")
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    lines = _article_lines(article)
    return lines[0] if lines else "(unknown)"


def _extract_subject(article: Tag) -> str:
    candidate = article.select_one(".posting__title")
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    candidate = article.select_one(
        "h1, h2, h3, [data-subject], .subject, .thread-subject"
    )
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    lines = _article_lines(article)
    return lines[1] if len(lines) > 1 else "(no subject)"


def _extract_snippet(article: Tag) -> str:
    candidate = article.select_one(".posting__summary")
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    candidate = article.select_one("p, [data-snippet], .snippet")
    if candidate is not None:
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    lines = _article_lines(article)
    return lines[2] if len(lines) > 2 else ""


def _extract_when(article: Tag) -> str:
    candidate = article.select_one("time.posting__time") or article.select_one("time")
    if candidate is not None:
        # Prefer the machine-readable ISO 8601 datetime attribute over the
        # human-readable display text (e.g. "2h ago", "Mar 1").
        dt_attr = candidate.get("datetime")
        if isinstance(dt_attr, str) and dt_attr.strip():
            return dt_attr.strip()
        text = candidate.get_text(" ", strip=True)
        if text:
            return text
    lines = _article_lines(article)
    return lines[-1] if lines else ""


def _article_lines(article: Tag) -> list[str]:
    return [line.strip() for line in article.stripped_strings if line.strip()]


def _ensure_allowed(url: str, host: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    if parsed.hostname != host:
        raise ValueError(f"refusing non-HEY host: {parsed.hostname}")


def _extract_csrf_token(html: str) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one("meta[name='csrf-token']")
    if meta is None:
        return None
    value = meta.get("content")
    return value if isinstance(value, str) and value else None


def _extract_topic_id(topic_url: str) -> str:
    """Extract the topic ID from a topic URL path.

    Parses the URL path and returns the segment immediately after ``/topics/``.
    """
    path_parts = [p for p in urlparse(topic_url).path.split("/") if p]
    try:
        idx = path_parts.index("topics")
    except ValueError:
        raise ValueError(f"unable to parse topic id from url: {topic_url}") from None
    if idx + 1 >= len(path_parts):
        raise ValueError(f"unable to parse topic id from url: {topic_url}")
    return path_parts[idx + 1]


def _extract_message_ids(topic_html: str) -> list[str]:
    """Extract message IDs from a topic page's anchor hrefs.

    Uses BeautifulSoup to find ``<a href="…/messages/<id>…">`` links and
    parses the path with ``urllib.parse`` rather than regex.
    """
    soup = BeautifulSoup(topic_html, "html.parser")
    ids: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href*='/messages/']"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        path_parts = [p for p in urlparse(href).path.split("/") if p]
        # Strip a trailing ".text" or ".eml" extension from the id segment.
        try:
            idx = path_parts.index("messages")
        except ValueError:
            continue
        if idx + 1 >= len(path_parts):
            continue
        raw_id = path_parts[idx + 1]
        # Remove known file extensions.
        for ext in (".text", ".eml"):
            if raw_id.endswith(ext):
                raw_id = raw_id[: -len(ext)]
                break
        if not raw_id or raw_id in seen:
            continue
        seen.add(raw_id)
        ids.append(raw_id)

    return ids


def _extract_contact_id_for_email(soup: BeautifulSoup, email: str) -> str | None:
    """Return the contact ID for *email* found in an already-parsed topic page.

    Looks for ``<a href="/contacts/<id>" title="<email>">`` links; the
    ``title`` attribute on such links is the full email address of the contact.
    Returns the ID string if found, otherwise ``None``.
    """
    for anchor in soup.select("a[href*='/contacts/']"):
        title = anchor.get("title")
        if not isinstance(title, str):
            continue
        if title.strip().lower() == email.strip().lower():
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            path_parts = [p for p in urlparse(href).path.split("/") if p]
            try:
                idx = path_parts.index("contacts")
            except ValueError:
                continue
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
    return None
