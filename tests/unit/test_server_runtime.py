import threading
from pathlib import Path

import yeh.server as server_mod
from yeh.config import AppPaths, ResolvedAccount
from yeh.storage import SessionRecord


def _runtime(tmp_path: Path) -> server_mod.Runtime:
    paths = AppPaths(
        config_dir=tmp_path,
        data_dir=tmp_path,
        config_file=tmp_path / "config.toml",
    )
    account = ResolvedAccount(
        hey_email="user@hey.com",
        mta_passwd="mta",
        hey_passwd=None,
        hey_totp=None,
        hey_csrf_cookie=None,
        hey_same_site_token=None,
        hey_authenticity_cookie=None,
        hey_host="app.hey.com",
    )
    return server_mod.Runtime(
        paths=paths,
        account=account,
        debug=False,
        auth_lock=threading.Lock(),
        sync_lock=threading.Lock(),
        imap_sync_min_interval_seconds=60.0,
        imap_sync_max_pages=1,
        imap_sync_workers=1,
    )


def test_request_imap_sync_respects_in_progress_and_cooldown(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    started: list[str] = []

    class _FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            self._target = target
            self._args = args

        def start(self) -> None:
            started.append(self._args[0])

    monkeypatch.setattr(server_mod.threading, "Thread", _FakeThread)

    runtime.request_imap_sync("SELECT")
    assert started == ["SELECT"]

    runtime.imap_sync_in_progress = True
    runtime.request_imap_sync("NOOP")
    assert started == ["SELECT"]

    runtime.imap_sync_in_progress = False
    runtime.request_imap_sync("STATUS")
    assert started == ["SELECT"]

    runtime.request_imap_sync("CHECK", force=True)
    assert started == ["SELECT", "CHECK"]


def test_sync_worker_runs_refresh_and_index(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    calls: list[str] = []

    class _Storage:
        def __init__(self, _db_path: Path):
            pass

        def load_session(self, _email: str):
            return SessionRecord("{}", "csrf", "https://app.hey.com", "now")

        def save_session(self, **_kwargs):
            calls.append("save_session")

        def close(self):
            calls.append("storage_close")

    class _Web:
        def __init__(self, **_kwargs):
            pass

        def export_session_state(self):
            return "{}", "csrf", "https://app.hey.com"

        def close(self):
            calls.append("web_close")

    class _Api:
        def __init__(self, **_kwargs):
            pass

        def refresh(self, *_args, **_kwargs):
            calls.append("refresh")

        def refresh_all(self, *_args, **_kwargs):
            calls.append("refresh_all")

    monkeypatch.setattr(server_mod, "Storage", _Storage)
    monkeypatch.setattr(server_mod, "HeyClient", _Web)
    monkeypatch.setattr(server_mod, "Client", _Api)

    runtime.imap_sync_in_progress = True
    runtime._sync_mail_for_imap_worker("SELECT")
    assert calls[0] == "refresh_all"
    assert "save_session" in calls
    assert "web_close" in calls
    assert "storage_close" in calls
    assert runtime.imap_sync_in_progress is False


def test_sync_mail_for_imap_now_calls_worker(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    called: list[str] = []

    def fake_worker(reason: str) -> None:
        called.append(reason)
        runtime.imap_sync_in_progress = False

    monkeypatch.setattr(runtime, "_sync_mail_for_imap_worker", fake_worker)
    runtime.sync_mail_for_imap_now("CHECK")
    assert called == ["CHECK"]
