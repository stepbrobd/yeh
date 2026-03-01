import base64
import binascii
import logging
import re
import shlex
import signal
import socket
import socketserver
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC
from email import policy
from email.parser import Parser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import cast

from yeh import routes
from yeh.auth import login as hey_login
from yeh.config import AppPaths, ResolvedAccount
from yeh.hey import Client
from yeh.imap import ReadOnlyClient
from yeh.mailbox import AuthenticationRequiredError, HeyClient
from yeh.smtp import Machine, SmtpState
from yeh.storage import SessionRecord, Storage

LOG = logging.getLogger(__name__)


class _ClientDisconnected(Exception):
    pass


@dataclass(frozen=True)
class ServerConfig:
    smtps_host: str
    smtps_port: int
    imaps_host: str
    imaps_port: int
    tls_cert_file: Path
    tls_key_file: Path


@dataclass
class Runtime:
    paths: AppPaths
    account: ResolvedAccount
    debug: bool
    auth_lock: threading.Lock
    sync_lock: threading.Lock
    imap_sync_min_interval_seconds: float
    imap_sync_max_pages: int
    imap_sync_workers: int
    last_imap_sync_at_monotonic: float = 0.0
    imap_sync_in_progress: bool = False

    def submit_message(self, machine_action):
        storage = Storage(self.paths.account_db_path(self.account.hey_email))
        try:
            session = storage.load_session(self.account.hey_email)
            if session is None:
                session = self._reauth(storage)

            client = HeyClient(account=self.account, session=session)
            try:
                api = Client(
                    storage=storage,
                    hey_email=self.account.hey_email,
                    web=client,
                )
                try:
                    result = api.smtp_submit(machine_action)
                except AuthenticationRequiredError:
                    session = self._reauth(storage, force=True)
                    client.replace_session(session)
                    result = api.smtp_submit(machine_action)

                cookie_jar_json, csrf_token, final_url = client.export_session_state()
                storage.save_session(
                    hey_email=self.account.hey_email,
                    cookie_jar_json=cookie_jar_json,
                    csrf_token=csrf_token,
                    final_url=final_url,
                )
                return result
            finally:
                client.close()
        finally:
            storage.close()

    def _reauth(self, storage: Storage, force: bool = False) -> SessionRecord:
        with self.auth_lock:
            existing = storage.load_session(self.account.hey_email)
            if existing is not None and not force:
                return existing
            result = hey_login(
                account=self.account, debug=self.debug, show_browser=False
            )
            storage.save_session(
                hey_email=self.account.hey_email,
                cookie_jar_json=result.cookie_jar_json,
                csrf_token=result.csrf_token,
                final_url=result.final_url,
            )
            session = storage.load_session(self.account.hey_email)
            if session is None:
                raise RuntimeError("failed to persist authenticated session")
            return session

    def request_imap_sync(self, reason: str, *, force: bool = False) -> None:
        now = time.monotonic()
        with self.sync_lock:
            if self.imap_sync_in_progress:
                LOG.debug("imap sync skipped reason=%s in_progress=true", reason)
                return
            age = now - self.last_imap_sync_at_monotonic
            if not force and age < self.imap_sync_min_interval_seconds:
                LOG.debug(
                    "imap sync skipped reason=%s cooldown=%.2fs",
                    reason,
                    self.imap_sync_min_interval_seconds - age,
                )
                return
            self.last_imap_sync_at_monotonic = now
            self.imap_sync_in_progress = True

        threading.Thread(
            target=self._sync_mail_for_imap_worker,
            args=(reason,),
            daemon=True,
            name=f"imap-sync-{reason.lower()}",
        ).start()

    def sync_mail_for_imap_now(self, reason: str) -> None:
        now = time.monotonic()
        with self.sync_lock:
            if self.imap_sync_in_progress:
                LOG.debug("imap sync immediate-skip reason=%s in_progress=true", reason)
                return
            self.last_imap_sync_at_monotonic = now
            self.imap_sync_in_progress = True
        self._sync_mail_for_imap_worker(reason)

    def _sync_mail_for_imap_worker(self, reason: str) -> None:

        LOG.info("imap sync begin reason=%s", reason)
        storage = Storage(self.paths.account_db_path(self.account.hey_email))
        try:
            session = storage.load_session(self.account.hey_email)
            if session is None:
                session = self._reauth(storage)

            client = HeyClient(account=self.account, session=session)
            try:
                api = Client(
                    storage=storage, hey_email=self.account.hey_email, web=client
                )
                try:
                    api.refresh_all(
                        max_pages=self.imap_sync_max_pages,
                        workers=self.imap_sync_workers,
                    )
                except AuthenticationRequiredError:
                    session = self._reauth(storage, force=True)
                    client.replace_session(session)
                    api.refresh_all(
                        max_pages=self.imap_sync_max_pages,
                        workers=self.imap_sync_workers,
                    )

                cookie_jar_json, csrf_token, final_url = client.export_session_state()
                storage.save_session(
                    hey_email=self.account.hey_email,
                    cookie_jar_json=cookie_jar_json,
                    csrf_token=csrf_token,
                    final_url=final_url,
                )
            finally:
                client.close()
        except (OSError, RuntimeError, ValueError) as exc:
            LOG.warning("imap sync failed reason=%s error=%s", reason, exc)
        finally:
            storage.close()
            with self.sync_lock:
                self.imap_sync_in_progress = False
        LOG.info("imap sync end reason=%s", reason)


