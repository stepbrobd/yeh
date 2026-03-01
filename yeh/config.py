import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

from yeh import routes


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    config_file: Path

    @classmethod
    def discover(cls) -> AppPaths:
        dirs = PlatformDirs(appname="yeh", appauthor=False)
        config_dir = Path(dirs.user_config_dir)
        data_dir = Path(dirs.user_data_dir)
        return cls(
            config_dir=config_dir,
            data_dir=data_dir,
            config_file=config_dir / "config.toml",
        )

    def ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def account_db_path(self, hey_email: str) -> Path:
        return self.data_dir / f"{canonicalize_email(hey_email)}.sqlite3"


@dataclass(frozen=True)
class AccountConfig:
    hey_email: str | None = None
    mta_passwd: str | None = None
    hey_passwd: str | None = None
    hey_totp: str | None = None
    hey_csrf_cookie: str | None = None
    hey_same_site_token: str | None = None
    hey_authenticity_cookie: str | None = None


@dataclass(frozen=True)
class FileConfig:
    accounts: list[AccountConfig]


@dataclass(frozen=True)
class AccountOverrides:
    hey_email: str | None = None
    mta_passwd: str | None = None
    hey_passwd: str | None = None
    hey_totp: str | None = None
    hey_csrf_cookie: str | None = None
    hey_same_site_token: str | None = None
    hey_authenticity_cookie: str | None = None


@dataclass(frozen=True)
class ResolvedAccount:
    hey_email: str
    mta_passwd: str | None
    hey_passwd: str | None
    hey_totp: str | None
    hey_csrf_cookie: str | None
    hey_same_site_token: str | None
    hey_authenticity_cookie: str | None
    hey_host: str


def effective_config_file(paths: AppPaths) -> Path:
    return Path(os.environ.get("YEH_CONFIG_PATH", paths.config_file))


def load_config_file(default_path: Path) -> FileConfig:
    path = Path(os.environ.get("YEH_CONFIG_PATH", str(default_path)))
    if not path.exists():
        return FileConfig(accounts=[])

    with path.open("rb") as f:
        data = tomllib.load(f)

    raw_accounts = data.get("accounts", [])
    accounts: list[AccountConfig] = []
    for item in raw_accounts:
        if not isinstance(item, dict):
            continue
        accounts.append(
            AccountConfig(
                hey_email=_as_opt_str(item.get("hey_email")),
                mta_passwd=_as_opt_str(item.get("mta_passwd")),
                hey_passwd=_as_opt_str(item.get("hey_passwd")),
                hey_totp=_as_opt_str(item.get("hey_totp")),
                hey_csrf_cookie=_as_opt_str(item.get("hey_csrf_cookie")),
                hey_same_site_token=_as_opt_str(item.get("hey_same_site_token")),
                hey_authenticity_cookie=_as_opt_str(
                    item.get("hey_authenticity_cookie")
                ),
            )
        )
    return FileConfig(accounts=accounts)


def resolve_account(
    cfg: FileConfig,
    cli_hey_email: str | None,
    overrides: AccountOverrides,
    require_email: bool,
) -> ResolvedAccount:
    env = _read_env()
    selected_email = first_some(overrides.hey_email, cli_hey_email, env.hey_email)
    hey_email = _select_hey_email(cfg, selected_email, require_email)
    configured = _lookup_account(cfg, hey_email)

    mta_passwd = first_some(
        overrides.mta_passwd,
        env.mta_passwd,
        configured.mta_passwd if configured else None,
    )
    hey_passwd = first_some(
        overrides.hey_passwd,
        env.hey_passwd,
        configured.hey_passwd if configured else None,
    )
    hey_totp = first_some(
        overrides.hey_totp, env.hey_totp, configured.hey_totp if configured else None
    )
    hey_csrf_cookie = first_some(
        overrides.hey_csrf_cookie,
        env.hey_csrf_cookie,
        configured.hey_csrf_cookie if configured else None,
    )
    hey_same_site_token = first_some(
        overrides.hey_same_site_token,
        env.hey_same_site_token,
        configured.hey_same_site_token if configured else None,
    )
    hey_authenticity_cookie = first_some(
        overrides.hey_authenticity_cookie,
        env.hey_authenticity_cookie,
        configured.hey_authenticity_cookie if configured else None,
    )

    hey_host = (env.hey_host or routes.HOST).strip()
    return ResolvedAccount(
        hey_email=hey_email,
        mta_passwd=mta_passwd,
        hey_passwd=hey_passwd,
        hey_totp=hey_totp,
        hey_csrf_cookie=hey_csrf_cookie,
        hey_same_site_token=hey_same_site_token,
        hey_authenticity_cookie=hey_authenticity_cookie,
        hey_host=hey_host,
    )


def render_initial_config(account: AccountOverrides) -> str:
    email = account.hey_email or "you@example.com"
    hey_passwd = account.hey_passwd or "change-me"
    mta_passwd = account.mta_passwd or "change-me"
    hey_totp = account.hey_totp or ""

    lines = [
        "[[accounts]]",
        f'hey_email = "{email}"',
        f'hey_passwd = "{hey_passwd}"',
    ]
    if hey_totp:
        lines.append(f'hey_totp = "{hey_totp}"')
    lines.append(f'mta_passwd = "{mta_passwd}"')
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _EnvConfig:
    hey_email: str | None
    mta_passwd: str | None
    hey_passwd: str | None
    hey_totp: str | None
    hey_csrf_cookie: str | None
    hey_same_site_token: str | None
    hey_authenticity_cookie: str | None
    hey_host: str | None


def _read_env() -> _EnvConfig:
    return _EnvConfig(
        hey_email=os.environ.get("YEH_HEY_EMAIL"),
        mta_passwd=os.environ.get("YEH_MTA_PASSWD"),
        hey_passwd=os.environ.get("YEH_HEY_PASSWD"),
        hey_totp=os.environ.get("YEH_HEY_TOTP"),
        hey_csrf_cookie=os.environ.get("YEH_HEY_CSRF_COOKIE"),
        hey_same_site_token=os.environ.get("YEH_HEY_SAME_SITE_TOKEN"),
        hey_authenticity_cookie=os.environ.get("YEH_HEY_AUTHENTICITY_COOKIE"),
        hey_host=os.environ.get("YEH_HEY_HOST"),
    )


def _select_hey_email(
    cfg: FileConfig, selected_hey_email: str | None, require_email: bool
) -> str:
    if selected_hey_email:
        return canonicalize_email(selected_hey_email)

    if len(cfg.accounts) == 1:
        only = cfg.accounts[0].hey_email
        if not only:
            raise ValueError("single configured account is missing hey_email")
        return canonicalize_email(only)

    if require_email:
        raise ValueError(
            "no account selected; provide --hey-email or set YEH_HEY_EMAIL"
        )

    return ""


def _lookup_account(cfg: FileConfig, email: str) -> AccountConfig | None:
    target = canonicalize_email(email)
    for account in cfg.accounts:
        if canonicalize_email(account.hey_email or "") == target:
            return account
    return None


def canonicalize_email(email: str) -> str:
    return email.strip().lower()


def first_some(*values: str | None) -> str | None:
    for value in values:
        if value is not None:
            return value
    return None


def _as_opt_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
