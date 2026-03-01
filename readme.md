# YEH <-> HEY

`yeh` is a Python CLI that authenticates to HEY web (with Selenium), fetches
mailbox pages, and persists thread/message content for local tooling.

Current scope:

- Click-based CLI.
- Config/env account resolution.
- Selenium login with TOTP fallback.
- Session persistence in sqlite.
- Textual inbox listing UI.
- Automatic re-auth when a fetch detects expired auth.
- Topic/message sync into sqlite via HEY plaintext message endpoints.

This project is still pre-IMAP/SMTP server; it currently focuses on reliable HEY
web auth/session handling and mailbox fetch/parsing.

## Installation

Requires Python `>=3.14` and `uv`.

```sh
uv sync
uv run python -m yeh --help
```

Chrome is required for Selenium auth (ChromeDriver is managed by Selenium
Manager automatically).

## Commands

Public end-user commands:

- `yeh config init`
- `yeh config show`
- `yeh email tui`
- `yeh email import <mbox_path>`
- `yeh email refresh`
- `yeh email index`
- `yeh server`

Equivalent module form:

```sh
uv run python -m yeh config show
uv run python -m yeh email tui
uv run python -m yeh email import /path/to/HEY-export.mbox
uv run python -m yeh email refresh --mailbox everything
uv run python -m yeh email index
uv run python -m yeh server --tls-cert-file ./cert.pem --tls-key-file ./key.pem
```

Start SMTPS + IMAPS listeners (implicit TLS, logs to stdout):

```sh
uv run python -m yeh server \
  --smtps-host 127.0.0.1 --smtps-port 8465 \
  --imaps-host 127.0.0.1 --imaps-port 8993 \
  --imap-sync-min-interval-seconds 30 \
  --imap-sync-max-pages 3 \
  --imap-sync-index-pages-per-mailbox 3 \
  --tls-cert-file ./cert.pem --tls-key-file ./key.pem
```

IMAP-triggered sync behavior:

- `NOOP`, `CHECK`, `SELECT`, and `STATUS` can trigger bounded background sync.
- Sync fetches latest topics + message content (`refresh --deep`) and mailbox
  membership reindex (`index`) with cooldown via
  `--imap-sync-min-interval-seconds`.

Auth notes:

- `mta_passwd` is required in config/env for SMTP/IMAP login.
- Username is your configured `hey_email`.

## Configuration

Config path uses XDG defaults:

- Config: `XDG_CONFIG_HOME/yeh/config.toml`
- Data: `XDG_DATA_HOME/yeh/`
- Session DB: `XDG_DATA_HOME/yeh/<hey_email>.sqlite3`

Override config file path:

- `YEH_CONFIG_PATH=/path/to/config.toml`

Config format:

```toml
[[accounts]]
hey_email = "you@hey.com"
hey_passwd = "your-password"
hey_totp = "BASE32_TOTP_SECRET" # optional unless account requires TOTP
mta_passwd = "client-password"
```

Supported env vars:

- `YEH_HEY_EMAIL`
- `YEH_HEY_PASSWD`
- `YEH_HEY_TOTP`
- `YEH_MTA_PASSWD`
- `YEH_HEY_HOST` (default: `app.hey.com`)

Precedence is:

1. CLI flags
2. env vars
3. config file

Notes:

- `config init` creates the config file in the resolved config directory and
  creates parent directories when needed.
- `config show` reads and prints the resolved config file.

## Authentication Model

Authentication is designed to be transparent to users:

- `email refresh`/`email index` first try existing saved session cookies.
- If there is no session, they log in automatically.
- If a fetch redirects to `/sign_in`, they re-authenticate and retry.
- Session state (cookies, csrf token, last URL) is refreshed and persisted after
  successful fetches.

The implementation minimizes re-auth while preserving correctness by preferring
session reuse and only logging in again on explicit auth failure.

## Email TUI (Textual)

Launch the local database UI:

```sh
uv run python -m yeh email tui
```

Key bindings:

- `q`: quit
- `r`: refresh database view
- `[`: previous mailbox filter
- `]`: next mailbox filter
- `a`: show all mailboxes
- `p`: previous page
- `n`: next page

For HTTP-to-local synchronization helpers:

- `email refresh` updates topic/message data for one mailbox (or `everything`)
  and defaults to bounded, metadata-only refresh.
- `email index` scans all mailbox routes and records mailbox memberships for
  each topic without deep message refetch.

Recommended refresh flow:

- Fast metadata refresh:
  `uv run python -m yeh email refresh --mailbox everything`
- Deep message refresh:
  `uv run python -m yeh email refresh --mailbox everything --deep`
- Larger index pass:
  `uv run python -m yeh email index --max-pages-per-mailbox 100`

Parsed fields per thread:

- sender
- subject
- snippet
- time
- topic URL

During list/refresh/pagination, each fetched topic is synchronized to sqlite:

- Topic records are stored in `topics`.
- Message records are stored in `messages`.
- Message content is fetched from `/messages/<id>.text` and refreshed when the
  message content changes.

Import HEY export `.mbox` into sqlite:

```sh
uv run python -m yeh email import ./HEY-emails-you@hey.com.mbox
```

Options:

- `--mailbox` (default: `everything`) sets mailbox association for imported
  topics.
- `--hey-email` selects account/db when multiple accounts are configured.

Supported mailbox keys:

- `imbox`
- `feedbox`
- `paper_trail`
- `drafts`
- `sent`
- `previously_seen`
- `screened_out`
- `spam`
- `trash`
- `everything`

## Development

Formatting/linting/checks:

```sh
nix fmt
uv run ruff check yeh
uv run ruff format --check yeh
```

## Roadmap

- Improve mailbox parsing coverage across HEY views.
- Add robust send flow support.
- Build IMAP/SMTP proxy layers on top of persisted session/mailbox primitives.

## License

Licensed under the [MIT License](license.txt). Use at your own risk.