class _TlsThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, request_handler_class, ssl_context, runtime):
        self.ssl_context = ssl_context
        self.runtime = runtime
        super().__init__(server_address, request_handler_class)

    def get_request(self):
        sock, addr = super().get_request()
        try:
            wrapped = self.ssl_context.wrap_socket(sock, server_side=True)
        except Exception:
            sock.close()
            raise
        return wrapped, addr


class _SmtpsHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        runtime = cast(Runtime, self.server.runtime)  # type: ignore[attr-defined]
        account = runtime.account
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        LOG.info("smtps connect peer=%s", peer)

        machine = Machine()
        password_required = bool(account.mta_passwd)
        authenticated = not password_required

        self._send_line(f"220 {socket.gethostname()} YEH SMTPS ready")

        while True:
            raw = self.rfile.readline(65536)
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue

            LOG.info("smtps cmd peer=%s line=%s", peer, _sanitize(line))
            upper = line.upper()

            if machine.state == SmtpState.DATA:
                if line == ".":
                    code, msg, action = machine.handle(line)
                    if action is None:
                        self._send_line(f"{code} {msg}")
                        continue
                    try:
                        result = runtime.submit_message(action)
                        if result.ok:
                            self._send_line("250 Message accepted")
                            LOG.info("smtps submit ok peer=%s", peer)
                        else:
                            self._send_line("554 HEY rejected message")
                            LOG.warning(
                                "smtps submit rejected peer=%s status=%s reason=%s location=%s draft_id=%s",
                                peer,
                                result.status_code,
                                result.reason,
                                result.location,
                                result.draft_id,
                            )
                    except Exception as exc:
                        LOG.exception("smtps submit failed peer=%s", peer)
                        self._send_line(f"451 temporary failure: {exc}")
                else:
                    machine.data_lines.append(line)
                continue

            if upper.startswith("EHLO"):
                machine.handle(line)
                self._send_line(f"250-{socket.gethostname()}")
                self._send_line("250-AUTH PLAIN LOGIN")
                self._send_line("250 SIZE 52428800")
                continue

            if upper.startswith("AUTH PLAIN"):
                ok = self._auth_plain(line, account)
                authenticated = ok
                self._send_line(
                    "235 Authentication successful" if ok else "535 Auth failed"
                )
                continue

            if upper == "AUTH LOGIN":
                ok = self._auth_login(account)
                authenticated = ok
                self._send_line(
                    "235 Authentication successful" if ok else "535 Auth failed"
                )
                continue

            if upper == "QUIT":
                self._send_line("221 Bye")
                break

            if (
                password_required
                and not authenticated
                and upper.startswith("MAIL FROM:")
            ):
                self._send_line("530 Authentication required")
                continue

            code, msg, action = machine.handle(line)
            if action is not None:
                try:
                    result = runtime.submit_message(action)
                    if result.ok:
                        self._send_line("250 Message accepted")
                    else:
                        self._send_line("554 HEY rejected message")
                        LOG.warning(
                            "smtps submit rejected peer=%s status=%s reason=%s location=%s draft_id=%s",
                            peer,
                            result.status_code,
                            result.reason,
                            result.location,
                            result.draft_id,
                        )
                except Exception as exc:
                    LOG.exception("smtps submit failed peer=%s", peer)
                    self._send_line(f"451 temporary failure: {exc}")
                continue
            self._send_line(f"{code} {msg}")

        LOG.info("smtps disconnect peer=%s", peer)

    def _auth_plain(self, line: str, account: ResolvedAccount) -> bool:
        parts = line.split(" ", 2)
        token = parts[2] if len(parts) == 3 else ""
        if not token:
            self._send_line("334 ")
            prompt = self.rfile.readline(65536)
            token = prompt.decode("utf-8", errors="replace").strip()
        username, password = _decode_plain_auth(token)
        if username is None or password is None:
            return False
        return _auth_ok(account, username, password)

    def _auth_login(self, account: ResolvedAccount) -> bool:
        self._send_line("334 VXNlcm5hbWU6")
        user_line = self.rfile.readline(65536)
        username = _decode_base64_text(
            user_line.decode("utf-8", errors="replace").strip()
        )
        self._send_line("334 UGFzc3dvcmQ6")
        pass_line = self.rfile.readline(65536)
        password = _decode_base64_text(
            pass_line.decode("utf-8", errors="replace").strip()
        )
        if username is None or password is None:
            return False
        return _auth_ok(account, username, password)

    def _send_line(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("utf-8"))
        self.wfile.flush()


