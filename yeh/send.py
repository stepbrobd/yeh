import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from yeh.mailbox import HeyClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposeDefaults:
    acting_sender_id: str
    acting_sender_email: str


@dataclass(frozen=True)
class SendResult:
    ok: bool
    location: str | None
    draft_id: str | None
    status_code: int
    reason: str


def load_compose_defaults(
    client: HeyClient,
    compose_url: str,
) -> ComposeDefaults:
    """Fetch the compose form at *compose_url* and extract acting-sender fields.

    For replies pass ``/entries/<entry_id>/replies/new`` so that HEY returns
    the correct ``acting_sender_id`` / ``acting_sender_email`` values and a
    fresh CSRF token valid for the subsequent draft-save POST.
    """
    html = client.fetch_html(compose_url)
    soup = BeautifulSoup(html, "html.parser")
    acting_sender_id = _value(soup, "acting_sender_id")
    acting_sender_email = _value(soup, "acting_sender_email")
    if not acting_sender_id or not acting_sender_email:
        raise ValueError(f"unable to parse compose defaults from {compose_url!r}")
    return ComposeDefaults(
        acting_sender_id=acting_sender_id,
        acting_sender_email=acting_sender_email,
    )


def load_new_mail_compose_defaults(
    client: HeyClient,
    hey_email: str,
) -> ComposeDefaults:
    """Return compose defaults for a new (non-reply) outbound message.

    HEY does not expose a standalone ``/messages/new`` compose page; the
    browser renders the new-mail composer as a client-side overlay without a
    dedicated server round-trip.  This function instead:

    1. Fetches the imbox page to obtain a fresh CSRF token (stored on the
       ``client`` object as a side-effect of ``fetch_imbox``).
    2. Walks topic pages returned by the imbox to find the sender's own
       contact ID (``<a href="/contacts/<id>" title="{hey_email}">``) .
    3. Returns a :class:`ComposeDefaults` built from the discovered contact ID
       and the known ``hey_email``.

    If no topic pages are available (e.g. a brand-new account with no mail)
    a ``ValueError`` is raised.
    """
    contact_id = client.fetch_sender_contact_id(hey_email)
    if contact_id is None:
        raise ValueError(
            f"unable to determine acting_sender_id for {hey_email!r}: "
            "no suitable topic page found in imbox"
        )
    return ComposeDefaults(
        acting_sender_id=contact_id,
        acting_sender_email=hey_email,
    )


def send_new(
    client: HeyClient,
    defaults: ComposeDefaults,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    html: str,
) -> SendResult:
    # Phase 1: save as XHR autodraft — HEY returns 204 + Location: /messages/<id>
    draft_data = _draft_payload(
        defaults=defaults,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        html=html,
        method="post",
    )
    draft_response = client.post_form(
        "/messages",
        data=draft_data,
        accept="text/html, application/xhtml+xml",
        xhr=True,
        multipart=True,
        follow_redirects=False,
    )
    location = draft_response.headers.get("Location")
    log.debug(
        "send_new phase1: status=%d location=%r url=%s",
        draft_response.status_code,
        location,
        draft_response.url,
    )
    draft_id = (
        _extract_message_id(location)
        or _extract_message_id(str(draft_response.url))
        or _extract_message_id(draft_response.text)
    )
    if draft_id is None:
        log.warning(
            "send_new phase1 failed: status=%d location=%r body_snippet=%r",
            draft_response.status_code,
            location,
            draft_response.text[:200],
        )
        return SendResult(
            ok=False,
            location=location,
            draft_id=None,
            status_code=draft_response.status_code,
            reason="draft_id_not_found",
        )

    # Phase 2: send commit — standard Turbo form submit (no X-Requested-With)
    send_data = _send_payload(
        defaults=defaults,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        html=html,
    )
    send_response = client.post_form(
        f"/messages/{draft_id}",
        data=send_data,
        accept="text/vnd.turbo-stream.html, text/html, application/xhtml+xml",
        xhr=False,
    )
    return SendResult(
        ok=send_response.status_code in (200, 204, 302, 303),
        location=send_response.headers.get("Location"),
        draft_id=draft_id,
        status_code=send_response.status_code,
        reason="send_commit",
    )


