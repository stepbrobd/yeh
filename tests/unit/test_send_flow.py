from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from yeh.mailbox import HeyClient
from yeh.send import (
    ComposeDefaults,
    load_compose_defaults,
    load_new_mail_compose_defaults,
    send_new,
    send_reply,
)


@dataclass
class _Resp:
    status_code: int
    headers: dict[str, str]
    url: str
    text: str


class _FakeClient:
    def __init__(
        self,
        responses: list[_Resp],
        html: str = "",
        expected_compose_url: str = "/messages/new",
        sender_contact_id: str | None = None,
    ) -> None:
        self.responses = responses
        self.html = html
        self.expected_compose_url = expected_compose_url
        self.sender_contact_id = sender_contact_id
        self.calls: list[tuple[str, dict[str, str | list[str]], str | None, bool]] = []

    def fetch_html(self, path: str) -> str:
        assert path == self.expected_compose_url, (
            f"fetch_html called with {path!r}, expected {self.expected_compose_url!r}"
        )
        return self.html

    def fetch_sender_contact_id(self, hey_email: str) -> str | None:
        return self.sender_contact_id

    def post_form(
        self,
        path: str,
        data,
        *,
        accept: str | None = None,
        xhr: bool = True,
        multipart: bool = False,
        follow_redirects: bool = True,
    ):
        self.calls.append((path, dict(data), accept, xhr))
        return self.responses.pop(0)


def test_load_compose_defaults_from_compose_html() -> None:
    html = (
        "<html><body>"
        "<input name='acting_sender_id' value='42'/>"
        "<input name='acting_sender_email' value='user@hey.com'/>"
        "</body></html>"
    )
    client = _FakeClient([], html=html, expected_compose_url="/entries/1/replies/new")
    out = load_compose_defaults(cast(HeyClient, client), "/entries/1/replies/new")
    assert out.acting_sender_id == "42"
    assert out.acting_sender_email == "user@hey.com"


def test_load_compose_defaults_with_custom_url() -> None:
    """Reply compose URL /entries/<id>/replies/new should be used for replies."""
    html = (
        "<html><body>"
        "<input name='acting_sender_id' value='99'/>"
        "<input name='acting_sender_email' value='sender@hey.com'/>"
        "</body></html>"
    )
    client = _FakeClient(
        [],
        html=html,
        expected_compose_url="/entries/123/replies/new",
    )
    out = load_compose_defaults(
        cast(HeyClient, client),
        compose_url="/entries/123/replies/new",
    )
    assert out.acting_sender_id == "99"
    assert out.acting_sender_email == "sender@hey.com"


def test_load_new_mail_compose_defaults_success() -> None:
    """load_new_mail_compose_defaults returns ComposeDefaults from contact ID lookup."""
    client = _FakeClient([], sender_contact_id="77")
    out = load_new_mail_compose_defaults(cast(HeyClient, client), "user@hey.com")
    assert out.acting_sender_id == "77"
    assert out.acting_sender_email == "user@hey.com"


def test_load_new_mail_compose_defaults_raises_when_no_contact_id() -> None:
    """Raises ValueError when acting_sender_id cannot be found in imbox topics."""
    import pytest

    client = _FakeClient([], sender_contact_id=None)
    with pytest.raises(ValueError, match="acting_sender_id"):
        load_new_mail_compose_defaults(cast(HeyClient, client), "user@hey.com")


def test_send_new_success_with_non_numeric_draft_id() -> None:
    defaults = ComposeDefaults("42", "user@hey.com")
    client = _FakeClient(
        [
            _Resp(302, {"Location": "https://app.hey.com/messages/draft-abc"}, "", ""),
            _Resp(302, {"Location": "https://app.hey.com/topics/sent"}, "", ""),
        ]
    )
    out = send_new(
        cast(HeyClient, client),
        defaults,
        ["a@example.com"],
        ["c@example.com"],
        ["b@example.com"],
        "subj",
        "<p>hi</p>",
    )
    assert out.ok is True
    assert out.draft_id == "draft-abc"
    assert out.reason == "send_commit"
    assert client.calls[0][0] == "/messages"
    assert client.calls[1][0] == "/messages/draft-abc"
    # Draft phase must use XHR; send commit must not.
    assert client.calls[0][3] is True
    assert client.calls[1][3] is False
    first_payload = client.calls[0][1]
    assert first_payload["entry[addressed][directly][]"] == ["a@example.com"]
    assert first_payload["entry[addressed][copied][]"] == ["c@example.com"]
    assert first_payload["entry[addressed][blindcopied][]"] == ["b@example.com"]
    assert (
        first_payload["entry[scheduled_bubble_up_on]"]
        == datetime.now(UTC).date().isoformat()
    )
    assert "entry[scheduled_delivery_at_date]" in first_payload
    assert "entry[scheduled_delivery_at_hour]" in first_payload
    assert first_payload["entry[status]"] == "drafted"
    assert first_payload["autodraft"] == "true"


