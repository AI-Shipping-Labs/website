"""Offline CLI contracts for ``asl contacts export`` (#1733)."""

from __future__ import annotations

import csv
import io
import json
import sys
import warnings

import pytest
from asl_cli.cli import cli, main
from asl_cli.client import APIError
from asl_cli.commands import contacts as contacts_module
from click.testing import CliRunner

CSV_HEADER = (
    "email,first_name,last_name,tags,tier,email_verified,unsubscribed,"
    "date_joined,last_login,stripe_customer_id,subscription_id,slack_member,"
    "slack_checked_at"
)
CSV_BODY = (
    f"{CSV_HEADER}\r\n"
    "alexey@example.com,Alexey,Grigorev,\"sprint:may-2026,workshop\",main,"
    "true,false,2026-01-02T10:00:00+00:00,2026-09-01T08:00:00+00:00,"
    "cus_1,sub_1,true,2026-09-10T00:00:00+00:00\r\n"
    "mueller@example.com,Jan,Müller,newsletter,basic,"
    "true,false,2026-02-03T11:00:00+00:00,,,,false,\r\n"
)

JSON_PAYLOAD = {
    "contacts": [
        {
            "email": "alexey@example.com", "first_name": "Alexey",
            "last_name": "Grigorev", "tier": "main",
            "tags": ["sprint:may-2026"], "unsubscribed": False,
        },
        {
            "email": "mueller@example.com", "first_name": "Jan",
            "last_name": "Müller", "tier": "basic",
            "tags": ["newsletter"], "unsubscribed": True,
        },
    ],
}


class RecordingClient:
    """Returns ``raw_result`` for raw calls and ``result`` otherwise."""

    def __init__(self, result=None, raw_result=None):
        self.calls = []
        self.result = result if result is not None else {"contacts": []}
        self.raw_result = raw_result if raw_result is not None else CSV_BODY

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return self.raw_result if kwargs.get("raw") else self.result


def stdout_text(result):
    """Decoded stdout with the server's line terminators intact.

    ``Result.stdout`` rewrites CRLF to LF, which would hide whether the CSV
    body reached the operator's file unchanged.
    """
    return result.stdout_bytes.decode("utf-8")


def invoke(monkeypatch, args, client=None):
    client = client or RecordingClient()
    monkeypatch.setattr(contacts_module, "get_client", lambda: client)
    result = CliRunner().invoke(cli, ["contacts", "export", *args])
    return client, result


@pytest.mark.parametrize("flag", ["--format", "-f"])
def test_csv_requests_the_raw_body_and_writes_it_verbatim(monkeypatch, flag):
    client, result = invoke(monkeypatch, [flag, "csv"])

    assert result.exit_code == 0, result.output
    assert client.calls == [
        ("GET", "/api/contacts/export", {"params": {"format": "csv"}, "raw": True}),
    ]
    # ``result.stdout`` rewrites CRLF, so assert on the raw stdout bytes.
    assert stdout_text(result) == CSV_BODY
    assert result.stderr == ""
    # The bytes the server produced: no JSON quoting, no escape sequences.
    assert not stdout_text(result).startswith('"')
    assert "\\r\\n" not in stdout_text(result)
    assert '\\"' not in stdout_text(result)


def test_csv_output_parses_directly_and_ends_with_one_newline(monkeypatch):
    _client, result = invoke(monkeypatch, ["-f", "csv"])
    written = stdout_text(result)

    rows = list(csv.reader(io.StringIO(written, newline="")))
    assert rows[0] == CSV_HEADER.split(",")
    assert len(rows) == 3
    assert rows[1][0] == "alexey@example.com"
    assert rows[1][3] == "sprint:may-2026,workshop"
    assert rows[2][0] == "mueller@example.com"
    # The server's CRLF terminators survive, and no extra blank line is added.
    assert written.endswith("\r\n")
    assert not written.endswith("\r\n\n")


def test_csv_body_without_trailing_newline_gets_exactly_one(monkeypatch):
    client = RecordingClient(raw_result=f"{CSV_HEADER}\r\nalexey@example.com,A,G")
    _client, result = invoke(monkeypatch, ["-f", "csv"], client=client)
    written = stdout_text(result)

    assert written.endswith("alexey@example.com,A,G\n")
    assert not written.endswith("\n\n")


def test_csv_preserves_non_ascii_characters(monkeypatch):
    _client, result = invoke(monkeypatch, ["-f", "csv"])
    written = stdout_text(result)

    assert "Müller" in written
    assert "�" not in written
    assert "\\u" not in written


def test_help_lists_one_format_option_with_all_four_choices(monkeypatch):
    runner = CliRunner()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        result = runner.invoke(
            cli, ["contacts", "export", "--help"], catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert result.output.count("--format") == 1
    assert "-f, --format [json|table|raw|csv]" in result.output


def test_default_json_emits_the_envelope_without_a_csv_param(monkeypatch):
    client, result = invoke(
        monkeypatch, [], client=RecordingClient(result=JSON_PAYLOAD),
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/contacts/export", {})]
    assert json.loads(result.output) == JSON_PAYLOAD
    assert len(json.loads(result.output)["contacts"]) == 2


def test_raw_emits_compact_json_without_a_csv_param(monkeypatch):
    client, result = invoke(
        monkeypatch, ["-f", "raw"], client=RecordingClient(result=JSON_PAYLOAD),
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [("GET", "/api/contacts/export", {})]
    assert len(result.output.strip().splitlines()) == 1
    assert json.loads(result.output) == JSON_PAYLOAD


def test_table_renders_one_row_per_contact(monkeypatch):
    payload = {"contacts": JSON_PAYLOAD["contacts"] + [{
        "email": "third@example.com", "first_name": "Third", "last_name": "User",
        "tier": "registered", "tags": [], "unsubscribed": False,
    }]}
    _client, result = invoke(
        monkeypatch, ["-f", "table"], client=RecordingClient(result=payload),
    )

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    header = lines[0]
    for column in contacts_module.EXPORT_TABLE_COLUMNS:
        assert column in header
    body_lines = lines[2:]
    assert len(body_lines) == 3
    assert body_lines[0].startswith("alexey@example.com")
    assert body_lines[1].startswith("mueller@example.com")
    assert body_lines[2].startswith("third@example.com")
    assert "contacts" not in header


def test_table_with_no_contacts_prints_nothing(monkeypatch):
    _client, result = invoke(
        monkeypatch, ["-f", "table"], client=RecordingClient(result={"contacts": []}),
    )

    assert result.exit_code == 0, result.output
    assert result.output == ""


def test_csv_api_error_exits_one_and_writes_no_partial_output(monkeypatch, capsys):
    class ErrorClient:
        def get(self, _path, **_kwargs):
            raise APIError(401, {"error": "Invalid token"}, "safe")

    monkeypatch.setattr(contacts_module, "get_client", lambda: ErrorClient())
    monkeypatch.setattr(sys, "argv", ["asl", "contacts", "export", "-f", "csv"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: HTTP 401" in captured.err
    assert "Invalid token" in captured.err


def test_unsupported_format_is_a_usage_error_and_sends_no_request(monkeypatch):
    client, result = invoke(monkeypatch, ["--format", "xlsx"])

    assert result.exit_code == 2
    assert client.calls == []
    for choice in ("json", "table", "raw", "csv"):
        assert choice in result.stderr