class _ImapsHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        runtime = cast(Runtime, self.server.runtime)  # type: ignore[attr-defined]
        account = runtime.account
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        LOG.info("imaps connect peer=%s", peer)

        storage = Storage(runtime.paths.account_db_path(account.hey_email))
        imap_client = ReadOnlyClient(storage=storage, hey_email=account.hey_email)
        authenticated = False
        selected_mailbox: routes.Mailbox | None = None
        try:
            self._send("* OK YEH IMAPS ready")
            while True:
                raw = self.rfile.readline(65536)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                LOG.info("imaps cmd peer=%s line=%s", peer, _sanitize(line))

                tag, cmd, args = _parse_imap_line(line)
                command = cmd.upper()
                match command:
                    case "CAPABILITY":
                        self._send("* CAPABILITY IMAP4rev1 AUTH=PLAIN")
                        self._send(f"{tag} OK CAPABILITY completed")
                    case "NOOP":
                        runtime.request_imap_sync("NOOP")
                        self._send(f"{tag} OK NOOP completed")
                    case "CHECK":
                        runtime.sync_mail_for_imap_now("CHECK")
                        self._send(f"{tag} OK CHECK completed")
                    case "CLOSE":
                        selected_mailbox = None
                        self._send(f"{tag} OK CLOSE completed")
                    case "EXPUNGE":
                        self._send(f"{tag} OK EXPUNGE completed")
                    case "LOGOUT":
                        self._send("* BYE YEH IMAPS logging out")
                        self._send(f"{tag} OK LOGOUT completed")
                        break
                    case "LOGIN":
                        username, password = _imap_login_args(args)
                        if username is None or password is None:
                            self._send(f"{tag} BAD malformed LOGIN")
                            continue
                        if _auth_ok(account, username, password):
                            authenticated = True
                            self._send(f"{tag} OK LOGIN completed")
                        else:
                            self._send(f"{tag} NO LOGIN failed")
                    case _ if not authenticated:
                        self._send(f"{tag} NO Authenticate first")
                    case "LIST":
                        runtime.request_imap_sync("LIST")
                        self._emit_mailbox_listing("LIST", tag, imap_client)
                    case "LSUB":
                        runtime.request_imap_sync("LSUB")
                        self._emit_mailbox_listing("LSUB", tag, imap_client)
                    case "SELECT":
                        runtime.request_imap_sync("SELECT")
                        if _is_empty_mailbox_arg(args):
                            selected_mailbox = None
                            self._send("* FLAGS (\\Seen)")
                            self._send("* 0 EXISTS")
                            self._send("* 0 RECENT")
                            self._send(f"{tag} OK [READ-ONLY] SELECT completed")
                            continue
                        mailbox = _parse_select_mailbox(args)
                        if mailbox is None:
                            self._send(f"{tag} BAD invalid mailbox")
                            continue
                        count = imap_client.select(mailbox)
                        selected_mailbox = mailbox
                        self._send("* FLAGS (\\Seen)")
                        self._send(f"* {count} EXISTS")
                        self._send("* 0 RECENT")
                        self._send(f"{tag} OK [READ-ONLY] SELECT completed")
                    case "STATUS":
                        runtime.request_imap_sync("STATUS")
                        mailbox, items = _parse_status_args(args)
                        if mailbox is None:
                            self._send(f"{tag} BAD malformed STATUS")
                            continue
                        previous = selected_mailbox
                        count = imap_client.select(mailbox)
                        unseen = storage.count_unseen_topics(account.hey_email, mailbox)
                        if previous is None:
                            selected_mailbox = None
                        else:
                            imap_client.select(previous)
                            selected_mailbox = previous
                        status = _format_status(
                            routes.mailbox_label(mailbox),
                            count,
                            unseen,
                            items,
                        )
                        self._send(status)
                        self._send(f"{tag} OK STATUS completed")
                    case "STORE":
                        if selected_mailbox is None:
                            self._send(f"{tag} NO SELECT a mailbox first")
                            continue
                        sequence_set, mode, flags = _parse_store_command(args)
                        if sequence_set is None or mode is None:
                            self._send(f"{tag} BAD malformed STORE")
                            continue
                        self._apply_seen_store(
                            sequence_set=sequence_set,
                            mode=mode,
                            flags=flags,
                            imap_client=imap_client,
                            storage=storage,
                            hey_email=account.hey_email,
                        )
                        self._send(f"{tag} OK STORE completed")
                    case "FETCH":
                        if selected_mailbox is None:
                            self._send(f"{tag} NO SELECT a mailbox first")
                            continue
                        seq_set, attrs = _parse_fetch_args(args)
                        if seq_set is None or attrs is None:
                            self._send(f"{tag} BAD malformed FETCH")
                            continue
                        self._handle_fetch(
                            tag,
                            seq_set,
                            attrs,
                            imap_client,
                            storage=storage,
                            hey_email=account.hey_email,
                            emit_tagged_ok=True,
                        )
                    case "UID":
                        self._handle_uid(
                            tag,
                            args,
                            imap_client,
                            storage=storage,
                            hey_email=account.hey_email,
                        )
                    case _:
                        self._send(f"{tag} BAD unsupported command")
        except _ClientDisconnected:
            pass
        finally:
            storage.close()
            LOG.info("imaps disconnect peer=%s", peer)

    def _handle_uid(
        self,
        tag: str,
        args: str,
        imap_client: ReadOnlyClient,
        *,
        storage: Storage,
        hey_email: str,
    ) -> None:
        parts = args.split(" ", 2)
        if not parts:
            self._send(f"{tag} BAD malformed UID")
            return
        verb = parts[0].upper()
        match verb:
            case "SEARCH":
                uids = imap_client.search_all()
                line = " ".join(str(uid) for uid in uids)
                self._send(f"* SEARCH {line}" if line else "* SEARCH")
                self._send(f"{tag} OK UID SEARCH completed")
            case "FETCH" if len(parts) == 3:
                self._handle_fetch(
                    tag,
                    parts[1],
                    _parse_fetch_attributes(parts[2]) or [],
                    imap_client,
                    storage=storage,
                    hey_email=hey_email,
                    emit_tagged_ok=False,
                )
                self._send(f"{tag} OK UID FETCH completed")
            case "STORE" if len(parts) == 3:
                uid_set = parts[1]
                mode, flags = _parse_store_args(parts[2])
                if mode is None:
                    self._send(f"{tag} BAD malformed UID STORE")
                    return
                self._apply_seen_store(
                    sequence_set=uid_set,
                    mode=mode,
                    flags=flags,
                    imap_client=imap_client,
                    storage=storage,
                    hey_email=hey_email,
                )
                self._send(f"{tag} OK UID STORE completed")
            case _:
                self._send(f"{tag} BAD malformed UID")

    def _emit_mailbox_listing(
        self,
        command: str,
        tag: str,
        imap_client: ReadOnlyClient,
    ) -> None:
        for item in imap_client.list_mailboxes():
            label = routes.mailbox_label(item.mailbox)
            self._send(f'* {command} (\\HasNoChildren) "/" "{label}"')
        self._send(f"{tag} OK {command} completed")

    def _handle_fetch(
        self,
        tag: str,
        sequence_set: str,
        attributes: list[str],
        imap_client: ReadOnlyClient,
        *,
        storage: Storage,
        hey_email: str,
        emit_tagged_ok: bool,
    ) -> None:
        max_uid = len(imap_client.search_all())
        uids = _expand_uid_set(sequence_set, max_uid=max_uid)

        envelopes = []
        for uid in uids:
            try:
                envelopes.append((uid, imap_client.fetch_envelope(uid)))
            except IndexError:
                continue

        seen_map = storage.topic_seen_map(
            hey_email,
            [envelope.topic_id for _, envelope in envelopes],
        )

        for uid, envelope in envelopes:
            # Eagerly fetch the full RFC 2822 body once; apply fallback if empty.
            raw_text = imap_client.fetch_latest_rfc822(uid)
            if raw_text:
                raw_full: bytes = raw_text.encode("utf-8")
            else:
                raw_full = _body_fallback(envelope)
            internaldate_text = _imap_date(
                _message_date_from_raw(raw_full.decode("utf-8", errors="ignore"))
                or envelope.date
            )
            response_parts: list[str] = []
            literal_parts: list[tuple[int, bytes]] = []
            include_all = "ALL" in attributes
            for attr in attributes:
                key = attr.upper()
                if key in ("UID",) or include_all:
                    response_parts.append(f"UID {uid}")
                if key in ("INTERNALDATE",) or include_all:
                    response_parts.append(f'INTERNALDATE "{internaldate_text}"')
                if key in ("RFC822.SIZE",) or include_all:
                    response_parts.append(f"RFC822.SIZE {len(raw_full)}")
                if key in ("FLAGS",) or include_all:
                    seen = seen_map.get(envelope.topic_id, False)
                    response_parts.append("FLAGS (\\Seen)" if seen else "FLAGS ()")
                if key.startswith(("BODY.PEEK[HEADER", "BODY[HEADER")):
                    header_literal = _extract_raw_headers(raw_full)
                    if header_literal is None:
                        header_literal = _header_from_envelope(envelope).encode("utf-8")
                    response_parts.append(f"BODY[HEADER] {{{len(header_literal)}}}")
                    literal_parts.append((len(response_parts) - 1, header_literal))
                    continue
                if key.startswith(("BODY.PEEK[]", "BODY[]")):
                    body_literal = raw_full
                    response_parts.append(f"BODY[] {{{len(body_literal)}}}")
                    literal_parts.append((len(response_parts) - 1, body_literal))
                    continue
                if key.startswith(("BODY.PEEK[TEXT]", "BODY[TEXT]")):
                    start, length = _parse_fetch_partial(key)
                    text = _extract_raw_body(raw_full)
                    if start is not None and length is not None:
                        text = text[start : start + length]
                    body_literal = text
                    if start is not None and length is not None:
                        response_parts.append(
                            f"BODY[TEXT]<{start}> {{{len(body_literal)}}}"
                        )
                    else:
                        response_parts.append(f"BODY[TEXT] {{{len(body_literal)}}}")
                    literal_parts.append((len(response_parts) - 1, body_literal))
                    continue
                if key.startswith(("BODY.PEEK[", "BODY[")):
                    section = _parse_body_section(key)
                    if section is None:
                        continue
                    body_literal = _resolve_body_section_literal(raw_full, section)
                    start, length = _parse_fetch_partial(key)
                    if start is not None and length is not None:
                        body_literal = body_literal[start : start + length]
                    section_label = section if section else ""
                    if start is not None and length is not None:
                        response_parts.append(
                            f"BODY[{section_label}]<{start}> {{{len(body_literal)}}}"
                        )
                    else:
                        response_parts.append(
                            f"BODY[{section_label}] {{{len(body_literal)}}}"
                        )
                    literal_parts.append((len(response_parts) - 1, body_literal))
                if key == "BODYSTRUCTURE":
                    response_parts.append(_bodystructure(len(raw_full)))

            if not response_parts:
                seen = seen_map.get(envelope.topic_id, False)
                response_parts.append("FLAGS (\\Seen)" if seen else "FLAGS ()")

            if not literal_parts:
                self._send(f"* {uid} FETCH ({' '.join(response_parts)})")
                continue

            literal_by_index = {idx: lit for idx, lit in literal_parts}
            self._write_bytes(f"* {uid} FETCH (".encode())
            for i, part in enumerate(response_parts):
                if i > 0:
                    self._write_bytes(b" ")
                self._write_bytes(part.encode("utf-8"))
                literal = literal_by_index.get(i)
                if literal is not None:
                    self._write_bytes(b"\r\n")
                    self._write_bytes(literal)
            self._write_bytes(b")\r\n")

        if emit_tagged_ok:
            self._send(f"{tag} OK FETCH completed")

    def _send(self, line: str) -> None:
        try:
            self.wfile.write((line + "\r\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError) as exc:
            raise _ClientDisconnected() from exc

    def _write_bytes(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError) as exc:
            raise _ClientDisconnected() from exc

    def _apply_seen_store(
        self,
        *,
        sequence_set: str,
        mode: str,
        flags: set[str],
        imap_client: ReadOnlyClient,
        storage: Storage,
        hey_email: str,
    ) -> None:
        if "\\SEEN" not in flags:
            return
        max_uid = len(imap_client.search_all())
        uids = _expand_uid_set(sequence_set, max_uid=max_uid)
        for uid in uids:
            try:
                topic_id = imap_client.fetch_envelope(uid).topic_id
            except IndexError:
                continue
            if mode.startswith("+"):
                storage.set_topic_seen(hey_email, topic_id, True)
            elif mode.startswith("-"):
                storage.set_topic_seen(hey_email, topic_id, False)
            else:
                storage.set_topic_seen(hey_email, topic_id, True)


def serve(config: ServerConfig, runtime: Runtime) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        certfile=str(config.tls_cert_file),
        keyfile=str(config.tls_key_file),
    )

    smtps = _TlsThreadingServer(
        (config.smtps_host, config.smtps_port),
        _SmtpsHandler,
        context,
        runtime,
    )
    imaps = _TlsThreadingServer(
        (config.imaps_host, config.imaps_port),
        _ImapsHandler,
        context,
        runtime,
    )

    stop_event = threading.Event()

    def shutdown(*_args) -> None:
        if stop_event.is_set():
            return
        LOG.info("server shutdown requested")
        stop_event.set()
        smtps.shutdown()
        imaps.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    smtps_thread = threading.Thread(
        target=smtps.serve_forever, name="smtps", daemon=True
    )
    imaps_thread = threading.Thread(
        target=imaps.serve_forever, name="imaps", daemon=True
    )
    smtps_thread.start()
    imaps_thread.start()

    LOG.info("smtps listening on %s:%d", config.smtps_host, config.smtps_port)
    LOG.info("imaps listening on %s:%d", config.imaps_host, config.imaps_port)

    try:
        while not stop_event.is_set():
            stop_event.wait(0.5)
    finally:
        smtps.server_close()
        imaps.server_close()
        smtps_thread.join(timeout=2)
        imaps_thread.join(timeout=2)


def _decode_plain_auth(token: str) -> tuple[str | None, str | None]:
    raw = _decode_base64_text(token)
    if raw is None:
        return None, None
    parts = raw.split("\x00")
    if len(parts) < 3:
        return None, None
    return parts[-2], parts[-1]


def _decode_base64_text(text: str) -> str | None:
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):  # fmt: skip
        return None


