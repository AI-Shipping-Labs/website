"""Fail-closed coverage for Playwright navigation-timeout diagnostics."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright_tests.navigation_diagnostics import (
    DIAGNOSTIC_SECTION_TITLE,
    DjangoRequestJournal,
    _ping_state,
    append_navigation_diagnostics,
    build_navigation_diagnostics,
    redact_url,
)


def _call(message, *, when="call"):
    exception = PlaywrightTimeoutError(message)
    return SimpleNamespace(when=when, excinfo=SimpleNamespace(value=exception))


def _item(config=None):
    return SimpleNamespace(
        nodeid="playwright_tests/test_example.py::test_navigation",
        config=config or SimpleNamespace(),
    )


def _providers(*, request_status=200, browser_connected=True, provider_error=False):
    def broken_provider():
        raise OSError("synthetic /proc failure")

    return {
        "host": broken_provider
        if provider_error
        else lambda: {
            "logical_cpus": 4,
            "load_average_1m": 8.0,
            "load_average_5m": 7.0,
            "load_average_15m": 6.0,
            "available_memory_bytes": 16 * 1024**3,
            "swap_total_bytes": 4 * 1024**3,
            "swap_used_bytes": 1024**3,
            "cpu_pressure": "some avg60=80.00",
            "memory_pressure": "full avg60=20.00",
            "memory_full_avg60": 20.0,
        },
        "server": lambda: {"alive": True, "base_url": "http://127.0.0.1:8123"},
        "ping": lambda: {"status": 200, "latency_ms": 4.5, "error": None},
        "browser": lambda: {"connected": browser_connected, "contexts": 1, "pages": 1},
        "capacity": lambda: {
            "requested_slots": 4,
            "capacity": 4,
            "granted": True,
            "wait_seconds": 10.0,
        },
        "requests": lambda: [{"method": "GET", "path": "/target", "status": request_status}],
    }


class NavigationDiagnosticFormattingTests(SimpleTestCase):
    timeout_message = (
        "Page.goto: Timeout 30000ms exceeded.\nCall log:\n"
        '  - navigating to "https://user:pass@example.test/target?token=secret&view=wide", '
        'waiting until "domcontentloaded"'
    )

    def test_responsive_server_and_starved_browser_emit_actionable_evidence(self):
        value = json.loads(
            build_navigation_diagnostics(
                _item(),
                _call(self.timeout_message),
                providers=_providers(),
            )
        )
        self.assertEqual(value["navigation"]["wait_until"], "domcontentloaded")
        self.assertEqual(
            value["navigation"]["destination"],
            "https://<redacted>@example.test/target?token=<redacted>&view=wide",
        )
        self.assertEqual(value["recent_django_requests"][0]["status"], 200)
        self.assertEqual(value["ping"]["status"], 200)
        self.assertEqual(value["browser"], {"connected": True, "contexts": 1, "pages": 1})
        self.assertIn("supports browser/host starvation", value["classification"])

    def test_diagnostic_provider_failure_is_explicit_and_secrets_are_redacted(self):
        rendered = build_navigation_diagnostics(
            _item(),
            _call(self.timeout_message),
            providers=_providers(provider_error=True),
        )
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("token=secret", rendered)
        value = json.loads(rendered)
        self.assertIn("unavailable (OSError)", value["host"]["error"])
        self.assertIn("original node remains failed", value["classification"])

    def test_every_optional_provider_can_fail_without_hiding_the_node(self):
        def unavailable():
            raise RuntimeError("must not replace original traceback")

        value = json.loads(
            build_navigation_diagnostics(
                _item(),
                _call(self.timeout_message),
                providers={key: unavailable for key in ("host", "server", "ping", "browser", "capacity", "requests")},
            )
        )
        self.assertEqual(value["node_id"], "playwright_tests/test_example.py::test_navigation")
        self.assertEqual(value["server"]["alive"], "unavailable")
        self.assertEqual(value["browser"]["connected"], "unavailable")
        self.assertEqual(value["ping"]["status"], "unavailable")
        self.assertIn("original node remains failed", value["classification"])

    def test_404_5xx_missing_response_and_browser_disconnect_stay_application_evidence(self):
        cases = (
            (404, True),
            (500, True),
            ("unavailable", True),
            (200, False),
        )
        for status, connected in cases:
            with self.subTest(status=status, connected=connected):
                value = json.loads(
                    build_navigation_diagnostics(
                        _item(),
                        _call(self.timeout_message),
                        providers=_providers(
                            request_status=status,
                            browser_connected=connected,
                        ),
                    )
                )
                self.assertIn("requires investigation", value["classification"])
                self.assertIn("original node remains failed", value["classification"])

    def test_report_section_never_changes_original_failed_outcome(self):
        report = SimpleNamespace(failed=True, sections=[], outcome="failed")
        call = _call(self.timeout_message)
        append_navigation_diagnostics(report, _item(), call)

        self.assertEqual(report.outcome, "failed")
        self.assertEqual(len(report.sections), 1)
        self.assertEqual(report.sections[0][0], DIAGNOSTIC_SECTION_TITLE)
        self.assertIn("Page.goto", self.timeout_message)

    def test_fixture_phase_goto_timeout_gets_diagnostics_but_other_timeout_does_not(self):
        setup_report = SimpleNamespace(failed=True, sections=[])
        append_navigation_diagnostics(
            setup_report,
            _item(),
            _call(self.timeout_message, when="setup"),
        )
        setup_value = json.loads(setup_report.sections[0][1])
        self.assertEqual(setup_value["pytest_phase"], "setup")

        click_report = SimpleNamespace(failed=True, sections=[])
        append_navigation_diagnostics(
            click_report,
            _item(),
            _call("Locator.click: Timeout 30000ms exceeded."),
        )
        self.assertEqual(click_report.sections, [])


class RequestJournalAndProbeTests(SimpleTestCase):
    def test_request_journal_is_bounded_and_redacts_sensitive_query_values(self):
        journal = DjangoRequestJournal(size=2)

        class Request:
            method = "GET"

            def __init__(self, path):
                self.path = path

            def get_full_path(self):
                return self.path

        for path, status in (
            ("/old", 200),
            ("/target?password=secret&view=wide", 500),
            ("/latest", 404),
        ):
            record = logging.LogRecord("django.server", logging.INFO, __file__, 1, "request", (), None)
            record.request = Request(path)
            record.status_code = status
            journal.emit(record)

        snapshot = journal.snapshot()
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(snapshot[0]["path"], "/target?password=<redacted>&view=wide")
        self.assertNotIn("secret", json.dumps(snapshot))

    def test_request_journal_parses_django_runserver_log_records(self):
        journal = DjangoRequestJournal()
        record = logging.LogRecord(
            "django.server",
            logging.INFO,
            __file__,
            1,
            '"%s" %s %s',
            ("GET /target?token=secret HTTP/1.1", "200", "42"),
            None,
        )
        record.status_code = 200
        journal.emit(record)
        self.assertEqual(
            journal.snapshot(),
            [{"method": "GET", "path": "/target?token=<redacted>", "status": 200}],
        )

    def test_probe_calls_only_ping_with_a_bounded_timeout(self):
        calls = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(url, timeout):
            calls.append((url, timeout))
            return Response()

        ticks = iter([10.0, 10.004])
        config = SimpleNamespace(_playwright_django_server_base_url="http://127.0.0.1:8123")
        value = _ping_state(config, opener=opener, monotonic=lambda: next(ticks))

        self.assertEqual(calls, [("http://127.0.0.1:8123/ping", 1.0)])
        self.assertEqual(value["status"], 200)
        self.assertEqual(value["latency_ms"], 4.0)

    def test_remote_dev_selection_has_no_local_diagnostic_claim(self):
        value = json.loads(
            build_navigation_diagnostics(
                _item(),
                _call(NavigationDiagnosticFormattingTests.timeout_message),
                providers={
                    **_providers(),
                    "server": lambda: {"alive": "unavailable", "base_url": "unavailable"},
                    "ping": lambda: {
                        "status": "unavailable",
                        "latency_ms": "unavailable",
                        "error": "local server unavailable",
                    },
                    "capacity": lambda: {
                        "requested_slots": "unavailable",
                        "capacity": "unavailable",
                        "granted": "unavailable",
                        "wait_seconds": "unavailable",
                    },
                },
            )
        )
        self.assertEqual(value["server"]["base_url"], "unavailable")
        self.assertEqual(value["capacity"]["granted"], "unavailable")

    def test_redact_url_handles_invalid_ports_without_leaking_credentials(self):
        self.assertEqual(redact_url("https://user:pass@example.test:bad/path?token=x"), "<redacted-url>")


class PytestFailureIntegrationTests(SimpleTestCase):
    def test_persistent_page_goto_timeout_keeps_failed_exit_and_report_section(self):
        root = Path(__file__).resolve().parent.parent
        scratch_root = root / ".tmp"
        scratch_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="goto-timeout-1605-", dir=scratch_root) as directory:
            test_file = Path(directory) / "test_synthetic_goto_timeout.py"
            test_file.write_text(
                "from playwright.sync_api import TimeoutError as PlaywrightTimeoutError\n\n"
                "def test_timeout_remains_failed():\n"
                "    raise PlaywrightTimeoutError(\n"
                "        'Page.goto: Timeout 30000ms exceeded.\\n'\n"
                "        'navigating to \\\"https://example.test/target\\\", '\n"
                "        'waiting until \\\"domcontentloaded\\\"'\n"
                "    )\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PLAYWRIGHT_BASE_URL"] = "https://example.invalid"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(test_file),
                    "-p",
                    "playwright_tests.conftest",
                    "-q",
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 1)
        self.assertIn("PlaywrightTimeoutError", output)
        self.assertIn(DIAGNOSTIC_SECTION_TITLE, output)
        self.assertIn('"destination": "https://example.test/target"', output)
        self.assertIn("1 failed", output)