def test_send_new_fails_when_draft_id_missing() -> None:
    defaults = ComposeDefaults("42", "user@hey.com")
    client = _FakeClient([_Resp(200, {}, "https://app.hey.com/messages", "no id")])
    out = send_new(
        cast(HeyClient, client),
        defaults,
        ["a@example.com"],
        [],
        [],
        "subj",
        "<p>hi</p>",
    )
    assert out.ok is False
    assert out.reason == "draft_id_not_found"


def test_send_new_can_extract_draft_id_from_escaped_response_text() -> None:
    defaults = ComposeDefaults("42", "user@hey.com")
    client = _FakeClient(
        [
            _Resp(
                200,
                {},
                "https://app.hey.com/messages",
                '{"location":"https:\\/\\/app.hey.com\\/messages\\/1234567890"}',
            ),
            _Resp(302, {"Location": "https://app.hey.com/topics/sent"}, "", ""),
        ]
    )
    out = send_new(
        cast(HeyClient, client),
        defaults,
        ["a@example.com"],
        [],
        [],
        "subj",
        "<p>hi</p>",
    )
    assert out.ok is True
    assert out.draft_id == "1234567890"
    assert client.calls[1][0] == "/messages/1234567890"


def test_send_reply_success() -> None:
    defaults = ComposeDefaults("42", "user@hey.com")
    client = _FakeClient(
        [
            # Phase 1: draft save — XHR, returns Location with draft message id
            _Resp(
                204,
                {"Location": "https://app.hey.com/messages/9999"},
                "",
                "",
            ),
            # Phase 2: send commit — Turbo form, returns 302 to imbox
            _Resp(302, {"Location": "https://app.hey.com/imbox"}, "", ""),
        ]
    )
    out = send_reply(
        cast(HeyClient, client),
        "1",
        defaults,
        ["a@example.com"],
        ["c@example.com"],
        ["b@example.com"],
        "subj",
        "<p>r</p>",
    )
    assert out.ok is True
    assert out.reason == "send_commit"
    assert out.draft_id == "9999"
    # Phase 1: draft save to /entries/<id>/replies with XHR
    assert client.calls[0][0] == "/entries/1/replies"
    assert client.calls[0][3] is True  # xhr=True
    draft_payload = client.calls[0][1]
    assert draft_payload["entry[addressed][copied][]"] == ["c@example.com"]
    assert draft_payload["entry[addressed][blindcopied][]"] == ["b@example.com"]
    assert draft_payload["entry[scheduled_delivery_at_date]"] == "tomorrow"
    assert draft_payload["entry[status]"] == "drafted"
    assert draft_payload["autodraft"] == "true"
    # Phase 2: send commit to /messages/<draft_id> without XHR
    assert client.calls[1][0] == "/messages/9999"
    assert client.calls[1][3] is False  # xhr=False
    send_payload = client.calls[1][1]
    assert send_payload["commit"] == "Send email"
    assert "entry[status]" not in send_payload
    assert "autodraft" not in send_payload


def test_send_reply_fails_when_draft_id_missing() -> None:
    defaults = ComposeDefaults("42", "user@hey.com")
    client = _FakeClient(
        [_Resp(200, {}, "https://app.hey.com/entries/1/replies", "no id here")]
    )
    out = send_reply(
        cast(HeyClient, client),
        "1",
        defaults,
        ["a@example.com"],
        [],
        [],
        "subj",
        "<p>r</p>",
    )
    assert out.ok is False
    assert out.reason == "draft_id_not_found"
    assert len(client.calls) == 1
