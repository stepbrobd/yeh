import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import click

from yeh import routes
from yeh.auth import login as hey_login
from yeh.config import (
    AccountOverrides,
    AppPaths,
    ResolvedAccount,
    effective_config_file,
    load_config_file,
    render_initial_config,
    resolve_account,
)
from yeh.hey import Client
from yeh.mailbox import AuthenticationRequiredError, HeyClient
from yeh.server import Runtime, ServerConfig, serve
from yeh.storage import SessionRecord, Storage
from yeh.tui import EmailDatabaseApp


@dataclass
class CliContext:
    debug: bool


def _init_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.INFO if debug else logging.WARNING)


@click.group()
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, debug: bool) -> None:
    _init_logging(debug)
    ctx.obj = CliContext(debug=debug)


@cli.group()
def config() -> None:
    pass


@config.command("init")
def config_init() -> None:
    paths = AppPaths.discover()
    config_file = effective_config_file(paths)
    if config_file.exists():
        raise click.ClickException(f"config file already exists: {config_file}")

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(render_initial_config(AccountOverrides()), encoding="utf-8")
    click.echo("config_init=ok")
    click.echo(f"config_file={config_file}")


@config.command("show")
def config_show() -> None:
    paths = AppPaths.discover()
    config_file = effective_config_file(paths)
    if not config_file.exists():
        raise click.ClickException(f"config file does not exist: {config_file}")
    click.echo(config_file.read_text(encoding="utf-8"), nl=False)


@cli.group()
def email() -> None:
    pass


