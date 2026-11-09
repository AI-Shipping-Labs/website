"""The CLI reports network failures without exposing a traceback."""

from __future__ import annotations

import sys

import httpx
import pytest

from asl_cli import cli as cli_module


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (httpx.ReadTimeout("request timed out"), "timed out after 30 seconds"),
        (httpx.ConnectError("connection failed"), "API request failed (ConnectError)"),
    ],
)
def test_main_reports_request_failures_without_traceback(
    monkeypatch, capsys, exception, message,
):
    def fail_request():
        raise exception

    monkeypatch.setattr(cli_module, "maybe_update", lambda: False)
    monkeypatch.setattr(cli_module, "cli", fail_request)
    monkeypatch.setattr(sys, "argv", ["asl"])

    with pytest.raises(SystemExit) as exit_info:
        cli_module.main()

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert message in captured.err
    assert "Traceback" not in captured.err
