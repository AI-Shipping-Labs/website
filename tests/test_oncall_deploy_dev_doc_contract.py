"""Offline documentation contract between the on-call role and the CI watcher.

Guards issue #1857: `.claude/agents/oncall-engineer.md` must describe the same
fail-closed default `Deploy Dev` policy that `scripts/watch-ci.py` implements
since #1856 — the twelve exact required job instances (including combined
coverage and PostgreSQL 16 verification), no default required-job skip, and
the green/failed/superseded/no_verdict outcome boundary. Deterministic and
offline: no network, no subprocess, no sleeping.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE_PATH = REPO_ROOT / ".claude" / "agents" / "oncall-engineer.md"
WATCHER_PATH = REPO_ROOT / "scripts" / "watch-ci.py"

GUARD_MODULE_NAME = Path(__file__).stem

# Load the watcher the same way tests/test_watch_ci.py does: the hyphenated
# filename cannot be a normal import, and importing it never starts polling.
_spec = importlib.util.spec_from_file_location("watch_ci_doc_contract", WATCHER_PATH)
assert _spec and _spec.loader
watch_ci = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("watch_ci_doc_contract", watch_ci)
_spec.loader.exec_module(watch_ci)

REQUIRED_JOBS = list(watch_ci.DEPLOY_DEV_REQUIRED_CHECKS)

# The numbered job inventory in the role file, anchored to its introducing
# sentence so unrelated numbered lists elsewhere cannot confuse the parse.
_JOB_INVENTORY_RE = re.compile(
    r"requires these (\d+)\s*\n?exact job instances\..*?pins this role file to it:\n\n"
    r"((?:\d+\.\s+`[^`]+`\n)+)\n",
    re.DOTALL,
)
_JOB_ITEM_RE = re.compile(r"^\d+\.\s+`([^`]+)`$", re.MULTILINE)

# Retired pre-#1856 policy text that must never return to the role file.
_RETIRED_PHRASES = (
    "ten required",
    "ten-job",
    "10 required",
    "appropriately skipped",
    "required-name set",
    "postgresql verification remains a deploy dependency",
)


def role_text() -> str:
    return ROLE_PATH.read_text(encoding="utf-8")


def norm(text: str) -> str:
    """Collapse all whitespace runs so line wrapping cannot hide a phrase."""
    return " ".join(text.split())


def completed_run(conclusions: dict[str, str] | None = None, *, omit: str = "") -> watch_ci.Run:
    """A completed `Deploy Dev` run whose required jobs all succeeded, with
    per-job conclusion overrides and an optional omitted (missing) job."""
    conclusions = conclusions or {}
    jobs = tuple(
        watch_ci.Job(name=name, status="completed", conclusion=conclusions.get(name, "success"))
        for name in REQUIRED_JOBS
        if name != omit
    )
    return watch_ci.Run(
        run_id="100",
        status="completed",
        conclusion="success",
        workflow="Deploy Dev",
        branch="main",
        created_at="2026-10-05T00:00:00Z",
        head_sha="a" * 40,
        jobs=jobs,
    )


@tag("core")
class WatcherContractGroundTruthTest(SimpleTestCase):
    """The watcher itself still carries the #1856 twelve-job no-skip policy."""

    def test_required_inventory_holds_twelve_jobs(self):
        self.assertEqual(len(REQUIRED_JOBS), 12)

    def test_combined_coverage_and_postgres_stay_required(self):
        self.assertIn("Combined coverage (fail-under 85)", REQUIRED_JOBS)
        self.assertIn("PostgreSQL 16 Verification", REQUIRED_JOBS)

    def test_default_policy_resolves_to_no_allowed_skips(self):
        required, allowed_skips = watch_ci._policy.resolve_required_policy(
            "Deploy Dev", None, None, watch_ci.DEPLOY_DEV_REQUIRED_CHECKS
        )
        self.assertEqual(required, REQUIRED_JOBS)
        self.assertEqual(allowed_skips, [])

    def test_custom_contract_may_allow_named_skips(self):
        required, allowed_skips = watch_ci._policy.resolve_required_policy(
            "Other Workflow", ["job-a", "job-b"], None, watch_ci.DEPLOY_DEV_REQUIRED_CHECKS
        )
        self.assertEqual(required, ["job-a", "job-b"])
        self.assertEqual(allowed_skips, ["job-a", "job-b"])