@cli.command("server")
@click.option("--hey-email", type=str)
@click.option("--smtps-host", type=str, default="127.0.0.1", show_default=True)
@click.option("--smtps-port", type=int, default=8465, show_default=True)
@click.option("--imaps-host", type=str, default="127.0.0.1", show_default=True)
@click.option("--imaps-port", type=int, default=8993, show_default=True)
@click.option(
    "--imap-sync-min-interval-seconds",
    type=float,
    default=30.0,
    show_default=True,
    help="Minimum seconds between IMAP-triggered HEY sync runs",
)
@click.option(
    "--imap-sync-max-pages",
    type=int,
    default=3,
    show_default=True,
    help="Page cap for deep refresh during IMAP-triggered sync",
)
@click.option(
    "--imap-sync-workers",
    type=int,
    default=2,
    show_default=True,
    help="Worker count for IMAP-triggered refresh",
)
@click.option(
    "--tls-cert-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--tls-key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.pass_context
def server_cmd(
    ctx: click.Context,
    hey_email: str | None,
    smtps_host: str,
    smtps_port: int,
    imaps_host: str,
    imaps_port: int,
    imap_sync_min_interval_seconds: float,
    imap_sync_max_pages: int,
    imap_sync_workers: int,
    tls_cert_file: Path,
    tls_key_file: Path,
) -> None:
    paths, account = _resolve_account(hey_email)
    if not account.mta_passwd:
        raise click.ClickException(
            "missing mta_passwd in env/config; required for SMTP/IMAP LOGIN"
        )
    runtime = Runtime(
        paths=paths,
        account=account,
        debug=ctx.obj.debug,
        auth_lock=threading.Lock(),
        sync_lock=threading.Lock(),
        imap_sync_min_interval_seconds=max(0.0, imap_sync_min_interval_seconds),
        imap_sync_max_pages=max(1, imap_sync_max_pages),
        imap_sync_workers=max(1, imap_sync_workers),
    )
    config = ServerConfig(
        smtps_host=smtps_host,
        smtps_port=smtps_port,
        imaps_host=imaps_host,
        imaps_port=imaps_port,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
    )
    click.echo("server=starting")
    click.echo(f"hey_email={account.hey_email}")
    click.echo(f"smtps={smtps_host}:{smtps_port}")
    click.echo(f"imaps={imaps_host}:{imaps_port}")
    serve(config=config, runtime=runtime)


@email.command("import")
@click.argument(
    "mbox_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--hey-email", type=str)
@click.option(
    "--mailbox",
    type=click.Choice(
        [x.value for x in sorted(routes.MAILBOX_PATHS, key=lambda x: x.value)],
        case_sensitive=False,
    ),
    default="everything",
    show_default=True,
)
def email_import(mbox_path: Path, hey_email: str | None, mailbox: str) -> None:
    paths, account = _resolve_account(hey_email)

    storage = Storage(paths.account_db_path(account.hey_email))
    try:
        api = Client(storage=storage, hey_email=account.hey_email, web=None)
        mb = routes.parse_mailbox(mailbox)
        stats = api.import_mbox(mbox_path, mb)
    finally:
        storage.close()

    click.echo("email_import=ok")
    click.echo(f"hey_email={account.hey_email}")
    click.echo(f"mbox_path={mbox_path.resolve()}")
    click.echo(f"mailbox={stats.mailbox.value}")
    click.echo(f"messages_total={stats.total_messages}")
    click.echo(f"messages_imported={stats.imported_messages}")
    click.echo(f"messages_updated={stats.updated_messages}")
    click.echo(f"topics={stats.topics}")


@email.command("tui")
@click.option("--hey-email", type=str)
def email_tui(hey_email: str | None) -> None:
    paths, account = _resolve_account(hey_email)

    storage = Storage(paths.account_db_path(account.hey_email))
    try:
        api = Client(storage=storage, hey_email=account.hey_email, web=None)

        app = EmailDatabaseApp(
            load_mailboxes=lambda: api.mailboxes(account.hey_host),
            load_topics=lambda mailbox, limit, offset: api.topics(
                mailbox, limit, offset
            ),
            load_thread=lambda topic_id: api.thread(topic_id),
        )
        app.run()
    finally:
        storage.close()


@email.command("refresh")
@click.option("--hey-email", type=str)
@click.option(
    "--mailbox",
    type=click.Choice(
        [x.value for x in sorted(routes.MAILBOX_PATHS, key=lambda x: x.value)],
        case_sensitive=False,
    ),
    default="everything",
    show_default=True,
)
@click.option(
    "--max-pages",
    type=int,
    default=20,
    show_default=True,
    help="Page cap to keep refresh bounded",
)
@click.option(
    "--workers",
    type=int,
    default=4,
    show_default=True,
    help="Concurrent message fetch workers",
)
@click.pass_context
def email_refresh(
    ctx: click.Context,
    hey_email: str | None,
    mailbox: str,
    max_pages: int | None,
    workers: int,
) -> None:
    paths, account = _resolve_account(hey_email)

    storage = Storage(paths.account_db_path(account.hey_email))
    try:
        session = storage.load_session(account.hey_email)
        if session is None:
            session = _reauth_and_load_session(storage, account, ctx.obj.debug)

        client = HeyClient(account=account, session=session)
        try:
            api = Client(storage=storage, hey_email=account.hey_email, web=client)
            mb = routes.parse_mailbox(mailbox)
            try:
                result = api.refresh(
                    mb,
                    max_pages=max_pages,
                    progress=lambda line: click.echo(line),
                    workers=max(1, workers),
                )
            except AuthenticationRequiredError:
                session = _reauth_and_load_session(storage, account, ctx.obj.debug)
                client.replace_session(session)
                result = api.refresh(
                    mb,
                    max_pages=max_pages,
                    progress=lambda line: click.echo(line),
                    workers=max(1, workers),
                )

            cookie_jar_json, csrf_token, final_url = client.export_session_state()
            storage.save_session(
                hey_email=account.hey_email,
                cookie_jar_json=cookie_jar_json,
                csrf_token=csrf_token,
                final_url=final_url,
            )
        finally:
            client.close()
    finally:
        storage.close()

    click.echo("email_refresh=ok")
    click.echo(f"hey_email={account.hey_email}")
    click.echo(f"mailbox={result.mailbox.value}")
    click.echo(f"pages_scanned={result.pages_scanned}")
    click.echo(f"topics_seen={result.topics_seen}")
    click.echo(f"topics_new={result.new_topics}")
    click.echo(f"messages_synced={result.messages_synced}")
    click.echo(f"messages_updated={result.messages_updated}")


def _reauth_and_load_session(
    storage: Storage, account: ResolvedAccount, debug: bool
) -> SessionRecord:
    result = hey_login(account=account, debug=debug, show_browser=False)
    storage.save_session(
        hey_email=account.hey_email,
        cookie_jar_json=result.cookie_jar_json,
        csrf_token=result.csrf_token,
        final_url=result.final_url,
    )
    session = storage.load_session(account.hey_email)
    if session is None:
        raise click.ClickException("failed to persist authenticated session")
    return session


def _resolve_account(hey_email: str | None) -> tuple[AppPaths, ResolvedAccount]:
    paths = AppPaths.discover()
    cfg = load_config_file(effective_config_file(paths))
    try:
        account = resolve_account(
            cfg=cfg,
            cli_hey_email=hey_email,
            overrides=AccountOverrides(),
            require_email=True,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return paths, account


if __name__ == "__main__":
    cli()
