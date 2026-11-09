"""Offline contracts for complete ``asl crm-export`` pagination."""

from __future__ import annotations

import json

from asl_cli.cli import cli
from asl_cli.client import APIError
from asl_cli.commands import misc as misc_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self, total=201, fail_at=None):
        self.total = total
        self.fail_at = fail_at
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        offset = params["offset"]
        if offset == self.fail_at:
            raise APIError(503, {"error": "Unavailable"}, "test")
        size = min(params["limit"], self.total - offset)
        members = [{"email": f"member-{index}@example.com"}
                   for index in range(offset, offset + size)]
        return {
            "members": members,
            "count": len(members),
            "total": self.total,
            "limit": params["limit"],
            "offset": offset,
            "scope": "crm",
            "generated_at": "2026-11-09T10:00:00+00:00",
        }


def invoke(monkeypatch, client, *args):
    monkeypatch.setattr(misc_module, "get_client", lambda: client)
    return CliRunner().invoke(cli, ["crm-export", *args])


def test_crm_export_fetches_all_pages_and_preserves_response_envelope(monkeypatch):
    client = RecordingClient()

    result = invoke(monkeypatch, client)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["members"]) == 201
    assert payload["members"][0]["email"] == "member-0@example.com"
    assert payload["members"][-1]["email"] == "member-200@example.com"
    assert payload["count"] == payload["total"] == 201
    assert payload["limit"] == 200
    assert payload["offset"] == 0
    assert client.calls == [
        ("/api/crm/export", {"limit": 200, "offset": 0}),
        ("/api/crm/export", {"limit": 200, "offset": 200}),
    ]


def test_crm_export_does_not_print_a_partial_export_after_page_error(monkeypatch):
    client = RecordingClient(total=401, fail_at=200)

    result = invoke(monkeypatch, client)

    assert result.exit_code != 0
    assert result.output == ""
    assert isinstance(result.exception, APIError)
    assert len(client.calls) == 2


def test_crm_export_handles_an_empty_result(monkeypatch):
    client = RecordingClient(total=0)

    result = invoke(monkeypatch, client, "--format", "raw")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["members"] == []
    assert payload["count"] == payload["total"] == 0
    assert len(client.calls) == 1
