"""Offline safety contracts for ``scripts/cleanup-agent-worktrees.py``.

Every Git mutation in this module is confined to a synthetic repository below
the project's gitignored ``.tmp/`` directory. No existing worktree is ever
passed to the cleanup service.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, tag

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "scripts" / "cleanup-agent-worktrees.py"
_spec = importlib.util.spec_from_file_location("cleanup_agent_worktrees", MODULE_PATH)
assert _spec and _spec.loader
cleanup = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_agent_worktrees"] = cleanup
_spec.loader.exec_module(cleanup)


class StaticScanner:
    def __init__(self, scan=None):
        self.result = scan or cleanup.ProcessScan(True)

    def scan(self, candidate):
        return self.result


class RecordingRunner:
    def __init__(self):
        self.delegate = cleanup.CommandRunner()
        self.calls = []

    def __call__(self, args, *, cwd=None):
        self.calls.append((list(args), Path(cwd) if cwd else None))
        return self.delegate(args, cwd=cwd)


class FailingBranchDeleteRunner(RecordingRunner):
    def __call__(self, args, *, cwd=None):
        self.calls.append((list(args), Path(cwd) if cwd else None))
        if list(args[:3]) == ["git", "branch", "-d"]:
            return cleanup.CommandResult(b"", b"simulated branch refusal", 1)
        return self.delegate(args, cwd=cwd)


class SyntheticRepo:
    """A disposable repository whose entire graph lives below project .tmp."""

    def __init__(self):
        temp_root = PROJECT_ROOT / ".tmp" / "test-cleanup-agent-worktrees"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="repo-", dir=temp_root)).resolve()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "tests@example.com")
        self.git("config", "user.name", "Cleanup Tests")
        (self.root / "tracked.txt").write_text("base\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "base")
        self.git("update-ref", "refs/remotes/origin/main", self.head)

    def close(self):
        assert self.root.is_relative_to(PROJECT_ROOT / ".tmp")
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(
            ["git", *map(str, args)],
            cwd=cwd or self.root,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @property
    def head(self):
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def add_worktree(self, name="agent-1442", *, detached=False):
        path = self.root / ".claude" / "worktrees" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if detached:
            self.git("worktree", "add", "--detach", path, self.head)
        else:
            self.git("worktree", "add", "-b", f"worktree-{name}", path, self.head)
        return path.resolve()

    def gh(self, args):
        if list(args[:2]) == ["run", "view"]:
            run_id = str(args[2])
            return {
                "databaseId": int(run_id),
                "status": "completed",
                "conclusion": "success",
                "workflowName": cleanup.DEPLOY_WORKFLOW,
                "headSha": self.head,
            }
        if list(args[:2]) == ["issue", "view"]:
            return {"number": int(args[2]), "state": "CLOSED"}
        raise AssertionError(f"unexpected gh args: {args}")

    def service(self, *, scanner=None, runner=None, actor="orchestrator"):
        return cleanup.CleanupService(
            self.root,
            actor=actor,
            runner=runner,
            gh_runner=self.gh,
            process_scanner=scanner or StaticScanner(),
            now=lambda: "2026-08-13T20:00:00Z",
        )

    def terminal(self, service, path, *, issue=1442):
        service.create_lease(path=path, issue=issue, role="software-engineer")
        service.close_lease(
            path=path,
            issue=issue,
            merge_sha=self.head,
            run_id="12345",
            run_head_sha=self.head,
        )


class SyntheticRepoTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.synthetic = SyntheticRepo()

    def tearDown(self):
        self.synthetic.close()
        super().tearDown()


@tag("core")
class PorcelainAndProcessContractTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        temp_root = PROJECT_ROOT / ".tmp" / "test-cleanup-agent-worktrees"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="proc-", dir=temp_root)).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_nul_porcelain_preserves_spaces_detached_locked_and_prunable(self):
        raw = (
            b"worktree /repo/main\0HEAD abc\0branch refs/heads/main\0\0"
            b"worktree /repo/agent one\0HEAD def\0detached\0locked reason\0prunable stale\0\0"
        )
        records = cleanup.parse_worktree_porcelain(raw)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[1].path, Path("/repo/agent one"))
        self.assertTrue(records[1].detached)
        self.assertTrue(records[1].locked)
        self.assertTrue(records[1].prunable)

    def test_proc_scanner_detects_rooted_cwd_cmdline_and_fd(self):
        candidate = self.root / "candidate"
        candidate.mkdir()
        proc = self.root / "proc"
        for pid in (101, 102, 103):
            process = proc / str(pid)
            (process / "fd").mkdir(parents=True)
            (process / "cmdline").write_bytes(b"python\0")
            os.symlink(self.root, process / "cwd")
        (proc / "102" / "cmdline").write_bytes(b"python\0--workdir=" + os.fsencode(candidate) + b"\0")
        rooted_file = candidate / "open.log"
        rooted_file.write_text("open")
        os.symlink(rooted_file, proc / "103" / "fd" / "5")
        (proc / "101" / "cwd").unlink()
        os.symlink(candidate, proc / "101" / "cwd")

        result = cleanup.ProcessScanner(proc, self_pid=999).scan(candidate)

        self.assertTrue(result.complete)
        self.assertEqual(
            [(use.pid, use.reasons) for use in result.uses],
            [(101, ("cwd",)), (102, ("cmdline",)), (103, ("fd",))],
        )

    def test_proc_visibility_failure_is_incomplete_not_empty(self):
        missing = self.root / "does-not-exist"
        result = cleanup.ProcessScanner(missing).scan(self.root / "candidate")

        self.assertFalse(result.complete)
        self.assertTrue(result.errors)


@tag("core")
class LeaseContractTest(SyntheticRepoTestCase):
    def test_active_lease_lives_in_common_git_dir_and_vetoes_cleanup(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        lease_path = service.create_lease(path=path, issue=1442, role="tester")

        self.assertTrue(lease_path.is_relative_to(self.synthetic.root / ".git" / cleanup.LEASE_DIRNAME))
        self.assertFalse(lease_path.is_relative_to(path))
        self.assertEqual(json.loads(lease_path.read_text())["state"], "active")
        plan = service.classify_path(path)
        self.assertEqual(plan.classification, cleanup.RETAIN_ACTIVE_LIFECYCLE)

    def test_blank_attribution_or_invalid_lease_identity_is_rejected(self):
        path = self.synthetic.add_worktree()
        with self.assertRaises(cleanup.CleanupError):
            self.synthetic.service(actor=" ")
        service = self.synthetic.service()
        with self.assertRaises(cleanup.CleanupError):
            service.create_lease(path=path, issue=0, role="tester")
        with self.assertRaises(cleanup.CleanupError):
            service.create_lease(path=path, issue=1442, role=" ")

    def test_missing_malformed_and_path_mismatched_leases_retain(self):
        missing_path = self.synthetic.add_worktree("missing")
        malformed_path = self.synthetic.add_worktree("malformed")
        mismatch_path = self.synthetic.add_worktree("mismatch")
        service = self.synthetic.service()
        service.lease_dir.mkdir(parents=True)
        service._lease_path(malformed_path).write_text("not-json")
        service._lease_path(mismatch_path).write_text(
            json.dumps(
                {
                    "version": 1,
                    "issue": 1442,
                    "path": str(missing_path),
                    "state": "active",
                    "actor": "orchestrator",
                    "created_at": "now",
                    "updated_at": "now",
                }
            )
        )

        for path in (missing_path, malformed_path, mismatch_path):
            plan = service.classify_path(path)
            self.assertEqual(plan.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
            self.assertNotEqual(plan.classification, cleanup.ELIGIBLE_REMOVE)

        service._lease_path(malformed_path).write_text("[]")
        self.assertEqual(
            service.classify_path(malformed_path).classification,
            cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
        )

    def test_terminal_close_validates_run_and_ancestry(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)

        lease, errors = service.read_lease(path)
        self.assertEqual(errors, [])
        self.assertEqual(lease["state"], "terminal")
        self.assertTrue(lease["terminal"]["roles_ended"])
        self.assertEqual(service.classify_path(path).classification, cleanup.ELIGIBLE_REMOVE)

    def test_run_lookup_failure_and_mismatched_success_retain(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)

        service.gh_runner = lambda args: {"databaseId": 12345, "status": "completed", "conclusion": "failure"}
        plan = service.classify_path(path)

        self.assertEqual(plan.classification, cleanup.RETAIN_TERMINAL_EVIDENCE_MISSING)
        self.assertNotEqual(plan.classification, cleanup.ELIGIBLE_REMOVE)

    def test_open_or_mismatched_issue_evidence_retain(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)
        original_gh = service.gh_runner

        def open_issue(args):
            if list(args[:2]) == ["issue", "view"]:
                return {"number": 1442, "state": "OPEN"}
            return original_gh(args)

        service.gh_runner = open_issue
        plan = service.classify_path(path)

        self.assertEqual(plan.classification, cleanup.RETAIN_TERMINAL_EVIDENCE_MISSING)
        self.assertIn("issue terminal evidence mismatch", plan.errors)

    def test_legacy_adoption_is_explicit_and_attributable(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service(actor="orchestrator-session-7")

        lease_path = service.close_lease(
            path=path,
            issue=1442,
            merge_sha=self.synthetic.head,
            run_id="12345",
            run_head_sha=self.synthetic.head,
            adopt_legacy=True,
        )
        lease = json.loads(lease_path.read_text())

        self.assertTrue(lease["adopted_legacy"])
        self.assertEqual(lease["terminal"]["closed_by"], "orchestrator-session-7")


@tag("core")
class ClassificationContractTest(SyntheticRepoTestCase):
    def test_shared_main_outside_and_broken_directory_are_protected(self):
        broken = self.synthetic.root / ".claude" / "worktrees" / "broken"
        broken.mkdir(parents=True)
        service = self.synthetic.service()

        main = service.classify_path(self.synthetic.root)
        outside = service.classify_path(self.synthetic.root.parent / "outside")
        broken_plan = service.classify_path(broken)

        self.assertEqual(main.classification, cleanup.PROTECTED_SHARED_MAIN)
        self.assertEqual(outside.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        self.assertEqual(broken_plan.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        self.assertTrue(broken.exists())

    def test_tracked_and_untracked_changes_are_dirty(self):
        tracked = self.synthetic.add_worktree("tracked")
        untracked = self.synthetic.add_worktree("untracked")
        tracked.joinpath("tracked.txt").write_text("changed\n")
        untracked.joinpath("new.txt").write_text("valuable\n")
        service = self.synthetic.service()
        self.synthetic.terminal(service, tracked)
        self.synthetic.terminal(service, untracked)

        for path in (tracked, untracked):
            plan = service.classify_path(path)
            self.assertIn(cleanup.RETAIN_DIRTY, plan.reasons)
            self.assertTrue(path.exists())

    def test_clean_unmerged_head_and_branch_are_retained(self):
        path = self.synthetic.add_worktree("unmerged")
        path.joinpath("new.txt").write_text("commit\n")
        self.synthetic.git("add", "new.txt", cwd=path)
        self.synthetic.git("commit", "-m", "valuable branch-only work", cwd=path)
        service = self.synthetic.service()
        service.create_lease(path=path, issue=1442, role="software-engineer")

        plan = service.classify_path(path)

        self.assertIn(cleanup.RETAIN_UNMERGED_HEAD, plan.reasons)
        self.assertTrue(path.exists())
        self.assertEqual(self.synthetic.git("branch", "--list", "worktree-unmerged").returncode, 0)

    def test_active_process_and_incomplete_visibility_are_independent_vetoes(self):
        path = self.synthetic.add_worktree()
        active = cleanup.ProcessScan(True, (cleanup.ProcessUse(501, ("cwd",)),))
        service = self.synthetic.service(scanner=StaticScanner(active))
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)
        self.assertIn(cleanup.RETAIN_ACTIVE_PROCESS, plan.reasons)

        incomplete = cleanup.ProcessScan(False, errors=("pid=9:fd:PermissionError",))
        service.process_scanner = StaticScanner(incomplete)
        plan = service.classify_path(path)
        self.assertIn(cleanup.RETAIN_MISSING_OR_UNCLASSIFIED, plan.reasons)

    def test_command_failure_fails_closed(self):
        path = self.synthetic.add_worktree()
        base_runner = cleanup.CommandRunner()

        def failing_runner(args, *, cwd=None):
            if list(args[:3]) == ["git", "status", "--porcelain=v1"] and Path(cwd) == path:
                return cleanup.CommandResult(b"", b"simulated status failure", 70)
            return base_runner(args, cwd=cwd)

        service = self.synthetic.service(runner=failing_runner)
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)

        self.assertIn(cleanup.RETAIN_MISSING_OR_UNCLASSIFIED, plan.reasons)
        self.assertIn("simulated status failure", plan.errors)

    def test_digest_is_stable_across_timestamp_and_actor_but_facts_change_it(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service(actor="one")
        self.synthetic.terminal(service, path)
        first = service.classify_path(path)
        service.actor = "two"
        service.now = lambda: "2026-08-14T01:00:00Z"
        second = service.classify_path(path)
        self.assertEqual(first.plan_digest, second.plan_digest)

        path.joinpath("late.txt").write_text("drift\n")
        third = service.classify_path(path)
        self.assertNotEqual(first.plan_digest, third.plan_digest)


@tag("core")
class ApplyContractTest(SyntheticRepoTestCase):
    def test_digest_bound_apply_removes_one_eligible_worktree_and_merged_branch(self):
        path = self.synthetic.add_worktree("eligible")
        other = self.synthetic.add_worktree("other")
        runner = RecordingRunner()
        service = self.synthetic.service(runner=runner)
        self.synthetic.terminal(service, path)
        service.create_lease(path=other, issue=9999, role="tester")
        plan = service.classify_path(path)

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(
            result.completed_actions,
            [f"git worktree remove {path}", "git branch -d worktree-eligible"],
        )
        self.assertFalse(path.exists())
        self.assertTrue(other.exists())
        self.assertNotEqual(
            self.synthetic.git("show-ref", "--verify", "refs/heads/worktree-eligible", check=False).returncode,
            0,
        )
        # Lease evidence survives because it is stored in the common Git dir.
        self.assertTrue(service._lease_path(path).exists())
        mutating_calls = [call for call, _ in runner.calls if call[:3] in (["git", "worktree", "remove"], ["git", "branch", "-d"])]
        self.assertEqual(mutating_calls[0][:3], ["git", "worktree", "remove"])
        self.assertEqual(mutating_calls[1][:3], ["git", "branch", "-d"])
        self.assertNotIn("--force", [part for call in mutating_calls for part in call])

    def test_apply_revalidates_and_refuses_plan_drift_without_mutation(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)
        path.joinpath("new.txt").write_text("arrived after review\n")

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)

        self.assertEqual(result.exit_status, 2)
        self.assertIn("digest", result.errors[-1])
        self.assertTrue(path.exists())
        self.assertEqual(path.joinpath("new.txt").read_text(), "arrived after review\n")

    def test_wrong_issue_and_repeated_apply_refuse_explicitly(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)

        wrong = service.remove(path=path, issue=7, plan_digest=plan.plan_digest)
        self.assertEqual(wrong.exit_status, 2)
        self.assertTrue(path.exists())

        first = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        second = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        self.assertEqual(first.exit_status, 0)
        self.assertEqual(second.exit_status, 2)

    def test_detached_worktree_has_no_branch_deletion_action(self):
        path = self.synthetic.add_worktree("detached", detached=True)
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(result.completed_actions, [f"git worktree remove {path}"])


@tag("core")
class StaleMetadataContractTest(SyntheticRepoTestCase):
    def make_stale(self, name, *, terminal=True):
        path = self.synthetic.add_worktree(name)
        service = self.synthetic.service()
        if terminal:
            self.synthetic.terminal(service, path)
        shutil.rmtree(path)
        return service, path

    def test_absent_terminal_registration_is_eligible_and_prune_deletes_no_directory(self):
        service, path = self.make_stale("stale-safe")
        plans = service.classify_stale()
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].classification, cleanup.STALE_REGISTRATION_ELIGIBLE_PRUNE)

        result = service.prune_stale(plan_digest=service.stale_digest(plans))

        self.assertEqual(result["exit_status"], 0)
        self.assertEqual(result["completed_actions"], ["git worktree prune --expire now"])
        self.assertFalse(path.exists())
        listed = cleanup.parse_worktree_porcelain(service._git("worktree", "list", "--porcelain", "-z").stdout)
        self.assertNotIn(path, [worktree.path for worktree in listed])

    def test_any_missing_or_active_evidence_refuses_entire_prune(self):
        service, safe_path = self.make_stale("safe")
        _, unsafe_path = self.make_stale("unsafe", terminal=False)
        plans = service.classify_stale()

        result = service.prune_stale(plan_digest=service.stale_digest(plans))

        self.assertEqual(result["exit_status"], 2)
        self.assertTrue(result["errors"])
        listed = cleanup.parse_worktree_porcelain(service._git("worktree", "list", "--porcelain", "-z").stdout)
        self.assertIn(safe_path, [worktree.path for worktree in listed])
        self.assertIn(unsafe_path, [worktree.path for worktree in listed])

    def test_stale_digest_drift_refuses_prune(self):
        service, _ = self.make_stale("stale")

        result = service.prune_stale(plan_digest="0" * 64)

        self.assertEqual(result["exit_status"], 2)
        self.assertIn("digest", result["errors"][0])

    def test_stale_registration_with_unmerged_head_is_retained(self):
        path = self.synthetic.add_worktree("stale-unmerged")
        path.joinpath("branch-only.txt").write_text("valuable\n")
        self.synthetic.git("add", "branch-only.txt", cwd=path)
        self.synthetic.git("commit", "-m", "branch-only", cwd=path)
        service = self.synthetic.service()
        service.create_lease(path=path, issue=1442, role="software-engineer")
        # Synthetic terminal evidence deliberately points at the shipped main
        # SHA while the candidate itself retains a distinct valuable HEAD.
        lease, _ = service.read_lease(path)
        lease.update(
            {
                "state": "terminal",
                "updated_at": "2026-08-13T20:00:00Z",
                "terminal": {
                    "merge_sha": self.synthetic.head,
                    "run_id": "12345",
                    "run_head_sha": self.synthetic.head,
                    "result": "success",
                    "roles_ended": True,
                    "closed_by": "orchestrator",
                    "closed_at": "2026-08-13T20:00:00Z",
                },
            }
        )
        service._write_lease(path, lease)
        shutil.rmtree(path)

        plan = service.classify_stale()[0]

        self.assertIn(cleanup.RETAIN_UNMERGED_HEAD, plan.reasons)
        self.assertNotEqual(plan.classification, cleanup.STALE_REGISTRATION_ELIGIBLE_PRUNE)

    def test_locked_stale_registration_refuses_entire_prune(self):
        path = self.synthetic.add_worktree("stale-locked")
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)
        self.synthetic.git("worktree", "lock", path)
        shutil.rmtree(path)

        plan = service.classify_stale()[0]
        result = service.prune_stale(plan_digest=service.stale_digest([plan]))

        self.assertEqual(plan.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        self.assertIn("registration is locked", plan.errors)
        self.assertEqual(result["exit_status"], 2)

    def test_stale_branch_deletion_requires_explicit_reviewed_option(self):
        service, _ = self.make_stale("stale-branch")
        branch = "worktree-stale-branch"
        default_plans = service.classify_stale()
        default_result = service.prune_stale(plan_digest=service.stale_digest(default_plans))
        self.assertEqual(default_result["exit_status"], 0)
        self.assertEqual(self.synthetic.git("show-ref", "--verify", f"refs/heads/{branch}").returncode, 0)

        # Recreate a second stale registration and explicitly include branch
        # deletion in both the reviewed digest and apply invocation.
        service, _ = self.make_stale("stale-branch-explicit")
        explicit_branch = "worktree-stale-branch-explicit"
        plans = service.classify_stale(delete_merged_branches=True)
        result = service.prune_stale(
            plan_digest=service.stale_digest(plans),
            delete_merged_branches=True,
        )

        self.assertEqual(result["exit_status"], 0)
        self.assertIn(f"git branch -d {explicit_branch}", result["completed_actions"])
        self.assertNotEqual(
            self.synthetic.git("show-ref", "--verify", f"refs/heads/{explicit_branch}", check=False).returncode,
            0,
        )


@tag("core")
class OutputContractTest(SyntheticRepoTestCase):
    def test_json_and_human_output_are_attributable_without_environment(self):
        path = self.synthetic.add_worktree()
        service = self.synthetic.service(actor="orchestrator-session-8")
        service.create_lease(path=path, issue=1442, role="tester")
        plan = service.classify_path(path)

        payload = plan.public_dict()
        rendered = json.dumps(payload, sort_keys=True)
        human = cleanup.render_human([plan])

        self.assertEqual(payload["actor"], "orchestrator-session-8")
        self.assertEqual(payload["repository"], str(self.synthetic.root))
        self.assertEqual(payload["issue"], 1442)
        self.assertIn(cleanup.RETAIN_ACTIVE_LIFECYCLE, human)
        for field_name in cleanup.HUMAN_PLAN_FIELDS:
            self.assertIn(f"  {field_name}=", human)
        self.assertIn('  actor="orchestrator-session-8"', human)
        self.assertIn('  mode="remove"', human)
        self.assertIn(f'  repository="{self.synthetic.root}"', human)
        self.assertIn(f'  common_dir="{self.synthetic.root / ".git"}"', human)
        self.assertIn("  process_ids=[]", human)
        self.assertNotIn("environ", rendered.lower())
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn("environ", human.lower())
        self.assertNotIn("token", human.lower())

    def test_successful_apply_human_output_reports_each_completed_action(self):
        path = self.synthetic.add_worktree("human-success")
        service = self.synthetic.service(actor="orchestrator-success")
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        human = cleanup.render_human([result])

        self.assertEqual(result.exit_status, 0)
        self.assertIn(
            f'  completed_actions=["git worktree remove {path}","git branch -d worktree-human-success"]',
            human,
        )
        self.assertIn(f'  action="git worktree remove {path}" status=success', human)
        self.assertIn('  action="git branch -d worktree-human-success" status=success', human)
        self.assertIn("  exit_status=0", human)
        self.assertIn("  errors=[]", human)

    def test_refused_apply_human_output_reports_action_failures_and_error(self):
        path = self.synthetic.add_worktree("human-refusal")
        service = self.synthetic.service(actor="orchestrator-refusal")
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)
        path.joinpath("late.txt").write_text("valuable\n")

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        human = cleanup.render_human([result])

        self.assertEqual(result.exit_status, 2)
        self.assertIn(f'  action="git worktree remove {path}" status=failure', human)
        self.assertIn('  action="git branch -d worktree-human-refusal" status=failure', human)
        self.assertIn("reviewed plan digest does not match recomputed facts", human)
        self.assertIn("  exit_status=2", human)
        self.assertTrue(path.exists())

    def test_partial_apply_human_output_distinguishes_remove_success_from_branch_failure(self):
        path = self.synthetic.add_worktree("human-partial")
        runner = FailingBranchDeleteRunner()
        service = self.synthetic.service(actor="orchestrator-partial", runner=runner)
        self.synthetic.terminal(service, path)
        plan = service.classify_path(path)

        result = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        human = cleanup.render_human([result])

        self.assertEqual(result.exit_status, 1)
        self.assertIn(f'  action="git worktree remove {path}" status=success', human)
        self.assertIn('  action="git branch -d worktree-human-partial" status=failure', human)
        self.assertIn("simulated branch refusal", human)
        self.assertIn("  exit_status=1", human)
        self.assertFalse(path.exists())
        self.assertEqual(
            self.synthetic.git("show-ref", "--verify", "refs/heads/worktree-human-partial").returncode,
            0,
        )

    def test_stale_prune_human_output_has_summary_candidate_and_action_status(self):
        path = self.synthetic.add_worktree("human-stale")
        service = self.synthetic.service(actor="orchestrator-stale")
        self.synthetic.terminal(service, path)
        shutil.rmtree(path)
        plans = service.classify_stale(delete_merged_branches=True)

        result = service.prune_stale(
            plan_digest=service.stale_digest(plans),
            delete_merged_branches=True,
        )
        human = cleanup.render_stale_human(result)

        self.assertEqual(result["exit_status"], 0)
        self.assertIn("apply-summary", human)
        self.assertIn("candidate=1", human)
        self.assertIn('  actor="orchestrator-stale"', human)
        self.assertIn(f'  repository="{self.synthetic.root}"', human)
        self.assertIn(f'  common_dir="{self.synthetic.root / ".git"}"', human)
        self.assertIn('  action="git worktree prune --expire now" status=success', human)
        self.assertIn('  action="git branch -d worktree-human-stale" status=success', human)
        self.assertIn("  exit_status=0", human)