def _auth_ok(account: ResolvedAccount, username: str, password: str) -> bool:
    expected_user = account.hey_email.strip().lower()
    actual_user = username.strip().lower()
    if expected_user != actual_user:
        return False
    expected_password = account.mta_passwd or ""
    return expected_password != "" and password == expected_password


def _parse_imap_line(line: str) -> tuple[str, str, str]:
    parts = line.split(" ", 2)
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[2]


def _imap_login_args(args: str) -> tuple[str | None, str | None]:
    try:
        parts = shlex.split(args)
    except ValueError:
        return None, None
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


def _parse_select_mailbox(args: str) -> routes.Mailbox | None:
    try:
        parts = shlex.split(args)
    except ValueError:
        return None
    if len(parts) != 1:
        return None
    try:
        return routes.parse_mailbox_friendly(parts[0])
    except ValueError:
        return None


def _is_empty_mailbox_arg(args: str) -> bool:
    try:
        parts = shlex.split(args)
    except ValueError:
        return False
    return len(parts) == 1 and parts[0].strip() == ""


def _expand_uid_set(uid_set: str, max_uid: int | None = None) -> list[int]:
    # '*' tokens are silently dropped when max_uid is None; callers that may
    # receive wildcard sets must pass max_uid.
    out: list[int] = []
    for token in uid_set.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            left, right = token.split(":", 1)
            if left == "*" and max_uid is not None:
                left = str(max_uid)
            if right == "*" and max_uid is not None:
                right = str(max_uid)
            if not left.isdigit() or not right.isdigit():
                continue
            start = int(left)
            end = int(right)
            if end < start:
                start, end = end, start
            out.extend(range(start, end + 1))
            continue
        if token == "*" and max_uid is not None:
            out.append(max_uid)
            continue
        if token.isdigit():
            out.append(int(token))
    seen: set[int] = set()
    unique: list[int] = []
    for uid in out:
        if uid in seen:
            continue
        seen.add(uid)
        unique.append(uid)
    if max_uid is None:
        return unique
    return [uid for uid in unique if 1 <= uid <= max_uid]


