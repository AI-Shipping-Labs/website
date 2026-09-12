"""Offline CLI contracts for package-backed integration settings."""

from asl_cli.cli import cli
from asl_cli.commands import integrations as integrations_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return self.pages.pop(0)

    def post(self, path, *, json_body=None, **kwargs):
        self.calls.append(("POST", path, kwargs, json_body))
        return {"updated": list(json_body["settings"])}


def test_settings_lists_all_package_pages_before_group_filter(monkeypatch):
    client = RecordingClient(
        [
            {
                "settings": [{"key": "A", "group": "one"}],
                "pagination": {"total": 2, "limit": 100, "offset": 0},
            },
            {
                "settings": [{"key": "B", "group": "two"}],
                "pagination": {"total": 2, "limit": 100, "offset": 1},
            },
        ]
    )
    monkeypatch.setattr(integrations_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        ["integrations", "settings", "--group", "two", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert '"key": "B"' in result.output
    assert '"key": "A"' not in result.output
    assert client.calls == [
        ("GET", "/api/v1/settings", {"params": {"limit": 100, "offset": 0}}),
        ("GET", "/api/v1/settings", {"params": {"limit": 100, "offset": 1}}),
    ]


def test_settings_set_posts_package_import_shape(monkeypatch):
    client = RecordingClient([])
    monkeypatch.setattr(integrations_module, "get_client", lambda: client)

    result = CliRunner().invoke(
        cli,
        [
            "integrations",
            "set",
            "--updates",
            "SITE_BASE_URL=https://example.test,SLACK_ENABLED=true",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "POST",
            "/api/v1/settings/import",
            {},
            {
                "settings": {
                    "SITE_BASE_URL": "https://example.test",
                    "SLACK_ENABLED": "true",
                },
                "reason": "Updated through asl integrations set",
            },
        )
    ]
