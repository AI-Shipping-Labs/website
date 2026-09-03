"""Hermetic gates for the opt-in live Slack announcement target (#1480).

These tests never post to Slack. They prove default collection stays
side-effect free and that operator-facing failures cannot echo tokens.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner

from community.tests.live_slack.test_post_slack_announcement_real import (
    LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV,
    secret_safe_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_MODULE = "community.tests.live_slack.test_post_slack_announcement_real"
LIVE_PYTEST_PATH = (
    "community/tests/live_slack/test_post_slack_announcement_real.py"
)


def _run_live_pytest(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            LIVE_PYTEST_PATH,
            "-q",
            "-rs",
            "--tb=no",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class LiveSlackAnnouncementGatingTest(SimpleTestCase):
    def test_django_runner_does_not_collect_the_live_pytest_contract(self):
        runner = DiscoverRunner(verbosity=0, interactive=False)
        suite = runner.build_suite([LIVE_MODULE])
        self.assertEqual(suite.countTestCases(), 0)

    def test_credentials_without_opt_in_skip_and_do_not_send(self):
        env = os.environ.copy()
        env.pop(LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV, None)
        env["SLACK_BOT_TOKEN"] = "xoxb-gating-must-not-send"
        env["SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID"] = "C0AHN84QNP3"

        completed = _run_live_pytest(env)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertRegex(combined, r"1 skipped")
        self.assertNotIn("xoxb-gating-must-not-send", combined)
        self.assertIn("opt-in", combined)

    def test_opt_in_without_credentials_preserves_the_original_skip(self):
        env = os.environ.copy()
        env[LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV] = "1"
        env.pop("SLACK_BOT_TOKEN", None)
        env.pop("SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID", None)

        completed = _run_live_pytest(env)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        combined = f"{completed.stdout}\n{completed.stderr}"
        self.assertRegex(combined, r"1 skipped")
        self.assertIn("SLACK_BOT_TOKEN", combined)
        self.assertNotIn("xoxb-", combined)

    def test_make_target_names_the_external_slack_side_effect(self):
        completed = subprocess.run(
            ["make", "-n", "test-live-slack-announcement"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = completed.stdout
        self.assertIn(f"{LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV}=1", output)
        self.assertIn("community/tests/live_slack/", output)
        self.assertIn("live_slack_announcement", output)

    def test_default_make_test_recipe_does_not_opt_in_to_live_slack(self):
        completed = subprocess.run(
            ["make", "-n", "test"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("test-live-slack-announcement", completed.stdout)
        self.assertNotIn(LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV, completed.stdout)

    def test_failure_text_redacts_tokens_and_authorization_headers(self):
        leaked = (
            "Bearer xoxb-secret-token-value chat.postMessage failed "
            "for xoxb-secret-token-value"
        )
        redacted = secret_safe_text(leaked)
        self.assertNotIn("xoxb-secret-token-value", redacted)
        self.assertNotIn("Bearer xoxb-secret-token-value", redacted)
        self.assertIn("[redacted]", redacted)
        self.assertIn("chat.postMessage failed", redacted)