def _parse_fetch_args(args: str) -> tuple[str | None, list[str] | None]:
    parts = args.strip().split(" ", 1)
    if len(parts) != 2:
        return None, None
    seq_set = parts[0].strip()
    attrs_raw = parts[1].strip()
    attrs = _parse_fetch_attributes(attrs_raw)
    if attrs is None:
        return None, None
    return seq_set, attrs


def _parse_fetch_attributes(raw: str) -> list[str] | None:
    value = raw.strip()
    if not value:
        return None
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    if not value:
        return []
    return [token for token in re.split(r"\s+", value) if token]


def _imap_date(value: str) -> str:
    fallback = "01-Jan-1970 00:00:00 +0000"
    if not value:
        return fallback
    text = value.strip()
    if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}", text):
        return text
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):  # fmt: skip
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.strftime("%d-%b-%Y %H:%M:%S %z")


def _rfc2822_date(value: str) -> str:
    """Return value as an RFC 2822 date string suitable for the Date: header."""
    fallback = "Thu, 01 Jan 1970 00:00:00 +0000"
    if not value:
        return fallback
    text = value.strip()
    # Already RFC 2822 — pass through unchanged.
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):  # fmt: skip
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.strftime("%a, %d %b %Y %H:%M:%S %z")


def _body_fallback(envelope) -> bytes:
    """Synthesise a minimal RFC 2822 message from envelope metadata.

    Used when a topic has no stored message text (e.g. newly discovered topics
    that have not yet been deep-synced).
    """
    header = _header_from_envelope(envelope)
    body = f"[No message body available — subject: {envelope.subject}]\r\n"
    return (header + body).encode("utf-8")


