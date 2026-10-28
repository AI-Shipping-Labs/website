"""Executable coverage for ``deploy/readiness_poll.sh``.

The scheduled dev Playwright workflow sources this helper to wait for the
Contabo dev endpoint. A stub ``curl`` on ``PATH`` replays a scripted sequence
of responses so the polling contract runs without network access.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

HELPER = Path(__file__).resolve().parents[1] / "deploy" / "readiness_poll.sh"

# Each call pops the first line of $STUB_RESPONSES; an empty line means the
# endpoint was unreachable. The call count is appended to $STUB_CALLS.
CURL_STUB = """#!/usr/bin/env bash
echo call >> "${STUB_CALLS}"
line=$(head -n 1 "${STUB_RESPONSES}")
tail -n +2 "${STUB_RESPONSES}" > "${STUB_RESPONSES}.next"
mv "${STUB_RESPONSES}.next" "${STUB_RESPONSES}"
if [ -z "${line}" ]; then
    exit 7
fi
printf '%s' "${line}"
"""


class ReadinessPollTest(SimpleTestCase):
    def poll(self, responses, args):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stub = tmp_path / "curl"
            stub.write_text(CURL_STUB)
            stub.chmod(0o755)
            responses_file = tmp_path / "responses"
            responses_file.write_text("\n".join(responses) + "\n")
            calls_file = tmp_path / "calls"
            calls_file.write_text("")
            env = dict(
                os.environ,
                PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}",
                STUB_RESPONSES=str(responses_file),
                STUB_CALLS=str(calls_file),
            )
            script = f'source "{HELPER}"; readiness_poll_until_stable {args}'
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            calls = len(calls_file.read_text().splitlines())
        return result, calls

    def test_succeeds_after_required_consecutive_non_empty_responses(self):
        result, calls = self.poll(["", "ok", "ok", "ok"], 'http://x/ping "" 30 0 10 3')

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertEqual(calls, 4)

    def test_failed_response_resets_the_consecutive_count(self):
        result, calls = self.poll(["ok", "ok", "", "ok", "ok"], 'http://x/ping "" 30 0 5 3')

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, 5)

    def test_exact_expectation_rejects_other_bodies(self):
        result, _calls = self.poll(["old", "old"], 'http://x/ping "new" 30 0 2 1')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("response_state=non-matching", result.stdout)

    def test_response_body_is_never_logged(self):
        result, _calls = self.poll(["secret-body"], 'http://x/ping "" 30 0 1 1')

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("secret-body", result.stdout + result.stderr)

    def test_invalid_configuration_fails_before_polling(self):
        result, calls = self.poll(["ok"], 'http://x/ping "" 0 0 1 1')

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timeout must be a positive integer", result.stderr)
        self.assertEqual(calls, 0)
