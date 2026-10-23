"""Executable coverage for scheduled Playwright dev endpoint readiness."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml"
SCRATCH_ROOT = REPO_ROOT / ".tmp" / "test-scheduled-playwright-dev-readiness"

FAKE_CURL = """#!/bin/bash
printf '%s\\n' "$*" >> "${FAKE_CURL_LOG}"
case "${FAKE_CURL_MODE}" in
    healthy)
        printf 'ready\\n'
        ;;
    http-503)
        exit 22
        ;;
    unreachable)
        exit 7
        ;;
    *)
        exit 64
        ;;
esac
"""

FAKE_AWS = """#!/bin/bash
printf '%s\\n' "$*" >> "${FAKE_AWS_LOG}"
exit 97
"""

FAKE_SLEEP = """#!/bin/bash
exit 0
"""

FAKE_CLOCK = """#!/bin/bash
printf '100\\n'
"""


def _readiness_script():
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    steps = workflow["jobs"]["dev-readiness"]["steps"]
    for step in steps:
        if step.get("name") == "Wait for Contabo dev endpoint":
            return step["run"]
    raise AssertionError("Scheduled dev readiness step is missing")


def _write_executable(path, source):
    path.write_text(source)
    path.chmod(0o755)


def _stubbed_env(stub_dir, mode, curl_log, aws_log):
    env = os.environ.copy()
    env.pop("BASH_ENV", None)
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["READINESS_PYTHON_BIN"] = str(stub_dir / "clock")
    env["PLAYWRIGHT_BASE_URL"] = "https://contabo-dev.example/"
    env["FAKE_CURL_MODE"] = mode
    env["FAKE_CURL_LOG"] = str(curl_log)
    env["FAKE_AWS_LOG"] = str(aws_log)
    return env


def _read_log(path):
    if not path.exists():
        return []
    return path.read_text().splitlines()


def _run_readiness_step(mode):
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f"{mode}-", dir=SCRATCH_ROOT) as root:
        stub_dir = Path(root)
        curl_log = stub_dir / "curl.log"
        aws_log = stub_dir / "aws.log"
        _write_executable(stub_dir / "curl", FAKE_CURL)
        _write_executable(stub_dir / "aws", FAKE_AWS)
        _write_executable(stub_dir / "sleep", FAKE_SLEEP)
        _write_executable(stub_dir / "clock", FAKE_CLOCK)

        result = subprocess.run(
            ["bash", "-c", _readiness_script()],
            cwd=REPO_ROOT,
            env=_stubbed_env(stub_dir, mode, curl_log, aws_log),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result, _read_log(curl_log), _read_log(aws_log)


@tag("core")
class ScheduledPlaywrightDevReadinessExecutionTest(SimpleTestCase):
    def test_healthy_endpoint_requires_three_consecutive_responses(self):
        result, curl_calls, aws_calls = _run_readiness_step("healthy")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(curl_calls), 3)
        self.assertTrue(
            all(call.endswith("https://contabo-dev.example/ping") for call in curl_calls)
        )
        self.assertIn("Contabo dev endpoint is ready.", result.stdout)
        self.assertEqual(aws_calls, [])

    def test_persistent_http_error_and_unreachable_endpoint_fail_bounded(self):
        for mode in ("http-503", "unreachable"):
            with self.subTest(mode=mode):
                result, curl_calls, aws_calls = _run_readiness_step(mode)

                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(curl_calls), 60)
                self.assertIn("response_state=empty-or-unreachable", result.stdout)
                self.assertIn("::error ::Contabo dev endpoint", result.stdout)
                self.assertEqual(aws_calls, [])