def _header_from_envelope(envelope) -> str:
    to_header = getattr(envelope, "to", None) or ""
    to_line = (
        f"To: {to_header}\r\n" if to_header else "To: undisclosed-recipients:;\r\n"
    )
    return (
        f"From: {envelope.sender}\r\n"
        f"{to_line}"
        f"Subject: {envelope.subject}\r\n"
        f"Date: {_rfc2822_date(envelope.date)}\r\n"
        f"Message-ID: <topic-{envelope.topic_id}@yeh.local>\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
    )


def _extract_raw_headers(raw: bytes) -> bytes | None:
    if not raw:
        return None
    marker = b"\r\n\r\n"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[: idx + len(marker)]
    marker = b"\n\n"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[: idx + len(marker)]
    try:
        msg = Parser(policy=policy.default).parsestr(
            raw.decode("utf-8", errors="ignore")
        )
    except (TypeError, ValueError, LookupError):  # fmt: skip
        return None
    # reconstruct header block from parsed message; ensure CRLF termination
    header_text = "".join(f"{k}: {v}\r\n" for k, v in msg.items()) + "\r\n"
    return header_text.encode("utf-8", errors="surrogateescape")


def _extract_raw_body(raw: bytes) -> bytes:
    if not raw:
        return b""
    marker = b"\r\n\r\n"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[idx + len(marker) :]
    marker = b"\n\n"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[idx + len(marker) :]
    return raw


