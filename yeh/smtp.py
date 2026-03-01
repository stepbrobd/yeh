from dataclasses import dataclass, field
from email import policy
from email.parser import Parser
from email.utils import getaddresses, parseaddr
from enum import Enum


class SmtpState(Enum):
    CONNECTED = "connected"
    READY = "ready"
    MAIL = "mail"
    RCPT = "rcpt"
    DATA = "data"
    QUIT = "quit"


@dataclass(frozen=True)
class NewMail:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    html: str


@dataclass(frozen=True)
class ReplyMail:
    entry_id: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    html: str


MailAction = NewMail | ReplyMail


@dataclass
class Machine:
    state: SmtpState = SmtpState.CONNECTED
    sender: str | None = None
    recipients: list[str] = field(default_factory=list)
    data_lines: list[str] = field(default_factory=list)

    def handle(self, line: str) -> tuple[int, str, MailAction | None]:
        cmd = line.rstrip("\r\n")

        if self.state == SmtpState.CONNECTED:
            if cmd.upper().startswith(("EHLO", "HELO")):
                self.state = SmtpState.READY
                return 250, "OK", None
            return 503, "Send HELO/EHLO first", None

        if self.state == SmtpState.READY:
            if cmd.upper().startswith("MAIL FROM:"):
                self.sender = _arg(cmd)
                self.recipients.clear()
                self.data_lines.clear()
                self.state = SmtpState.MAIL
                return 250, "Sender accepted", None
            if cmd.upper() == "QUIT":
                self.state = SmtpState.QUIT
                return 221, "Bye", None
            return 503, "Need MAIL FROM", None

        if self.state in (SmtpState.MAIL, SmtpState.RCPT):
            if cmd.upper().startswith("RCPT TO:"):
                self.recipients.append(_arg(cmd))
                self.state = SmtpState.RCPT
                return 250, "Recipient accepted", None
            if cmd.upper() == "DATA":
                if not self.recipients:
                    return 554, "No recipients", None
                self.state = SmtpState.DATA
                self.data_lines.clear()
                return 354, "End data with <CR><LF>.<CR><LF>", None
            return 503, "Need RCPT TO or DATA", None

        if self.state == SmtpState.DATA:
            if cmd == ".":
                action = _parse_data(self.recipients, "\n".join(self.data_lines))
                self.state = SmtpState.READY
                self.data_lines.clear()
                return 250, "Message accepted", action
            self.data_lines.append(cmd)
            return 250, "Continue", None

        return 503, "Session closed", None


def _arg(cmd: str) -> str:
    raw = cmd.split(":", 1)[1].strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    return raw


def _parse_data(recipients: list[str], raw: str) -> MailAction:
    msg = Parser(policy=policy.default).parsestr(raw)
    subject = str(msg.get("Subject", "")).strip() or "(no subject)"
    html = _body(msg)
    envelope = [_normalize_email(x) for x in recipients]
    envelope = [x for x in envelope if x]
    to, cc, bcc = _split_recipients(msg, envelope)
    entry_id = str(msg.get("X-HEY-Reply-Entry-ID", "")).strip()
    if entry_id:
        return ReplyMail(
            entry_id=entry_id,
            to=tuple(to),
            cc=tuple(cc),
            bcc=tuple(bcc),
            subject=subject,
            html=html,
        )
    return NewMail(
        to=tuple(to), cc=tuple(cc), bcc=tuple(bcc), subject=subject, html=html
    )


def _normalize_email(value: str) -> str:
    _, addr = parseaddr(value)
    return addr.strip().lower()


def _split_recipients(
    msg, envelope: list[str]
) -> tuple[list[str], list[str], list[str]]:
    to = [_normalize_email(x) for _, x in getaddresses(msg.get_all("To", []))]
    cc = [_normalize_email(x) for _, x in getaddresses(msg.get_all("Cc", []))]
    bcc = [_normalize_email(x) for _, x in getaddresses(msg.get_all("Bcc", []))]

    envelope_set = set(envelope)
    to_list = [x for x in to if x and x in envelope_set]
    cc_list = [x for x in cc if x and x in envelope_set and x not in to_list]

    bcc_seed = [
        x
        for x in bcc
        if x and x in envelope_set and x not in to_list and x not in cc_list
    ]
    bcc_rest = [
        x
        for x in envelope
        if x not in to_list and x not in cc_list and x not in bcc_seed
    ]
    bcc_list = bcc_seed + bcc_rest

    if not to_list and envelope:
        to_list = [envelope[0]]
        bcc_list = [x for x in envelope[1:] if x not in cc_list]

    return to_list, cc_list, bcc_list


def _body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                text = part.get_content()
                if isinstance(text, str):
                    return text
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                text = part.get_content()
                if isinstance(text, str):
                    return f"<pre>{text}</pre>"
        return ""
    content = msg.get_content()
    if not isinstance(content, str):
        return ""
    if msg.get_content_type() == "text/html":
        return content
    return f"<pre>{content}</pre>"
