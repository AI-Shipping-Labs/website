from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

OWNED_INVOCATIONS = {
    ("deploy-dev.yml", "playwright-core", "Run Playwright core shard"): (
        'uv run pytest -m "core and not manual_visual and not slow_platform '
        'and not visual_regression" "${files[@]}" -v --durations=25'
    ),
    ("scheduled-playwright.yml", "playwright-full", "Run full Playwright shard"): (
        'uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" '
        '"${files[@]}" -v --durations=25'
    ),
    (
        "scheduled-playwright.yml",
        "playwright-excluded-markers",
        "Run excluded Playwright marker suites",
    ): (
        'uv run pytest -m "manual_visual or slow_platform" '
        'playwright_tests/ -v --durations=25'
    ),
    ("scheduled-playwright-dev.yml", "playwright-dev", "Run Playwright dev shard"): (
        'uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" '
        '"${files[@]}" -v --durations=25'
    ),
}


def _load_workflow(filename):
    return yaml.safe_load((WORKFLOW_DIR / filename).read_text())


def _owned_pytest_invocations():
    observed = {}
    for filename, job_names in {
        "deploy-dev.yml": {"playwright-core"},
        "scheduled-playwright.yml": {"playwright-full", "playwright-excluded-markers"},
        "scheduled-playwright-dev.yml": {"playwright-dev"},
    }.items():
        workflow = _load_workflow(filename)
        for job_name in job_names:
            for step in workflow["jobs"][job_name]["steps"]:
                command = step.get("run", "")
                if "uv run pytest " in command:
                    observed[(filename, job_name, step["name"])] = (command.strip(), step)
    return observed


@tag("core")
class CIPytestTimingContractTest(SimpleTestCase):
    def test_every_owned_playwright_invocation_emits_bounded_durations(self):
        observed = _owned_pytest_invocations()

        self.assertEqual(set(observed), set(OWNED_INVOCATIONS))
        for identity, expected_command in OWNED_INVOCATIONS.items():
            with self.subTest(identity=identity):
                command, step = observed[identity]
                self.assertIn(expected_command, command)
                self.assertEqual(command.count("uv run pytest "), 1)
                self.assertEqual(command.count("--durations=25"), 1)
                self.assertNotIn("|| true", command)
                self.assertFalse(step.get("continue-on-error", False))

    def test_local_pytest_defaults_do_not_emit_ci_timing_table(self):
        self.assertNotIn("--durations", (REPO_ROOT / "pyproject.toml").read_text())
        self.assertNotIn("--durations", (REPO_ROOT / "Makefile").read_text())
