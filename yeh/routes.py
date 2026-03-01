from enum import StrEnum
from urllib.parse import urlunparse

HOST = "app.hey.com"

SIGN_IN = "/sign_in"
TWO_FACTOR_CHALLENGE = "/two_factor_authentication/challenge"
TWO_FACTOR_CHALLENGE_WEBAUTHN = "/two_factor_authentication/challenge/web_authn"
TWO_FACTOR_CHALLENGE_TOTP = "/two_factor_authentication/challenge?scheme_type=totp"

IMBOX = "/imbox"


class Mailbox(StrEnum):
    IMBOX = "imbox"
    FEEDBOX = "feedbox"
    PAPER_TRAIL = "paper_trail"
    DRAFTS = "drafts"
    SENT = "sent"
    PREVIOUSLY_SEEN = "previously_seen"
    SCREENED_OUT = "screened_out"
    SPAM = "spam"
    TRASH = "trash"
    EVERYTHING = "everything"


MAILBOX_PATHS: dict[Mailbox, str] = {
    Mailbox.IMBOX: "/imbox",
    Mailbox.FEEDBOX: "/feedbox",
    Mailbox.PAPER_TRAIL: "/paper_trail",
    Mailbox.DRAFTS: "/entries/drafts",
    Mailbox.SENT: "/topics/sent",
    Mailbox.PREVIOUSLY_SEEN: "/imbox/seen",
    Mailbox.SCREENED_OUT: "/contacts/denied",
    Mailbox.SPAM: "/topics/spam",
    Mailbox.TRASH: "/topics/trash",
    Mailbox.EVERYTHING: "/topics/everything",
}

MAILBOX_LABELS: dict[Mailbox, str] = {
    Mailbox.IMBOX: "Inbox",
    Mailbox.FEEDBOX: "Feedbox",
    Mailbox.PAPER_TRAIL: "Paper Trail",
    Mailbox.DRAFTS: "Drafts",
    Mailbox.SENT: "Sent",
    Mailbox.PREVIOUSLY_SEEN: "Previously Seen",
    Mailbox.SCREENED_OUT: "Screened Out",
    Mailbox.SPAM: "Spam",
    Mailbox.TRASH: "Trash",
    Mailbox.EVERYTHING: "Everything",
}


def parse_mailbox(value: str) -> Mailbox:
    return Mailbox(value.strip().lower())


def mailbox_label(mailbox: Mailbox) -> str:
    return MAILBOX_LABELS.get(mailbox, mailbox.value)


def parse_mailbox_friendly(value: str) -> Mailbox:
    token = " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())
    if token == "inbox":
        return Mailbox.IMBOX
    for mailbox in Mailbox:
        if token == " ".join(mailbox_label(mailbox).strip().lower().split()):
            return mailbox
    return parse_mailbox(token)


def https_url(host: str, path: str = "/") -> str:
    p = path if path.startswith("/") else f"/{path}"
    return urlunparse(("https", host, p, "", "", ""))


def mailbox_url(host: str, mailbox: Mailbox) -> str:
    return https_url(host, MAILBOX_PATHS[mailbox])


def message_text_url(host: str, message_id: str) -> str:
    return https_url(host, f"/messages/{message_id}.text")
