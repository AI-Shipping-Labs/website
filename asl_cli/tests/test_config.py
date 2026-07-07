"""Token and base-URL resolution tests.

All tests are hermetic: they clear the real environment and patch
``config._find_env_file`` so no real ``.env`` on the developer/CI box can
leak a token into the assertions. Nothing here touches the network.
"""

from __future__ import annotations

import getpass

import pytest

from asl_cli import config


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Strip the real environment and any ambient ``.env`` before each test."""
    for key in ("ASL_API_TOKEN", "API_SHIPPING_LABS_API_TOKEN", "ASL_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_find_env_file", lambda: None)


def _write_env(tmp_path, body: str):
    env_file = tmp_path / ".env"
    env_file.write_text(body, encoding="utf-8")
    return env_file


def test_env_var_wins_over_dotenv(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path, 'API_SHIPPING_LABS_API_TOKEN="from-dotenv"\n')
    monkeypatch.setattr(config, "_find_env_file", lambda: env_file)
    monkeypatch.setenv("ASL_API_TOKEN", "from-env")

    assert config.resolve_staff_token() == "from-env"


def test_dotenv_used_when_env_var_absent_with_quotes_stripped(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path, 'API_SHIPPING_LABS_API_TOKEN="quoted-token"\n')
    monkeypatch.setattr(config, "_find_env_file", lambda: env_file)

    assert config.resolve_staff_token() == "quoted-token"


def test_dotenv_single_quotes_stripped(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path, "API_SHIPPING_LABS_API_TOKEN='single-quoted'\n")
    monkeypatch.setattr(config, "_find_env_file", lambda: env_file)

    assert config.resolve_staff_token() == "single-quoted"


def test_dotenv_comment_lines_ignored(monkeypatch, tmp_path):
    # A commented-out key must not shadow the real one further down the file.
    env_file = _write_env(
        tmp_path,
        "# a comment\n"
        "#API_SHIPPING_LABS_API_TOKEN=commented-out\n"
        "\n"
        "API_SHIPPING_LABS_API_TOKEN=real-token\n",
    )
    monkeypatch.setattr(config, "_find_env_file", lambda: env_file)

    assert config.resolve_staff_token() == "real-token"


def test_non_tty_no_token_raises_naming_env_var(monkeypatch):
    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: False)
    # If resolution ever reached the prompt this would blow up loudly.
    monkeypatch.setattr(
        getpass, "getpass", lambda *a, **k: pytest.fail("prompted despite non-TTY")
    )

    with pytest.raises(RuntimeError) as excinfo:
        config.resolve_staff_token()

    assert "ASL_API_TOKEN" in str(excinfo.value)


def test_error_message_never_contains_a_token(monkeypatch):
    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError) as excinfo:
        config.resolve_staff_token()
    message = str(excinfo.value)
    # The clear error is about configuration, not a leaked secret.
    assert "Token " not in message


def test_base_url_defaults_to_production():
    assert config.resolve_base_url() == "https://aishippinglabs.com"


def test_base_url_override_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("ASL_BASE_URL", "https://staging.example.com/")
    assert config.resolve_base_url() == "https://staging.example.com"


def test_base_url_override_without_trailing_slash(monkeypatch):
    monkeypatch.setenv("ASL_BASE_URL", "https://staging.example.com")
    assert config.resolve_base_url() == "https://staging.example.com"
