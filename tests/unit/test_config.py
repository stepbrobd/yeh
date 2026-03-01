from yeh.config import (
    AccountConfig,
    AccountOverrides,
    FileConfig,
    canonicalize_email,
    resolve_account,
)


def test_canonicalize_email_normalizes_case_and_space() -> None:
    assert canonicalize_email("  User@Hey.Com ") == "user@hey.com"


def test_resolve_account_prefers_override_then_env_config() -> None:
    cfg = FileConfig(
        accounts=[
            AccountConfig(
                hey_email="user@hey.com",
                mta_passwd="cfg-mta",
                hey_passwd="cfg-hey",
            )
        ]
    )
    out = resolve_account(
        cfg=cfg,
        cli_hey_email="user@hey.com",
        overrides=AccountOverrides(mta_passwd="override-mta"),
        require_email=True,
    )
    assert out.hey_email == "user@hey.com"
    assert out.mta_passwd == "override-mta"
    assert out.hey_passwd == "cfg-hey"