def send_reply(
    client: HeyClient,
    entry_id: str,
    defaults: ComposeDefaults,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    html: str,
) -> SendResult:
    # Phase 1: save as XHR autodraft — HEY returns 204 + Location: /messages/<id>
    draft_data = _draft_payload(
        defaults=defaults,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        html=html,
        method="post",
    )
    draft_response = client.post_form(
        f"/entries/{entry_id}/replies",
        data=draft_data,
        accept="text/html, application/xhtml+xml",
        xhr=True,
        multipart=True,
        follow_redirects=False,
    )
    location = draft_response.headers.get("Location")
    log.debug(
        "send_reply phase1: status=%d location=%r url=%s",
        draft_response.status_code,
        location,
        draft_response.url,
    )
    draft_id = (
        _extract_message_id(location)
        or _extract_message_id(str(draft_response.url))
        or _extract_message_id(draft_response.text)
    )
    if draft_id is None:
        log.warning(
            "send_reply phase1 failed: status=%d location=%r body_snippet=%r",
            draft_response.status_code,
            location,
            draft_response.text[:200],
        )
        return SendResult(
            ok=False,
            location=location,
            draft_id=None,
            status_code=draft_response.status_code,
            reason="draft_id_not_found",
        )

    # Phase 2: send commit — standard Turbo form submit (no X-Requested-With)
    send_data = _send_payload(
        defaults=defaults,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        html=html,
    )
    send_response = client.post_form(
        f"/messages/{draft_id}",
        data=send_data,
        accept="text/vnd.turbo-stream.html, text/html, application/xhtml+xml",
        xhr=False,
    )
    return SendResult(
        ok=send_response.status_code in (200, 204, 302, 303),
        location=send_response.headers.get("Location"),
        draft_id=draft_id,
        status_code=send_response.status_code,
        reason="send_commit",
    )


def _value(soup: BeautifulSoup, name: str) -> str | None:
    node = soup.select_one(f"input[name='{name}']")
    if node is None:
        return None
    v = node.get("value")
    return str(v) if isinstance(v, str) and v else None


def _draft_payload(
    defaults: ComposeDefaults,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    html: str,
    method: str,
) -> Mapping[str, str | list[str]]:
    timing = _timing_values()
    return {
        "message[auto_quoting]": "false",
        "acting_sender_id": defaults.acting_sender_id,
        "acting_sender_email": defaults.acting_sender_email,
        "entry[addressed][directly][]": list(to),
        "entry[addressed][copied][]": list(cc),
        "entry[addressed][blindcopied][]": list(bcc),
        "message[subject]": subject,
        "message[content]": html,
        "entry[scheduled_delivery]": "false",
        "entry[scheduled_delivery_at_date]": timing.delivery_date,
        "entry[scheduled_delivery_at_hour]": timing.delivery_hour,
        "entry[scheduled_bubble_up]": "false",
        "entry[scheduled_bubble_up_on]": timing.today_iso,
        "date": timing.today_iso,
        "_method": method,
        "entry[status]": "drafted",
        "autodraft": "true",
    }


def _send_payload(
    defaults: ComposeDefaults,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    html: str,
) -> Mapping[str, str | list[str]]:
    timing = _timing_values()
    return {
        "message[auto_quoting]": "false",
        "acting_sender_id": defaults.acting_sender_id,
        "acting_sender_email": defaults.acting_sender_email,
        "entry[addressed][directly][]": list(to),
        "entry[addressed][copied][]": list(cc),
        "entry[addressed][blindcopied][]": list(bcc),
        "message[subject]": subject,
        "message[content]": html,
        "entry[scheduled_delivery]": "false",
        "entry[scheduled_delivery_at_date]": timing.delivery_date,
        "entry[scheduled_delivery_at_hour]": timing.delivery_hour,
        "entry[scheduled_bubble_up]": "false",
        "entry[scheduled_bubble_up_on]": timing.today_iso,
        "date": timing.today_iso,
        "_method": "PUT",
        "commit": "Send email",
    }


@dataclass(frozen=True)
class _TimingValues:
    today_iso: str
    delivery_date: str
    delivery_hour: str


def _timing_values() -> _TimingValues:
    now = datetime.now(UTC)
    return _TimingValues(
        today_iso=now.date().isoformat(),
        delivery_date="tomorrow",
        delivery_hour=str(now.hour),
    )


def _extract_message_id(location: str | None) -> str | None:
    """Extract the message ID from a URL or URL-like string.

    Handles:
    - Plain URL strings e.g. ``https://app.hey.com/messages/123``
    - JSON bodies e.g. ``{"location":"https:\\/\\/.../messages/123"}``
    - HTML-entity-encoded URLs
    """
    if location is None:
        return None

    # If the input looks like a JSON object, extract the location value from it.
    stripped = location.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            inner = obj.get("location") if isinstance(obj, dict) else None
            if isinstance(inner, str):
                return _extract_message_id(inner)
        except (json.JSONDecodeError, ValueError):  # fmt: skip
            pass

    candidates = [location]
    normalized = location.replace("\\/", "/")
    if normalized != location:
        candidates.append(normalized)
    html_normalized = unescape(normalized)
    if html_normalized != normalized:
        candidates.append(html_normalized)

    for candidate in candidates:
        parsed = urlsplit(candidate)
        path_parts = [p for p in parsed.path.split("/") if p]
        try:
            idx = path_parts.index("messages")
        except ValueError:
            pass
        else:
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
    return None
