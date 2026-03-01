from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from yeh import routes
from yeh.config import AppPaths, ResolvedAccount
from yeh.server import _ImapsHandler, _SmtpsHandler
from yeh.storage import Storage


def _account() -> ResolvedAccount:
    return ResolvedAccount(
        hey_email="user@hey.com",
        mta_passwd="secret",
        hey_passwd=None,
        hey_totp=None,
        hey_csrf_cookie=None,
        hey_same_site_token=None,
        hey_authenticity_cookie=None,
        hey_host="app.hey.com",
    )


def _app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(
        config_dir=tmp_path,
        data_dir=tmp_path,
        config_file=tmp_path / "config.toml",
    )


def _seed_db(tmp_path: Path) -> None:
    db_path = tmp_path / "user@hey.com.sqlite3"
    s = Storage(db_path)
    try:
        s.upsert_topic(
            hey_email="user@hey.com",
            topic_id="t1",
            topic_url="https://app.hey.com/topics/t1",
            sender="Alice <alice@example.com>",
            subject="Hi",
            snippet="hello",
            when_text="Sun, 1 Mar 2026 00:00:00 +0100",
            summary_hash="h1",
        )
        s.assign_topic_mailbox(
            "user@hey.com",
            "t1",
            routes.Mailbox.IMBOX,
            "https://app.hey.com/imbox",
        )
        s.upsert_message_text(
            "user@hey.com",
            "t1",
            "m1",
            "https://app.hey.com/messages/m1.text",
            "From: Alice <alice@example.com>\r\n"
            "Reply-To: reply@example.com\r\n"
            "Subject: Hi\r\n"
            "Date: Sun, 1 Mar 2026 00:00:00 +0100\r\n"
            "\r\n"
            "hello body",
        )
    finally:
        s.close()


def _run_handler(handler_cls, runtime, transcript: str) -> str:
    h = object.__new__(handler_cls)
    h.server = SimpleNamespace(runtime=runtime)
    h.client_address = ("127.0.0.1", 50000)
    h.rfile = BytesIO(transcript.encode("utf-8"))
    h.wfile = BytesIO()
    h.handle()
    return h.wfile.getvalue().decode("utf-8", errors="ignore")


def test_imap_session_basic_flow_with_real_temp_db(tmp_path: Path) -> None:
    _seed_db(tmp_path)
    runtime = SimpleNamespace(
        account=_account(),
        paths=_app_paths(tmp_path),
        request_imap_sync=lambda _reason: None,
    )
    out = _run_handler(
        _ImapsHandler,
        runtime,
        "A1 CAPABILITY\r\n"
        "A2 LOGIN user@hey.com secret\r\n"
        'A3 LIST "" *\r\n'
        "A4 SELECT INBOX\r\n"
        "A5 FETCH 1 (UID FLAGS BODY.PEEK[HEADER])\r\n"
        "A6 STORE 1 +FLAGS.SILENT (\\Seen)\r\n"
        'A7 STATUS "Inbox" (MESSAGES UNSEEN)\r\n'
        'A8 SELECT ""\r\n'
        "A9 EXPUNGE\r\n"
        "A10 LOGOUT\r\n",
    )
    assert "A2 OK LOGIN completed" in out
    assert "* LIST" in out
    assert "A4 OK [READ-ONLY] SELECT completed" in out
    assert "From: Alice <alice@example.com>" in out
    assert "A6 OK STORE completed" in out
    assert "A7 OK STATUS completed" in out
    assert "A9 OK EXPUNGE completed" in out


def test_imap_list_emits_all_mailboxes_and_triggers_sync(tmp_path: Path) -> None:
    """LIST must advertise all known mailboxes and trigger a background sync."""
    _seed_db(tmp_path)
    sync_reasons: list[str] = []
    runtime = SimpleNamespace(
        account=_account(),
        paths=_app_paths(tmp_path),
        request_imap_sync=lambda reason: sync_reasons.append(reason),
    )
    out = _run_handler(
        _ImapsHandler,
        runtime,
        'A1 LOGIN user@hey.com secret\r\nA2 LIST "" *\r\nA3 LOGOUT\r\n',
    )
    # All labels from MAILBOX_LABELS must appear in the LIST response.
    from yeh import routes

    for label in routes.MAILBOX_LABELS.values():
        assert f'"{label}"' in out, f"missing mailbox label {label!r} in LIST output"

    # Inbox label maps to IMBOX.
    assert '"Inbox"' in out

    # Background sync was requested.
    assert "LIST" in sync_reasons

    assert "A2 OK LIST completed" in out


def test_imap_lsub_emits_all_mailboxes_and_triggers_sync(tmp_path: Path) -> None:
    """LSUB behaves identically to LIST for mailbox advertising and sync."""
    _seed_db(tmp_path)
    sync_reasons: list[str] = []
    runtime = SimpleNamespace(
        account=_account(),
        paths=_app_paths(tmp_path),
        request_imap_sync=lambda reason: sync_reasons.append(reason),
    )
    out = _run_handler(
        _ImapsHandler,
        runtime,
        'A1 LOGIN user@hey.com secret\r\nA2 LSUB "" *\r\nA3 LOGOUT\r\n',
    )
    from yeh import routes

    for label in routes.MAILBOX_LABELS.values():
        assert f'"{label}"' in out, f"missing mailbox label {label!r} in LSUB output"

    assert "LSUB" in sync_reasons
    assert "A2 OK LSUB completed" in out

    runtime = SimpleNamespace(
        account=_account(),
        submit_message=lambda _action: SimpleNamespace(
            ok=True,
            location="/topics/sent",
            draft_id="d1",
            status_code=302,
            reason="send_commit",
        ),
    )
    out = _run_handler(
        _SmtpsHandler,
        runtime,
        "EHLO localhost\r\n"
        "AUTH PLAIN AHVzZXJAaGV5LmNvbQBzZWNyZXQ=\r\n"
        "MAIL FROM:<user@hey.com>\r\n"
        "RCPT TO:<dest@example.com>\r\n"
        "DATA\r\n"
        "Subject: hi\r\n"
        "\r\n"
        "hello\r\n"
        ".\r\n"
        "QUIT\r\n",
    )
    assert "235 Authentication successful" in out
    assert "250 Message accepted" in out
    assert "221 Bye" in out