def _message_date_from_raw(raw: str) -> str | None:
    try:
        msg = Parser(policy=policy.default).parsestr(raw)
    except (TypeError, ValueError, LookupError):  # fmt: skip
        return None
    date = msg.get("Date")
    if date is None:
        return None
    return str(date).strip() or None


def _parse_status_args(args: str) -> tuple[routes.Mailbox | None, list[str]]:
    parts = args.strip().split(" ", 1)
    if len(parts) != 2:
        return None, []
    mailbox = _parse_select_mailbox(parts[0])
    if mailbox is None:
        return None, []
    items_raw = parts[1].strip()
    if items_raw.startswith("(") and items_raw.endswith(")"):
        items_raw = items_raw[1:-1].strip()
    if not items_raw:
        return mailbox, []
    return mailbox, [item.upper() for item in items_raw.split()]


def _format_status(
    mailbox: str, count: int, unseen_count: int, items: list[str]
) -> str:
    values: list[str] = []
    for item in items:
        if item == "MESSAGES":
            values.append(f"MESSAGES {count}")
        elif item == "UIDNEXT":
            values.append(f"UIDNEXT {count + 1}")
        elif item == "UIDVALIDITY":
            values.append("UIDVALIDITY 1")
        elif item == "UNSEEN":
            values.append(f"UNSEEN {unseen_count}")
    if not values:
        values.append(f"MESSAGES {count}")
    return f'* STATUS "{mailbox}" ({" ".join(values)})'