@tag("core")
class WatcherBehaviourBoundaryTest(SimpleTestCase):
    """The documented boundary must not contradict the watcher's classifier."""

    @staticmethod
    def watcher_with_gh(payload: str) -> watch_ci.CIWatcher:
        return watch_ci.CIWatcher(runner=lambda args: payload, clock=watch_ci.VirtualClock())

    def test_watcher_instance_uses_the_default_no_skip_policy(self):
        watcher = self.watcher_with_gh("{}")
        self.assertEqual(watcher.required, REQUIRED_JOBS)
        self.assertEqual(watcher.allowed_skips, [])

    def test_all_required_jobs_success_is_green(self):
        verdict = watch_ci.classify(completed_run(), REQUIRED_JOBS, [])
        self.assertEqual(verdict.result, watch_ci.GREEN)

    def test_skipped_deploy_is_no_verdict_and_not_a_deployment(self):
        verdict = watch_ci.classify(
            completed_run({"Deploy to Dev": "skipped"}), REQUIRED_JOBS, []
        )
        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertIn("Deploy to Dev", verdict.failing_jobs)
        self.assertIn("no verified deployment", verdict.reason)

    def test_cancelled_postgres_with_skipped_deploy_is_no_verdict(self):
        verdict = watch_ci.classify(
            completed_run(
                {"PostgreSQL 16 Verification": "cancelled", "Deploy to Dev": "skipped"}
            ),
            REQUIRED_JOBS,
            [],
        )
        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertNotEqual(verdict.result, watch_ci.GREEN)
        self.assertIn("PostgreSQL 16 Verification", verdict.failing_jobs)

    def test_missing_required_job_is_no_verdict(self):
        verdict = watch_ci.classify(
            completed_run(omit="Combined coverage (fail-under 85)"), REQUIRED_JOBS, []
        )
        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertIn("Combined coverage (fail-under 85)", verdict.failing_jobs)

    def test_genuine_postgres_failure_is_failed_and_wins_over_skip(self):
        verdict = watch_ci.classify(
            completed_run(
                {"PostgreSQL 16 Verification": "failure", "Deploy to Dev": "skipped"}
            ),
            REQUIRED_JOBS,
            [],
        )
        self.assertEqual(verdict.result, watch_ci.FAILED)
        self.assertIn("PostgreSQL 16 Verification", verdict.failing_jobs)

    def test_cancelled_run_is_superseded_only_with_a_newer_run(self):
        cancelled = watch_ci.Run(
            run_id="100",
            status="completed",
            conclusion="cancelled",
            workflow="Deploy Dev",
            branch="main",
            created_at="2026-10-05T00:00:00Z",
            head_sha="a" * 40,
            jobs=completed_run().jobs,
        )
        raw = watch_ci.classify(cancelled, REQUIRED_JOBS, [])
        self.assertIsNotNone(raw)
        self.assertEqual(raw.result, watch_ci.SUPERSEDED)

        # Verdict is mutable and `_resolve_terminal` rewrites it in place, so
        # each resolution path classifies the cancelled run fresh.
        without_newer = self.watcher_with_gh("{}")._resolve_terminal(
            watch_ci.classify(cancelled, REQUIRED_JOBS, []), cancelled
        )
        self.assertEqual(without_newer.result, watch_ci.NO_VERDICT)
        self.assertNotEqual(without_newer.result, watch_ci.GREEN)

        newer_payload = json.dumps(
            [{"databaseId": "200", "createdAt": "2026-10-05T01:00:00Z", "status": "queued"}]
        )
        with_newer = self.watcher_with_gh(newer_payload)._resolve_terminal(
            watch_ci.classify(cancelled, REQUIRED_JOBS, []), cancelled
        )
        self.assertEqual(with_newer.result, watch_ci.SUPERSEDED)
        self.assertEqual(with_newer.newer_run_id, "200")
        self.assertNotEqual(with_newer.result, watch_ci.GREEN)


@tag("core")
class RoleDocumentContractTest(SimpleTestCase):
    """The role file must carry the watcher's exact inventory and boundary."""

    def test_role_file_exists(self):
        self.assertTrue(ROLE_PATH.is_file(), f"missing role file: {ROLE_PATH}")

    def test_role_job_list_matches_watcher_exactly(self):
        match = _JOB_INVENTORY_RE.search(role_text())
        self.assertIsNotNone(
            match, "the role file no longer contains the required-job inventory list"
        )
        stated_count = int(match.group(1))
        documented_jobs = _JOB_ITEM_RE.findall(match.group(2))
        self.assertEqual(stated_count, len(REQUIRED_JOBS))
        self.assertEqual(documented_jobs, REQUIRED_JOBS)

    def test_exit_code_table_matches_watcher(self):
        text = role_text()
        for name in ("green", "failed", "hang", "unresolved", "superseded", "no_verdict"):
            code = watch_ci.Verdict(result=name).exit_code
            self.assertIn(f"| {code} | `{name}` |", text)

    def test_role_states_the_fail_closed_boundary(self):
        text = norm(role_text())
        count = len(REQUIRED_JOBS)
        self.assertIn(f"requires these {count}", text)
        self.assertIn(f"all {count} required jobs are present, terminal, and", text)
        self.assertIn("no acceptable required-job skip", text)
        self.assertIn("wins over downstream cancellation or skip", text)
        self.assertIn("follow the json `newer_run_id` once within the same assignment", norm(text).lower())

    def test_role_points_at_this_guard_by_its_real_name(self):
        self.assertIn(GUARD_MODULE_NAME, role_text())

    def test_role_keeps_custom_skip_policy_out_of_the_default_contract(self):
        text = norm(role_text())
        self.assertIn("Explicit custom watcher contracts", text)
        self.assertIn("never a property of the default", text)

    def test_role_keeps_sole_observer_and_green_only_claims(self):
        text = norm(role_text())
        self.assertIn("the only CI observation you do", text)
        self.assertIn("Never claim an image, tag, deployment, promotion candidate, or readiness", text)
        self.assertIn("`run_id` and `head_sha`", text)


@tag("core")
class RetiredPolicyRegressionTest(SimpleTestCase):
    """The retired ten-job / default-skip policy must never come back."""

    def test_retired_phrases_stay_out_of_the_role_file(self):
        lowered = role_text().lower()
        for phrase in _RETIRED_PHRASES:
            self.assertNotIn(phrase, lowered)