def _parse_store_args(raw: str) -> tuple[str | None, set[str]]:
    parts = raw.strip().split(" ", 1)
    if len(parts) != 2:
        return None, set()
    mode = parts[0].strip().upper()
    flags_raw = parts[1].strip()
    if flags_raw.startswith("(") and flags_raw.endswith(")"):
        flags_raw = flags_raw[1:-1].strip()
    flags = {flag.upper() for flag in flags_raw.split() if flag}
    return mode, flags


def _parse_store_command(args: str) -> tuple[str | None, str | None, set[str]]:
    parts = args.strip().split(" ", 1)
    if len(parts) != 2:
        return None, None, set()
    sequence_set = parts[0].strip()
    mode, flags = _parse_store_args(parts[1])
    return sequence_set, mode, flags


def _parse_fetch_partial(attr: str) -> tuple[int | None, int | None]:
    match = re.search(r"<(\d+)\.(\d+)>", attr)
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_body_section(attr: str) -> str | None:
    match = re.search(r"BODY(?:\.PEEK)?\[(.*?)\]", attr, flags=re.IGNORECASE)
    if match is None:
        return None
    return match.group(1).strip().upper()


def _resolve_body_section_literal(raw: bytes, section: str) -> bytes:
    if section == "":
        return raw
    if section in {"TEXT", "1", "1.TEXT"}:
        return _extract_raw_body(raw)
    if section in {"HEADER", "MIME", "1.MIME"} or section.startswith("HEADER.FIELDS"):
        return _extract_raw_headers(raw) or b""
    return _extract_raw_body(raw)


def _bodystructure(size: int) -> str:
    return f'BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" {size} 1)'


def _sanitize(line: str) -> str:
    upper = line.upper()
    if upper.startswith("AUTH PLAIN"):
        return "AUTH PLAIN <redacted>"
    if upper == "AUTH LOGIN":
        return line
    # IMAP: "<tag> LOGIN user pass" — preserve the tag prefix.
    if upper.startswith("LOGIN ") or " LOGIN " in upper:
        parts = line.split()
        if len(parts) >= 2 and parts[-2].upper() == "LOGIN":
            # bare "LOGIN user pass" with no tag
            return f"LOGIN {parts[-1]} <redacted>"
        # "<tag> LOGIN user pass" or similar
        idx = upper.split().index("LOGIN") if "LOGIN" in upper.split() else -1
        if idx >= 0:
            prefix = " ".join(line.split()[: idx + 2])
            return prefix + " <redacted>"
    return line
