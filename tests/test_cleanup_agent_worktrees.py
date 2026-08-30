"""Offline safety contracts for ``scripts/cleanup-agent-worktrees.py``.

Every Git mutation in this module is confined to a synthetic repository below
the project's gitignored ``.tmp/`` directory. No existing worktree is ever
passed to the cleanup service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, tag

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "scripts" / "cleanup-agent-worktrees.py"
_spec = importlib.util.spec_from_file_location("cleanup_agent_worktrees", MODULE_PATH)
assert _spec and _spec.loader
cleanup = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_agent_worktrees"] = cleanup
_spec.loader.exec_module(cleanup)


def snapshot_preexisting_real_state():
    """Capture repository-global state that synthetic cleanup tests must not touch."""

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    common_raw = git("rev-parse", "--git-common-dir").decode().strip()
    common_dir = Path(common_raw)
    if not common_dir.is_absolute():
        common_dir = (PROJECT_ROOT / common_dir).resolve()
    lease_dir = common_dir / cleanup.LEASE_DIRNAME
    lease_records = []
    if lease_dir.exists():
        for record in sorted(lease_dir.iterdir(), key=lambda item: item.name):
            if record.is_symlink():
                lease_records.append((record.name, "symlink", os.readlink(record)))
            elif record.is_file():
                raw = record.read_bytes()
                lease_records.append((record.name, "file", hashlib.sha256(raw).hexdigest()))
            else:
                lease_records.append((record.name, "other", None))

    worktrees_raw = git("worktree", "list", "--porcelain", "-z")
    worktrees = cleanup.parse_worktree_porcelain(worktrees_raw)
    main = next(worktree for worktree in worktrees if worktree.branch_ref == "refs/heads/main")
    configured_boundary = main.path / ".claude" / "worktrees"
    boundary = (
        str(configured_boundary),
        configured_boundary.is_symlink(),
        os.readlink(configured_boundary) if configured_boundary.is_symlink() else None,
        str(configured_boundary.resolve(strict=True)),
    )

    processes = []
    for pid in sorted({os.getpid(), os.getppid()}):
        proc = Path("/proc") / str(pid)
        if not proc.exists():
            continue
        stat_fields = (proc / "stat").read_text().split()
        processes.append((pid, stat_fields[21], os.readlink(proc / "cwd")))

    return {
        "lease_records": tuple(lease_records),
        "worktrees": worktrees_raw,
        "branches": git("for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads"),
        "boundary": boundary,
        "processes": tuple(processes),
    }


def snapshot_entry(path):
    if not os.path.lexists(path):
        return ("missing",)
    metadata = path.lstat()
    if path.is_symlink():
        return ("symlink", os.readlink(path), metadata.st_ino)
    if path.is_file():
        return ("file", path.read_bytes(), metadata.st_ino)
    return ("other", metadata.st_mode, metadata.st_ino)


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
        self.sandbox = Path(tempfile.mkdtemp(prefix="repo-", dir=temp_root)).resolve()
        self.root = self.sandbox / "main"
        self.root.mkdir()
        self.external_worktrees = self.sandbox / "external-worktrees"
        self.git("init", "-b", "main")
        self.git("config", "user.email", "tests@example.com")
        self.git("config", "user.name", "Cleanup Tests")
        (self.root / "tracked.txt").write_text("base\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "base")
        self.git("update-ref", "refs/remotes/origin/main", self.head)

    def close(self):
        assert self.sandbox.is_relative_to(PROJECT_ROOT / ".tmp")
        shutil.rmtree(self.sandbox, ignore_errors=True)

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

    def add_worktree(self, name="agent-1442", *, detached=False, parent=None):
        path = (parent or self.root / ".claude" / "worktrees") / name
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

    def service(self, *, scanner=None, runner=None, actor="orchestrator", migration_hook=None):
        return cleanup.CleanupService(
            self.root,
            actor=actor,
            runner=runner,
            gh_runner=self.gh,
            process_scanner=scanner or StaticScanner(),
            now=lambda: "2026-08-13T20:00:00Z",
            migration_hook=migration_hook,
        )

    def enable_external_boundary(self):
        configured = self.root / ".claude" / "worktrees"
        configured.parent.mkdir(parents=True, exist_ok=True)
        self.external_worktrees.mkdir()
        configured.symlink_to(self.external_worktrees, target_is_directory=True)
        return configured

    def legacy_alias_path(self, path):
        return self.root / ".claude" / "worktrees" / path.name

    def write_legacy_lease(self, service, path, *, issue=1491, state="active", filename_key=None):
        alias = self.legacy_alias_path(path)
        timestamp = "2026-08-13T20:00:00Z"
        lease = {
            "version": 1,
            "issue": issue,
            "path": str(alias),
            "state": state,
            "actor": "historical-orchestrator",
            "role": "software-engineer",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if state == "terminal":
            lease["terminal"] = {
                "merge_sha": self.head,
                "run_id": "12345",
                "run_head_sha": self.head,
                "result": "success",
                "roles_ended": True,
                "closed_by": "historical-orchestrator",
                "closed_at": timestamp,
            }
        service.lease_dir.mkdir(parents=True, exist_ok=True)
        key = filename_key or cleanup.lease_key(alias)
        lease_path = service.lease_dir / f"{key}.json"
        lease_path.write_text(json.dumps(lease, sort_keys=True, indent=2) + "\n")
        return lease_path, lease

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
class RepositoryContractTest(SyntheticRepoTestCase):
    def install_shipped_gitignore(self):
        contents = PROJECT_ROOT.joinpath(".gitignore").read_text()
        patterns = {line.strip() for line in contents.splitlines()}
        self.assertIn(".claude/worktrees", patterns)
        self.assertNotIn(".claude/worktrees/", patterns)
        self.synthetic.root.joinpath(".gitignore").write_text(contents)

    def untracked_paths(self):
        result = self.synthetic.git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        return result.stdout.decode().splitlines()

    def test_shipped_ignore_rule_ignores_symlink_object_only_at_configured_path(self):
        self.install_shipped_gitignore()
        claude = self.synthetic.root / ".claude"
        claude.mkdir()
        self.synthetic.external_worktrees.mkdir()
        claude.joinpath("worktrees").symlink_to(
            self.synthetic.external_worktrees,
            target_is_directory=True,
        )
        claude.joinpath("visible.txt").write_text("visible\n")

        paths = self.untracked_paths()

        self.assertNotIn("?? .claude/worktrees", paths)
        self.assertIn("?? .claude/visible.txt", paths)
        self.assertEqual(
            self.synthetic.git("check-ignore", "--quiet", ".claude/worktrees").returncode,
            0,
        )

    def test_shipped_ignore_rule_ignores_normal_directory_and_descendants(self):
        self.install_shipped_gitignore()
        worktrees = self.synthetic.root / ".claude" / "worktrees"
        worktrees.mkdir(parents=True)
        worktrees.joinpath("agent", "valuable.txt").parent.mkdir()
        worktrees.joinpath("agent", "valuable.txt").write_text("ignored\n")
        self.synthetic.root.joinpath(".claude", "visible.txt").write_text("visible\n")

        paths = self.untracked_paths()

        self.assertNotIn("?? .claude/worktrees/agent/valuable.txt", paths)
        self.assertIn("?? .claude/visible.txt", paths)
        self.assertEqual(
            self.synthetic.git(
                "check-ignore",
                "--quiet",
                ".claude/worktrees/agent/valuable.txt",
            ).returncode,
            0,
        )

    def test_process_contract_makes_post_green_cleanup_an_attributable_gate(self):
        process = PROJECT_ROOT.joinpath("_docs", "PROCESS.md").read_text()
        normalized = " ".join(process.split())

        for required in (
            "delivery-pipeline completion gate for every",
            "Report every removed path exactly",
            "exact classifier reason codes and errors",
            "never authorizes force",
            "authoritative external active-agent registry check remains required",
            "OS process absence is not role-completion evidence",
            "lease-reconcile --path",
            "lease-reconcile --apply --path",
            "`lease-adopt` must never be used",
            "migration success is not cleanup eligibility",
        ):
            self.assertIn(required, normalized)


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
class LegacyAliasReconciliationContractTest(SyntheticRepoTestCase):
    def setUp(self):
        super().setUp()
        self.alias_root = self.synthetic.enable_external_boundary()

    def test_active_alias_is_authoritative_and_blocks_adoption_or_close(self):
        path = self.synthetic.add_worktree("legacy-active")
        service = self.synthetic.service(actor="migration-operator")
        source, lease = self.synthetic.write_legacy_lease(service, path)

        plan = service.classify_path(path)

        self.assertEqual(plan.classification, cleanup.RETAIN_ACTIVE_LIFECYCLE)
        self.assertEqual(plan.issue, lease["issue"])
        self.assertEqual(plan.lease_lookup_source, "legacy-alias")
        self.assertEqual(plan.lease_source_file, str(source))
        self.assertEqual(plan.lease_stored_path, str(self.synthetic.legacy_alias_path(path)))
        self.assertEqual(plan.lease_source_key, source.stem)
        self.assertEqual(plan.lease_canonical_path, str(path))
        self.assertEqual(plan.lease_canonical_key, cleanup.lease_key(path))
        self.assertTrue(plan.lease_content_digest)
        before = source.read_bytes()
        with self.assertRaisesMessage(cleanup.CleanupError, "blocked by"):
            service.close_lease(
                path=path,
                issue=1491,
                merge_sha=self.synthetic.head,
                run_id="12345",
                run_head_sha=self.synthetic.head,
                adopt_legacy=True,
            )
        with self.assertRaisesMessage(cleanup.CleanupError, "must be reconciled"):
            service.close_lease(
                path=path,
                issue=1491,
                merge_sha=self.synthetic.head,
                run_id="12345",
                run_head_sha=self.synthetic.head,
            )
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(service._lease_path(path).exists())

    def test_reconciliation_plan_has_stable_attributable_human_and_json_fields(self):
        path = self.synthetic.add_worktree("legacy-output")
        service = self.synthetic.service(actor="migration-operator")
        source, lease = self.synthetic.write_legacy_lease(service, path)

        plan = service.reconcile_lease_plan(path=path, roles_ended=True)
        payload = plan.public_dict()
        human = cleanup.render_human([plan])

        self.assertEqual(payload["mode"], "reconcile-lease")
        self.assertEqual(payload["lease_lookup_source"], "legacy-alias")
        self.assertEqual(payload["lease_source_file"], str(source))
        self.assertEqual(payload["lease_stored_path"], lease["path"])
        self.assertEqual(payload["lease_actor"], lease["actor"])
        self.assertEqual(payload["lease_role"], lease["role"])
        self.assertEqual(payload["lease_created_at"], lease["created_at"])
        self.assertEqual(payload["lease_updated_at"], lease["updated_at"])
        self.assertTrue(payload["roles_ended_asserted"])
        directory = payload["lease_directory_snapshot"]
        self.assertEqual(directory["path"], str(service.lease_dir))
        self.assertEqual(directory["resolved_path"], str(service.lease_dir))
        self.assertEqual(directory["errors"], [])
        self.assertEqual(directory["lstat"]["kind"], "directory")
        self.assertEqual(directory["opened"]["kind"], "directory")
        self.assertEqual(
            (
                directory["lstat"]["device"],
                directory["lstat"]["inode"],
                directory["lstat"]["mode"],
            ),
            (
                directory["opened"]["device"],
                directory["opened"]["inode"],
                directory["opened"]["mode"],
            ),
        )
        self.assertIn("ELIGIBLE_LEASE_MIGRATION", human)
        self.assertIn(str(source), human)
        self.assertIn("lease_directory_snapshot", human)
        self.assertIn(plan.plan_digest, human)

    def test_malformed_wrong_key_alternate_root_and_collision_fail_closed(self):
        cases = ("malformed", "wrong-key", "alternate-root", "collision")
        for name in cases:
            with self.subTest(name=name):
                path = self.synthetic.add_worktree(name)
                service = self.synthetic.service()
                alias = self.synthetic.legacy_alias_path(path)
                if name == "malformed":
                    service.lease_dir.mkdir(parents=True, exist_ok=True)
                    evidence = service.lease_dir / f"{cleanup.lease_key(alias)}.json"
                    evidence.write_text("not-json")
                elif name == "wrong-key":
                    evidence, _ = self.synthetic.write_legacy_lease(
                        service,
                        path,
                        filename_key="f" * 64,
                    )
                elif name == "alternate-root":
                    alternate = self.synthetic.sandbox / "alternate"
                    alternate.symlink_to(self.synthetic.external_worktrees, target_is_directory=True)
                    stored = alternate / path.name
                    payload = {
                        "version": 1,
                        "issue": 1491,
                        "path": str(stored),
                        "state": "active",
                        "actor": "historical",
                        "created_at": "now",
                        "updated_at": "now",
                    }
                    service.lease_dir.mkdir(parents=True, exist_ok=True)
                    evidence = service.lease_dir / f"{cleanup.lease_key(stored)}.json"
                    evidence.write_text(json.dumps(payload))
                else:
                    evidence, _ = self.synthetic.write_legacy_lease(service, path)
                    canonical = {
                        "version": 1,
                        "issue": 1491,
                        "path": str(path),
                        "state": "active",
                        "actor": "current",
                        "created_at": "now",
                        "updated_at": "now",
                    }
                    service._lease_path(path).write_text(json.dumps(canonical))

                before = {item: item.read_bytes() for item in service.lease_dir.iterdir()}
                plan = service.classify_path(path)
                reconcile = service.reconcile_lease_plan(path=path, roles_ended=True)

                self.assertEqual(plan.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
                self.assertNotEqual(reconcile.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
                with self.assertRaises(cleanup.CleanupError):
                    service.close_lease(
                        path=path,
                        issue=1491,
                        merge_sha=self.synthetic.head,
                        run_id="12345",
                        run_head_sha=self.synthetic.head,
                        adopt_legacy=True,
                    )
                self.assertEqual(
                    {item: item.read_bytes() for item in service.lease_dir.iterdir()},
                    before,
                )

    def test_unsupported_and_nested_alias_evidence_fail_closed(self):
        for name in ("unsupported", "nested"):
            with self.subTest(name=name):
                path = self.synthetic.add_worktree(name)
                service = self.synthetic.service()
                if name == "unsupported":
                    evidence, payload = self.synthetic.write_legacy_lease(service, path)
                    payload["version"] = 2
                else:
                    nested_link = self.synthetic.external_worktrees / "nested-alias"
                    nested_link.symlink_to(
                        self.synthetic.external_worktrees,
                        target_is_directory=True,
                    )
                    stored = self.alias_root / "nested-alias" / path.name
                    payload = {
                        "version": 1,
                        "issue": 1491,
                        "path": str(stored),
                        "state": "active",
                        "actor": "historical-orchestrator",
                        "role": "software-engineer",
                        "created_at": "2026-08-13T20:00:00Z",
                        "updated_at": "2026-08-13T20:00:00Z",
                    }
                    service.lease_dir.mkdir(parents=True, exist_ok=True)
                    evidence = service.lease_dir / f"{cleanup.lease_key(stored)}.json"
                evidence.write_text(json.dumps(payload, sort_keys=True) + "\n")
                before = evidence.read_bytes()

                lookup = service._lookup_lease(path)
                classification = service.classify_path(path)
                reconciliation = service.reconcile_lease_plan(path=path, roles_ended=True)

                self.assertIsNone(lookup.lease)
                self.assertEqual(
                    lookup.evidence_sources,
                    ((str(evidence), evidence.stem),),
                )
                self.assertEqual(
                    classification.classification,
                    cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
                )
                self.assertEqual(
                    reconciliation.classification,
                    cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
                )
                with self.assertRaises(cleanup.CleanupError) as raised:
                    service.close_lease(
                        path=path,
                        issue=1491,
                        merge_sha=self.synthetic.head,
                        run_id="12345",
                        run_head_sha=self.synthetic.head,
                        adopt_legacy=True,
                    )
                self.assertIn(f"path={evidence};key={evidence.stem}", str(raised.exception))
                self.assertEqual(evidence.read_bytes(), before)
                self.assertFalse(service._lease_path(path).exists())

    def test_multiple_aliases_report_every_conflicting_path_and_key_deterministically(self):
        path = self.synthetic.add_worktree("multiple-aliases")
        service = self.synthetic.service()
        exact, _ = self.synthetic.write_legacy_lease(service, path)
        alternate_root = self.synthetic.sandbox / "second-alias"
        alternate_root.symlink_to(self.synthetic.external_worktrees, target_is_directory=True)
        alternate_path = alternate_root / path.name
        alternate_payload = {
            "version": 1,
            "issue": 1491,
            "path": str(alternate_path),
            "state": "active",
            "actor": "other-historical-orchestrator",
            "role": "tester",
            "created_at": "2026-08-13T20:00:00Z",
            "updated_at": "2026-08-13T20:00:00Z",
        }
        alternate = service.lease_dir / f"{cleanup.lease_key(alternate_path)}.json"
        alternate.write_text(json.dumps(alternate_payload, sort_keys=True) + "\n")
        conflicts = tuple((str(item), item.stem) for item in sorted((exact, alternate), key=lambda item: item.name))
        expected_description = ",".join(f"path={source_file};key={source_key}" for source_file, source_key in conflicts)
        before = {item: item.read_bytes() for item in (exact, alternate)}

        lookup = service._lookup_lease(path)
        reconciliation = service.reconcile_lease_plan(path=path, roles_ended=True)
        expected_sources = [
            {"source_file": source_file, "source_key": source_key} for source_file, source_key in conflicts
        ]

        self.assertEqual(lookup.evidence_sources, conflicts)
        self.assertIn(expected_description, lookup.errors[0])
        self.assertEqual(reconciliation.facts["lookup"]["evidence_sources"], expected_sources)
        self.assertEqual(reconciliation.public_dict()["lease_evidence_sources"], expected_sources)
        human = cleanup.render_human([reconciliation])
        for source_file, source_key in conflicts:
            self.assertIn(f"path={source_file};key={source_key}", "\n".join(reconciliation.errors))
            self.assertIn(source_file, human)
            self.assertIn(source_key, human)
        with self.assertRaises(cleanup.CleanupError) as raised:
            service.close_lease(
                path=path,
                issue=1491,
                merge_sha=self.synthetic.head,
                run_id="12345",
                run_head_sha=self.synthetic.head,
                adopt_legacy=True,
            )
        self.assertEqual(
            str(raised.exception),
            "legacy adoption requires genuinely absent evidence; blocked by " + expected_description,
        )
        self.assertEqual({item: item.read_bytes() for item in (exact, alternate)}, before)
        self.assertFalse(service._lease_path(path).exists())

    def test_alias_and_canonical_nonregular_evidence_never_follow_or_classify(self):
        for key_kind in ("legacy-alias", "canonical"):
            for entry_kind in ("external", "broken", "loop", "fifo", "directory"):
                with self.subTest(key_kind=key_kind, entry_kind=entry_kind):
                    synthetic = SyntheticRepo()
                    try:
                        synthetic.enable_external_boundary()
                        path = synthetic.add_worktree(f"{key_kind}-{entry_kind}")
                        service = synthetic.service()
                        alias = synthetic.legacy_alias_path(path)
                        evidence_path = alias if key_kind == "legacy-alias" else path
                        evidence = service.lease_dir / f"{cleanup.lease_key(evidence_path)}.json"
                        service.lease_dir.mkdir(parents=True, exist_ok=True)
                        payload = {
                            "version": 1,
                            "issue": 1491,
                            "path": str(evidence_path),
                            "state": "active",
                            "actor": "historical-orchestrator",
                            "role": "software-engineer",
                            "created_at": "2026-08-13T20:00:00Z",
                            "updated_at": "2026-08-13T20:00:00Z",
                        }
                        external = synthetic.sandbox / f"external-{key_kind}-{entry_kind}.json"
                        if entry_kind == "external":
                            external.write_text(json.dumps(payload, sort_keys=True) + "\n")
                            evidence.symlink_to(external)
                        elif entry_kind == "broken":
                            evidence.symlink_to(synthetic.sandbox / "missing-evidence.json")
                        elif entry_kind == "loop":
                            evidence.symlink_to(evidence.name)
                        elif entry_kind == "fifo":
                            os.mkfifo(evidence)
                        else:
                            evidence.mkdir()
                        link_target = os.readlink(evidence) if evidence.is_symlink() else None
                        external_before = external.read_bytes() if external.exists() else None

                        lookup = service._lookup_lease(path)
                        classification = service.classify_path(path)
                        reconciliation = service.reconcile_lease_plan(
                            path=path,
                            roles_ended=True,
                        )

                        self.assertIsNone(lookup.lease)
                        self.assertEqual(lookup.source, "invalid")
                        self.assertEqual(
                            lookup.evidence_sources,
                            ((str(evidence), evidence.stem),),
                        )
                        self.assertIn("not a regular file", "\n".join(lookup.errors))
                        self.assertEqual(
                            classification.classification,
                            cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
                        )
                        self.assertEqual(
                            reconciliation.classification,
                            cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
                        )
                        with self.assertRaises(cleanup.CleanupError) as raised:
                            service.close_lease(
                                path=path,
                                issue=1491,
                                merge_sha=synthetic.head,
                                run_id="12345",
                                run_head_sha=synthetic.head,
                                adopt_legacy=True,
                            )
                        self.assertIn(
                            f"path={evidence};key={evidence.stem}",
                            str(raised.exception),
                        )
                        self.assertTrue(os.path.lexists(evidence))
                        if link_target is not None:
                            self.assertTrue(evidence.is_symlink())
                            self.assertEqual(os.readlink(evidence), link_target)
                        if external_before is not None:
                            self.assertEqual(external.read_bytes(), external_before)
                    finally:
                        synthetic.close()

    def test_nonregular_migration_artifacts_are_detected_without_following(self):
        for artifact_kind in ("broken-new", "loop-staged", "external-new"):
            with self.subTest(artifact_kind=artifact_kind):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"artifact-{artifact_kind}")
                    service = synthetic.service()
                    source, _ = synthetic.write_legacy_lease(service, path)
                    _, new_record, staged_source = service._migration_paths(path)
                    artifact = staged_source if artifact_kind == "loop-staged" else new_record
                    if artifact_kind == "broken-new":
                        artifact.symlink_to(synthetic.sandbox / "missing-artifact")
                    elif artifact_kind == "loop-staged":
                        artifact.symlink_to(artifact.name)
                    else:
                        external = synthetic.sandbox / "external-artifact"
                        external.write_text("not authoritative\n")
                        artifact.symlink_to(external)
                    source_before = source.read_bytes()
                    link_target = os.readlink(artifact)

                    lookup = service._lookup_lease(path)
                    reconciliation = service.reconcile_lease_plan(
                        path=path,
                        roles_ended=True,
                    )

                    self.assertIsNone(lookup.lease)
                    self.assertIn("migration is incomplete", lookup.errors[0])
                    self.assertEqual(
                        reconciliation.classification,
                        cleanup.RETAIN_MISSING_OR_UNCLASSIFIED,
                    )
                    self.assertIn("not a regular file", "\n".join(reconciliation.errors))
                    self.assertEqual(source.read_bytes(), source_before)
                    self.assertTrue(artifact.is_symlink())
                    self.assertEqual(os.readlink(artifact), link_target)
                finally:
                    synthetic.close()

    def test_terminal_alias_migrates_path_only_and_never_runs_cleanup_commands(self):
        path = self.synthetic.add_worktree("legacy-terminal")
        runner = RecordingRunner()
        service = self.synthetic.service(runner=runner, actor="migration-session")
        source, original = self.synthetic.write_legacy_lease(service, path, state="terminal")

        before_migration = service.classify_path(path)
        plan = service.reconcile_lease_plan(path=path)
        result = service.migrate_lease(path=path, plan_digest=plan.plan_digest)

        self.assertEqual(plan.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
        self.assertEqual(before_migration.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        self.assertEqual(result.exit_status, 0)
        self.assertFalse(source.exists())
        canonical = service._lease_path(path)
        self.assertTrue(canonical.exists())
        migrated = json.loads(canonical.read_text())
        preserved = dict(migrated)
        migration = preserved.pop("path_migration")
        preserved["path"] = original["path"]
        self.assertEqual(preserved, original)
        self.assertEqual(migration["from_path"], original["path"])
        self.assertEqual(migration["from_key"], source.stem)
        self.assertEqual(migration["actor"], "migration-session")
        self.assertEqual(service.read_lease(path)[0]["state"], "terminal")
        self.assertEqual(service.classify_path(path).classification, cleanup.ELIGIBLE_REMOVE)
        mutating_git = [
            call
            for call, _ in runner.calls
            if call[:3]
            in (
                ["git", "worktree", "remove"],
                ["git", "worktree", "prune"],
                ["git", "branch", "-d"],
            )
        ]
        self.assertEqual(mutating_git, [])

    def test_active_orphan_requires_role_assertion_and_stays_active(self):
        path = self.synthetic.external_worktrees / "legacy-orphan"
        service = self.synthetic.service(actor="migration-session")
        source, _ = self.synthetic.write_legacy_lease(service, path, state="active")

        retained = service.reconcile_lease_plan(path=path)
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        migrated = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(retained.classification, cleanup.RETAIN_ACTIVE_LIFECYCLE)
        self.assertIn("--roles-ended", retained.errors[0])
        self.assertEqual(reviewed.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
        self.assertFalse(reviewed.registered)
        self.assertFalse(reviewed.path_exists)
        self.assertEqual(migrated.exit_status, 0)
        self.assertFalse(source.exists())
        lease, errors = service.read_lease(path)
        self.assertEqual(errors, [])
        self.assertEqual(lease["state"], "active")
        self.assertNotIn("terminal", lease)

    def test_terminal_evidence_and_source_drift_refuse_without_mutation(self):
        invalid_path = self.synthetic.add_worktree("invalid-terminal")
        service = self.synthetic.service()
        invalid_source, _ = self.synthetic.write_legacy_lease(service, invalid_path, state="terminal")
        invalid = json.loads(invalid_source.read_text())
        invalid["terminal"]["result"] = "failed"
        invalid_source.write_text(json.dumps(invalid))
        invalid_plan = service.reconcile_lease_plan(path=invalid_path)
        self.assertEqual(invalid_plan.classification, cleanup.RETAIN_TERMINAL_EVIDENCE_MISSING)
        self.assertTrue(invalid_source.exists())

        drift_path = self.synthetic.add_worktree("drift")
        drift_source, _ = self.synthetic.write_legacy_lease(service, drift_path, state="active")
        reviewed = service.reconcile_lease_plan(path=drift_path, roles_ended=True)
        drifted = json.loads(drift_source.read_text())
        drifted["role"] = "tester"
        drift_source.write_text(json.dumps(drifted))
        result = service.migrate_lease(
            path=drift_path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(result.exit_status, 2)
        self.assertIn("digest", result.errors[-1])
        self.assertTrue(drift_source.exists())
        self.assertFalse(service._lease_path(drift_path).exists())

    def test_every_required_after_review_drift_refuses_without_changing_source(self):
        drift_cases = (
            "boundary",
            "registration",
            "existence",
            "role-assertion",
            "terminal-evidence",
            "target-collision",
        )
        for drift_case in drift_cases:
            with self.subTest(drift_case=drift_case):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    registered = drift_case not in {"registration", "existence"}
                    path = (
                        synthetic.add_worktree(f"drift-{drift_case}")
                        if registered
                        else synthetic.external_worktrees / f"drift-{drift_case}"
                    )
                    service = synthetic.service(actor="migration-session")
                    state = "terminal" if drift_case == "terminal-evidence" else "active"
                    source, _ = synthetic.write_legacy_lease(service, path, state=state)
                    roles_ended = state == "active"
                    reviewed = service.reconcile_lease_plan(
                        path=path,
                        roles_ended=roles_ended,
                    )
                    self.assertEqual(reviewed.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
                    source_before = source.read_bytes()
                    target = service._lease_path(path)
                    target_before = None

                    if drift_case == "boundary":
                        configured = synthetic.root / ".claude" / "worktrees"
                        configured.unlink()
                        replacement = synthetic.sandbox / "replacement-boundary"
                        replacement.mkdir()
                        configured.symlink_to(replacement, target_is_directory=True)
                    elif drift_case == "registration":
                        self.assertEqual(
                            synthetic.add_worktree(f"drift-{drift_case}"),
                            path,
                        )
                    elif drift_case == "existence":
                        path.mkdir()
                    elif drift_case == "role-assertion":
                        roles_ended = False
                    elif drift_case == "terminal-evidence":
                        drifted = json.loads(source.read_text())
                        drifted["terminal"]["run_head_sha"] = "f" * 40
                        source.write_text(json.dumps(drifted, sort_keys=True, indent=2) + "\n")
                        source_before = source.read_bytes()
                    else:
                        target_before = b"operator-created collision\n"
                        target.write_bytes(target_before)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=roles_ended,
                    )

                    self.assertEqual(result.exit_status, 2)
                    self.assertIn("digest", result.errors[-1])
                    self.assertTrue(source.exists())
                    self.assertEqual(source.read_bytes(), source_before)
                    if target_before is None:
                        self.assertFalse(target.exists())
                    else:
                        self.assertEqual(target.read_bytes(), target_before)
                    _, new_record, staged_source = service._migration_paths(path)
                    self.assertFalse(new_record.exists())
                    self.assertFalse(staged_source.exists())
                finally:
                    synthetic.close()

    def test_same_registration_fact_drift_invalidates_reviewed_digest(self):
        expected_change = {
            "head": "head",
            "branch": "branch_ref",
            "detached": "detached",
            "locked": "locked",
            "prunable": "prunable",
        }
        for drift_case, changed_field in expected_change.items():
            with self.subTest(drift_case=drift_case):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"registration-{drift_case}")
                    service = synthetic.service(actor="migration-session")
                    source, _ = synthetic.write_legacy_lease(service, path)
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    self.assertEqual(reviewed.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
                    self.assertEqual(len(reviewed.registration_snapshot), 1)
                    self.assertEqual(
                        reviewed.registration_snapshot,
                        reviewed.facts["registration_snapshot"],
                    )
                    self.assertEqual(reviewed.registration_snapshot[0]["path"], str(path))
                    source_before = source.read_bytes()

                    if drift_case == "head":
                        path.joinpath("head-drift.txt").write_text("new head\n")
                        synthetic.git("add", "head-drift.txt", cwd=path)
                        synthetic.git("commit", "-m", "head drift", cwd=path)
                    elif drift_case == "branch":
                        synthetic.git("checkout", "-b", "replacement-branch", cwd=path)
                    elif drift_case == "detached":
                        synthetic.git("checkout", "--detach", cwd=path)
                    elif drift_case == "locked":
                        synthetic.git("worktree", "lock", path)
                    else:
                        shutil.rmtree(path)

                    recomputed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    self.assertNotEqual(
                        recomputed.registration_snapshot[0][changed_field],
                        reviewed.registration_snapshot[0][changed_field],
                    )
                    self.assertNotEqual(recomputed.plan_digest, reviewed.plan_digest)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    self.assertEqual(result.exit_status, 2)
                    self.assertIn("digest", result.errors[-1])
                    self.assertEqual(source.read_bytes(), source_before)
                    self.assertFalse(service._lease_path(path).exists())
                    _, new_record, staged_source = service._migration_paths(path)
                    self.assertFalse(os.path.lexists(new_record))
                    self.assertFalse(os.path.lexists(staged_source))
                finally:
                    synthetic.close()

    def test_transition_source_drift_is_retained_before_authority_changes(self):
        path = self.synthetic.add_worktree("transition-source-drift")
        source_holder = {}

        def drift_source(transition):
            if transition == "new-record-written":
                source = source_holder["source"]
                drifted = json.loads(source.read_text())
                drifted["role"] = "tester-drift"
                source.write_text(json.dumps(drifted, sort_keys=True, indent=2) + "\n")
                source_holder["drifted"] = source.read_bytes()

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=drift_source,
        )
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_holder["source"] = source
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        target, new_record, staged_source = service._migration_paths(path)
        self.assertEqual(result.exit_status, 1)
        self.assertIn("authoritative source changed", result.errors[-1])
        self.assertEqual(source.read_bytes(), source_holder["drifted"])
        self.assertTrue(new_record.exists())
        self.assertFalse(target.exists())
        self.assertFalse(staged_source.exists())

    def test_transition_registration_drift_refuses_before_source_deletion(self):
        for drift_case in ("head", "branch", "detached", "locked", "prunable"):
            with self.subTest(drift_case=drift_case):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"transition-registration-{drift_case}")

                    def drift_registration(transition):
                        if transition != "new-record-written":
                            return
                        if drift_case == "head":
                            path.joinpath("transition-head.txt").write_text("changed\n")
                            synthetic.git("add", "transition-head.txt", cwd=path)
                            synthetic.git("commit", "-m", "transition head", cwd=path)
                        elif drift_case == "branch":
                            synthetic.git("checkout", "-b", "transition-branch", cwd=path)
                        elif drift_case == "detached":
                            synthetic.git("checkout", "--detach", cwd=path)
                        elif drift_case == "locked":
                            synthetic.git("worktree", "lock", path)
                        else:
                            shutil.rmtree(path)

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=drift_registration,
                    )
                    source, _ = synthetic.write_legacy_lease(service, path)
                    source_before = source.read_bytes()
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    target, new_record, staged_source = service._migration_paths(path)
                    self.assertEqual(result.exit_status, 1)
                    self.assertIn("during migration", result.errors[-1])
                    self.assertEqual(source.read_bytes(), source_before)
                    self.assertTrue(new_record.exists())
                    self.assertFalse(target.exists())
                    self.assertFalse(staged_source.exists())
                finally:
                    synthetic.close()

    def test_review_digest_and_transition_context_bind_migration_actor(self):
        path = self.synthetic.add_worktree("actor-review-drift")
        service = self.synthetic.service(actor="reviewed-migration-actor")
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_before = snapshot_entry(source)
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        service.actor = "different-apply-actor"
        recomputed = service.reconcile_lease_plan(path=path, roles_ended=True)
        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertNotEqual(recomputed.plan_digest, reviewed.plan_digest)
        self.assertEqual(result.exit_status, 2)
        self.assertIn("digest", result.errors[-1])
        self.assertEqual(snapshot_entry(source), source_before)
        target, new_record, staged_source = service._migration_paths(path)
        self.assertFalse(os.path.lexists(target))
        self.assertFalse(os.path.lexists(new_record))
        self.assertFalse(os.path.lexists(staged_source))

    def test_actor_drift_at_every_transition_preserves_evidence(self):
        transitions = (
            "new-record-written",
            "source-staging-linked",
            "source-staged",
            "canonical-linked",
            "new-record-removed",
            "source-removed",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"actor-drift-{transition}")
                    holder = {}

                    def drift_actor(current):
                        if current == transition:
                            holder["service"].actor = f"drifted-{transition}"

                    service = synthetic.service(
                        actor="reviewed-migration-actor",
                        migration_hook=drift_actor,
                    )
                    holder["service"] = service
                    source, _ = synthetic.write_legacy_lease(service, path)
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    target, new_record, staged_source = service._migration_paths(path)
                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(result.completed_actions, [])
                    self.assertIn("migration actor changed", result.errors[-1])
                    self.assertTrue(os.path.lexists(source) or os.path.lexists(staged_source))
                    self.assertTrue(os.path.lexists(new_record) or os.path.lexists(target))
                finally:
                    synthetic.close()

    def test_legacy_quarantine_race_restores_exact_source_before_refusal(self):
        path = self.synthetic.add_worktree("legacy-quarantine-race")
        service = self.synthetic.service(actor="migration-session")
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_before = snapshot_entry(source)
        target, _, staged_source = service._migration_paths(path)
        original_rename_noreplace = cleanup.rename_noreplace
        collision = b"operator-owned canonical collision\n"

        def collide_at_quarantine(source_fd, source_name, destination_fd, destination_name):
            if source_name == source.name:
                target.write_bytes(collision)
            return original_rename_noreplace(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )

        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        with mock.patch.object(cleanup, "rename_noreplace", collide_at_quarantine):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.completed_actions, [])
        self.assertEqual(snapshot_entry(source), source_before)
        self.assertEqual(target.read_bytes(), collision)
        self.assertTrue(os.path.lexists(staged_source))
        quarantines = list(service.lease_dir.glob(".lease-quarantine-legacy-source-*.evidence"))
        self.assertEqual(quarantines, [])

    def test_staged_quarantine_race_restores_exact_evidence_before_refusal(self):
        path = self.synthetic.add_worktree("staged-quarantine-race")
        service = self.synthetic.service(actor="migration-session")
        legacy_source, _ = self.synthetic.write_legacy_lease(service, path)
        legacy_bytes = legacy_source.read_bytes()
        target, _, staged_source = service._migration_paths(path)
        original_rename_noreplace = cleanup.rename_noreplace
        holder = {}

        def collide_at_quarantine(source_fd, source_name, destination_fd, destination_name):
            if source_name == staged_source.name:
                holder["staged_before"] = snapshot_entry(staged_source)
                legacy_source.write_bytes(legacy_bytes)
                holder["legacy_collision"] = snapshot_entry(legacy_source)
            return original_rename_noreplace(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )

        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        with mock.patch.object(cleanup, "rename_noreplace", collide_at_quarantine):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.completed_actions, [])
        self.assertEqual(snapshot_entry(staged_source), holder["staged_before"])
        self.assertEqual(snapshot_entry(legacy_source), holder["legacy_collision"])
        self.assertTrue(os.path.lexists(target))
        quarantines = list(service.lease_dir.glob(".lease-quarantine-staged-source-*.evidence"))
        self.assertEqual(quarantines, [])

    def test_new_record_fsync_failure_never_cleans_up_identity_swap(self):
        for replacement_kind in ("regular", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"new-fsync-swap-{replacement_kind}")
                    service = synthetic.service(actor="migration-session")
                    source, _ = synthetic.write_legacy_lease(service, path)
                    source_before = snapshot_entry(source)
                    target, new_record, staged_source = service._migration_paths(path)
                    original_fsync = cleanup.os.fsync
                    original_replace = cleanup.os.replace
                    holder = {}

                    def fail_after_identity_swap(descriptor):
                        if not holder:
                            foreign = synthetic.sandbox / f"foreign-{replacement_kind}"
                            if replacement_kind == "regular":
                                foreign.write_bytes(b"foreign replacement\n")
                            else:
                                external = synthetic.sandbox / "external-new-record"
                                external.write_bytes(b"external evidence\n")
                                foreign.symlink_to(external)
                            original_replace(foreign, new_record)
                            holder["snapshot"] = snapshot_entry(new_record)
                            raise OSError("synthetic new-record fsync failure")
                        return original_fsync(descriptor)

                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    with mock.patch.object(cleanup.os, "fsync", fail_after_identity_swap):
                        result = service.migrate_lease(
                            path=path,
                            plan_digest=reviewed.plan_digest,
                            roles_ended=True,
                        )

                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(result.completed_actions, [])
                    self.assertEqual(snapshot_entry(new_record), holder["snapshot"])
                    self.assertEqual(snapshot_entry(source), source_before)
                    self.assertFalse(os.path.lexists(target))
                    self.assertFalse(os.path.lexists(staged_source))
                finally:
                    synthetic.close()

    def test_post_file_fsync_forged_provenance_is_preserved_and_refused(self):
        path = self.synthetic.add_worktree("post-fsync-forged-provenance")
        holder = {}

        def forge_after_file_fsync(current):
            if current != "new-record-file-synced":
                return
            new_record = holder["new_record"]
            forged = json.loads(new_record.read_text())
            forged["path_migration"]["actor"] = "forged-operator"
            replacement = self.synthetic.sandbox / "forged-migration-new"
            replacement.write_text(json.dumps(forged, sort_keys=True, indent=2) + "\n")
            os.replace(replacement, new_record)
            holder["forged_snapshot"] = snapshot_entry(new_record)

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=forge_after_file_fsync,
        )
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_before = snapshot_entry(source)
        target, new_record, staged_source = service._migration_paths(path)
        holder["new_record"] = new_record
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 1)
        self.assertIn("after file fsync", result.errors[-1])
        self.assertEqual(snapshot_entry(new_record), holder["forged_snapshot"])
        self.assertEqual(snapshot_entry(source), source_before)
        self.assertFalse(os.path.lexists(target))
        self.assertFalse(os.path.lexists(staged_source))

    def test_foreign_swap_at_quarantine_syscall_is_rolled_back_without_loss(self):
        for replacement_kind in ("regular", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"quarantine-swap-{replacement_kind}")
                    service = synthetic.service(actor="migration-session")
                    source, _ = synthetic.write_legacy_lease(service, path)
                    source_before = snapshot_entry(source)
                    safe_source = synthetic.sandbox / f"safe-source-{replacement_kind}"
                    original_rename_noreplace = cleanup.rename_noreplace
                    holder = {}

                    def swap_at_syscall(source_fd, source_name, destination_fd, destination_name):
                        if source_name == source.name and not holder:
                            os.replace(source, safe_source)
                            foreign = synthetic.sandbox / f"foreign-swap-{replacement_kind}"
                            if replacement_kind == "regular":
                                foreign.write_bytes(b"foreign syscall replacement\n")
                            else:
                                external = synthetic.sandbox / "foreign-symlink-target"
                                external.write_bytes(b"external foreign evidence\n")
                                foreign.symlink_to(external)
                            os.replace(foreign, source)
                            holder["foreign_snapshot"] = snapshot_entry(source)
                        return original_rename_noreplace(
                            source_fd,
                            source_name,
                            destination_fd,
                            destination_name,
                        )

                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    with mock.patch.object(cleanup, "rename_noreplace", swap_at_syscall):
                        result = service.migrate_lease(
                            path=path,
                            plan_digest=reviewed.plan_digest,
                            roles_ended=True,
                        )

                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(snapshot_entry(source), holder["foreign_snapshot"])
                    self.assertEqual(snapshot_entry(safe_source), source_before)
                    self.assertTrue(service._migration_paths(path)[2].exists())
                finally:
                    synthetic.close()

    def test_lease_directory_swap_cannot_redirect_pinned_transaction(self):
        path = self.synthetic.add_worktree("lease-directory-swap")
        service = self.synthetic.service(actor="migration-session")
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_before = snapshot_entry(source)
        moved_directory = service.lease_dir.with_name("pinned-lease-directory")
        original_rename_noreplace = cleanup.rename_noreplace
        holder = {}

        def swap_directory(source_fd, source_name, destination_fd, destination_name):
            if source_name == source.name and not holder:
                service.lease_dir.rename(moved_directory)
                service.lease_dir.mkdir()
                foreign = service.lease_dir / "foreign-public-entry"
                foreign.write_bytes(b"replacement directory evidence\n")
                holder["foreign"] = snapshot_entry(foreign)
            return original_rename_noreplace(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )

        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        with mock.patch.object(cleanup, "rename_noreplace", swap_directory):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        self.assertEqual(result.exit_status, 1)
        self.assertIn("public lease directory", result.errors[-1])
        foreign = service.lease_dir / "foreign-public-entry"
        self.assertEqual(snapshot_entry(foreign), holder["foreign"])
        quarantines = list(moved_directory.glob(".lease-quarantine-legacy-source-*.evidence"))
        self.assertEqual(len(quarantines), 1)
        self.assertEqual(snapshot_entry(quarantines[0])[1:], source_before[1:])

    def test_review_digest_rejects_hardlinked_replacement_lease_directory(self):
        path = self.synthetic.add_worktree("reviewed-directory-replacement")
        service = self.synthetic.service(actor="migration-session")
        source, _ = self.synthetic.write_legacy_lease(service, path)
        source_bytes = source.read_bytes()
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        reviewed_directory = reviewed.lease_directory_snapshot
        parked_directory = service.lease_dir.with_name("reviewed-lease-directory")

        service.lease_dir.rename(parked_directory)
        service.lease_dir.mkdir(mode=reviewed_directory["opened"]["mode"])
        for entry in parked_directory.iterdir():
            if entry.is_file() and not entry.is_symlink():
                os.link(entry, service.lease_dir / entry.name, follow_symlinks=False)
        replacement_before = {entry.name: snapshot_entry(entry) for entry in service.lease_dir.iterdir()}
        parked_before = {entry.name: snapshot_entry(entry) for entry in parked_directory.iterdir()}

        current = service.reconcile_lease_plan(path=path, roles_ended=True)
        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertNotEqual(reviewed.plan_digest, current.plan_digest)
        self.assertNotEqual(
            reviewed_directory["opened"]["inode"],
            current.lease_directory_snapshot["opened"]["inode"],
        )
        self.assertEqual(result.exit_status, 2)
        self.assertIn("digest", result.errors[-1])
        self.assertEqual(
            {entry.name: snapshot_entry(entry) for entry in service.lease_dir.iterdir()},
            replacement_before,
        )
        self.assertEqual(
            {entry.name: snapshot_entry(entry) for entry in parked_directory.iterdir()},
            parked_before,
        )
        self.assertEqual((parked_directory / source.name).read_bytes(), source_bytes)

    def test_final_proof_refuses_late_retained_alias_and_target_replacement(self):
        for mutation in ("retained-alias", "target-replacement"):
            with self.subTest(mutation=mutation):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"final-proof-{mutation}")
                    holder = {}

                    def mutate_final_proof(current):
                        if current != "final-proof":
                            return
                        service = holder["service"]
                        target, _, _ = service._migration_paths(path)
                        if mutation == "retained-alias":
                            changed = service._retained_source_path(path)
                            changed.write_bytes(holder["source_bytes"])
                        else:
                            changed = target
                            replacement = synthetic.sandbox / "foreign-final-target"
                            replacement.write_bytes(b"foreign final canonical\n")
                            os.replace(replacement, changed)
                        holder["changed"] = changed
                        holder["changed_snapshot"] = snapshot_entry(changed)

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=mutate_final_proof,
                    )
                    holder["service"] = service
                    source, _ = synthetic.write_legacy_lease(service, path)
                    holder["source_bytes"] = source.read_bytes()
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(result.completed_actions, [])
                    self.assertEqual(
                        snapshot_entry(holder["changed"]),
                        holder["changed_snapshot"],
                    )
                    source_quarantines = list(service.lease_dir.glob(".lease-quarantine-legacy-source-*.evidence"))
                    self.assertEqual(len(source_quarantines), 1)
                    self.assertEqual(source_quarantines[0].read_bytes(), holder["source_bytes"])
                finally:
                    synthetic.close()

    def test_migration_never_uses_unbound_path_deletion_or_replacement(self):
        path = self.synthetic.add_worktree("forbidden-path-mutation-primitives")
        service = self.synthetic.service(actor="migration-session")
        self.synthetic.write_legacy_lease(service, path)
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        with (
            mock.patch.object(Path, "unlink", side_effect=AssertionError("Path.unlink called")),
            mock.patch.object(cleanup.os, "unlink", side_effect=AssertionError("os.unlink called")),
            mock.patch.object(cleanup.os, "remove", side_effect=AssertionError("os.remove called")),
            mock.patch.object(cleanup.os, "replace", side_effect=AssertionError("os.replace called")),
        ):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        self.assertEqual(result.exit_status, 0)
        target, new_record, staged_source = service._migration_paths(path)
        self.assertEqual(service._lookup_lease(path).source, "canonical")
        self.assertTrue(target.exists())
        self.assertFalse(os.path.lexists(new_record))
        self.assertFalse(os.path.lexists(staged_source))
        self.assertFalse(os.path.lexists(service._retained_source_path(path)))

    def test_retained_only_crash_state_recovers_to_one_canonical_record(self):
        path = self.synthetic.add_worktree("retained-only-recovery")

        def interrupt(current):
            if current == "new-record-removed":
                raise OSError("synthetic pre-final interruption")

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=interrupt,
        )
        legacy_source, _ = self.synthetic.write_legacy_lease(service, path)
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        interrupted = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(interrupted.exit_status, 1)
        target, new_record, staged_source = service._migration_paths(path)
        retained_source = service._retained_source_path(path)
        os.link(staged_source, retained_source, follow_symlinks=False)
        os.remove(staged_source)

        service.migration_hook = lambda current: None
        recovery = service.reconcile_lease_plan(path=path, roles_ended=True)
        completed = service.migrate_lease(
            path=path,
            plan_digest=recovery.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(recovery.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
        self.assertEqual(completed.exit_status, 0)
        self.assertFalse(os.path.lexists(legacy_source))
        self.assertFalse(os.path.lexists(new_record))
        self.assertFalse(os.path.lexists(staged_source))
        self.assertFalse(os.path.lexists(retained_source))
        self.assertEqual(service._lookup_lease(path).source, "canonical")
        self.assertEqual(list(service.lease_dir.glob("*.json")), [target])

    def test_link_first_crash_state_without_staged_source_recovers_exactly(self):
        path = self.synthetic.add_worktree("legacy-link-first-recovery")

        def interrupt_after_new_record(current):
            if current == "new-record-written":
                raise OSError("synthetic link-first setup")

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=interrupt_after_new_record,
        )
        legacy_source, _ = self.synthetic.write_legacy_lease(service, path)
        legacy_bytes = legacy_source.read_bytes()
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        interrupted = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(interrupted.exit_status, 1)
        target, new_record, staged_source = service._migration_paths(path)
        os.link(new_record, target, follow_symlinks=False)
        self.assertFalse(os.path.lexists(staged_source))

        service.migration_hook = lambda current: None
        recovery = service.reconcile_lease_plan(path=path, roles_ended=True)
        completed = service.migrate_lease(
            path=path,
            plan_digest=recovery.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(
            recovery.facts["recovery_stage"],
            "canonical-linked-before-source-staging",
        )
        self.assertEqual(recovery.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
        self.assertEqual(completed.exit_status, 0)
        self.assertEqual(completed.completed_actions, completed.requested_actions)
        self.assertEqual(service._lookup_lease(path).source, "canonical")
        self.assertEqual(list(service.lease_dir.glob("*.json")), [target])
        quarantines = list(service.lease_dir.glob(".lease-quarantine-legacy-source-*.evidence"))
        self.assertEqual(len(quarantines), 1)
        self.assertEqual(quarantines[0].read_bytes(), legacy_bytes)

    def test_post_syscall_link_interruptions_are_idempotently_recoverable(self):
        for link_role in ("staged-source", "canonical-target"):
            with self.subTest(link_role=link_role):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"post-link-{link_role}")
                    service = synthetic.service(actor="migration-session")
                    legacy_source, _ = synthetic.write_legacy_lease(service, path)
                    legacy_bytes = legacy_source.read_bytes()
                    target, new_record, staged_source = service._migration_paths(path)
                    original_link = cleanup.os.link
                    interrupted_once = False

                    def link_then_interrupt(
                        source_name,
                        destination_name,
                        *,
                        src_dir_fd=None,
                        dst_dir_fd=None,
                        follow_symlinks=True,
                    ):
                        nonlocal interrupted_once
                        result = original_link(
                            source_name,
                            destination_name,
                            src_dir_fd=src_dir_fd,
                            dst_dir_fd=dst_dir_fd,
                            follow_symlinks=follow_symlinks,
                        )
                        expected_destination = staged_source.name if link_role == "staged-source" else target.name
                        if not interrupted_once and destination_name == expected_destination:
                            interrupted_once = True
                            raise OSError(f"post-syscall interruption:{link_role}")
                        return result

                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    with mock.patch.object(cleanup.os, "link", link_then_interrupt):
                        interrupted = service.migrate_lease(
                            path=path,
                            plan_digest=reviewed.plan_digest,
                            roles_ended=True,
                        )

                    self.assertEqual(interrupted.exit_status, 1)
                    self.assertTrue(interrupted_once)
                    self.assertTrue(os.path.lexists(staged_source))
                    if link_role == "canonical-target":
                        self.assertTrue(os.path.lexists(target))
                    recovery = service.reconcile_lease_plan(path=path, roles_ended=True)
                    completed = service.migrate_lease(
                        path=path,
                        plan_digest=recovery.plan_digest,
                        roles_ended=True,
                    )

                    self.assertEqual(recovery.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
                    self.assertEqual(completed.exit_status, 0)
                    self.assertEqual(service._lookup_lease(path).source, "canonical")
                    self.assertEqual(list(service.lease_dir.glob("*.json")), [target])
                    self.assertTrue(
                        any(
                            item.read_bytes() == legacy_bytes
                            for item in service.lease_dir.glob(".lease-quarantine-*-*.evidence")
                        )
                    )
                    self.assertFalse(os.path.lexists(new_record))
                    self.assertFalse(os.path.lexists(staged_source))
                finally:
                    synthetic.close()

    def test_target_or_legacy_reappearance_at_every_transition_refuses_without_loss(self):
        transitions = (
            "new-record-written",
            "source-staging-linked",
            "source-staged",
            "canonical-linked",
            "new-record-removed",
            "source-removed",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"reappearance-{transition}")
                    holder = {}

                    def reappear(current):
                        if current != transition:
                            return
                        service = holder["service"]
                        source = holder["source"]
                        target, _, _ = service._migration_paths(path)
                        if transition in {"new-record-written", "source-staging-linked"}:
                            changed_path = target
                            changed_path.write_bytes(b"unexpected canonical collision\n")
                        else:
                            changed_path = source
                            changed_path.write_bytes(holder["source_bytes"])
                        holder["changed_path"] = changed_path
                        holder["changed_snapshot"] = snapshot_entry(changed_path)

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=reappear,
                    )
                    holder["service"] = service
                    source, _ = synthetic.write_legacy_lease(service, path)
                    holder["source"] = source
                    holder["source_bytes"] = source.read_bytes()
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    target, new_record, staged_source = service._migration_paths(path)
                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(result.completed_actions, [])
                    self.assertEqual(
                        snapshot_entry(holder["changed_path"]),
                        holder["changed_snapshot"],
                    )
                    if transition in {"new-record-written", "source-staging-linked"}:
                        self.assertEqual(source.read_bytes(), holder["source_bytes"])
                    else:
                        self.assertTrue(os.path.lexists(staged_source))
                    self.assertTrue(os.path.lexists(source) or os.path.lexists(staged_source))
                    self.assertTrue(os.path.lexists(new_record) or os.path.lexists(target))
                finally:
                    synthetic.close()

    def test_process_context_drift_at_every_transition_preserves_evidence(self):
        transitions = (
            "new-record-written",
            "source-staging-linked",
            "source-staged",
            "canonical-linked",
            "new-record-removed",
            "source-removed",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"process-drift-{transition}")
                    scanner = StaticScanner()

                    def drift_process(current):
                        if current == transition:
                            scanner.result = cleanup.ProcessScan(
                                True,
                                uses=(cleanup.ProcessUse(4242, ("cwd",)),),
                            )

                    service = synthetic.service(
                        scanner=scanner,
                        actor="migration-session",
                        migration_hook=drift_process,
                    )
                    source, _ = synthetic.write_legacy_lease(service, path)
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    target, new_record, staged_source = service._migration_paths(path)
                    self.assertEqual(result.exit_status, 1)
                    self.assertIn("process visibility or use changed", result.errors[-1])
                    retained_sources = [item for item in (source, staged_source) if os.path.lexists(item)]
                    self.assertTrue(retained_sources)
                    self.assertTrue(os.path.lexists(new_record) or os.path.lexists(target))
                    if os.path.lexists(target) and os.path.lexists(staged_source):
                        self.assertEqual(
                            service._lookup_lease(path).source,
                            "invalid",
                        )
                finally:
                    synthetic.close()

    def test_entry_drift_at_every_transition_is_preserved_on_refusal(self):
        transitions = (
            "new-record-written",
            "source-staging-linked",
            "source-staged",
            "canonical-linked",
            "new-record-removed",
            "source-removed",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"entry-drift-{transition}")
                    holder = {}

                    def drift_entry(current):
                        if current != transition:
                            return
                        service = holder["service"]
                        source = holder["source"]
                        target, new_record, staged_source = service._migration_paths(path)
                        if transition == "new-record-written":
                            changed_path = source
                            changed = json.loads(changed_path.read_text())
                            changed["role"] = "changed-source"
                        elif transition == "source-staging-linked":
                            changed_path = source
                            changed = json.loads(changed_path.read_text())
                            changed["role"] = "changed-linked-source"
                        elif transition in {"source-staged", "source-removed"}:
                            changed_path = staged_source
                            changed = json.loads(changed_path.read_text())
                            changed["role"] = f"changed-{transition}"
                        elif transition == "canonical-linked":
                            changed_path = new_record
                            changed = json.loads(changed_path.read_text())
                            changed["path_migration"]["actor"] = "changed-linked-target"
                        else:
                            changed_path = target
                            changed = json.loads(changed_path.read_text())
                            changed["path_migration"]["actor"] = "changed-final-target"
                        changed_path.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n")
                        holder["changed_path"] = changed_path
                        holder["changed_snapshot"] = snapshot_entry(changed_path)

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=drift_entry,
                    )
                    holder["service"] = service
                    source, _ = synthetic.write_legacy_lease(service, path)
                    holder["source"] = source
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

                    result = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    target, new_record, staged_source = service._migration_paths(path)
                    self.assertEqual(result.exit_status, 1)
                    self.assertEqual(
                        snapshot_entry(holder["changed_path"]),
                        holder["changed_snapshot"],
                    )
                    self.assertTrue(os.path.lexists(source) or os.path.lexists(staged_source))
                    self.assertTrue(os.path.lexists(new_record) or os.path.lexists(target))
                finally:
                    synthetic.close()

    def test_recovery_plan_binds_every_present_entry_bytes_and_identity(self):
        mutations = {
            "new-record-written": "new-content",
            "source-staging-linked": "legacy-kind",
            "source-staged": "staged-content",
            "canonical-linked": "new-kind",
            "new-record-removed": "target-content",
            "source-removed": "staged-kind",
        }
        for transition, mutation in mutations.items():
            with self.subTest(transition=transition, mutation=mutation):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"recovery-drift-{transition}")

                    def interrupt(current):
                        if current == transition:
                            raise OSError(f"interrupt {transition}")

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=interrupt,
                    )
                    source, _ = synthetic.write_legacy_lease(service, path)
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    interrupted = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )
                    self.assertEqual(interrupted.exit_status, 1)
                    service.migration_hook = lambda current: None
                    recovery = service.reconcile_lease_plan(path=path, roles_ended=True)
                    self.assertEqual(recovery.classification, cleanup.ELIGIBLE_LEASE_MIGRATION)
                    target, new_record, staged_source = service._migration_paths(path)

                    if mutation == "new-content":
                        changed = json.loads(new_record.read_text())
                        changed["path_migration"]["actor"] = "changed-new"
                        new_record.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n")
                    elif mutation == "legacy-kind":
                        original = source.read_bytes()
                        source.unlink()
                        external = synthetic.sandbox / "legacy-external"
                        external.write_bytes(original)
                        source.symlink_to(external)
                    elif mutation == "staged-content":
                        changed = json.loads(staged_source.read_text())
                        changed["role"] = "changed-staged"
                        staged_source.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n")
                    elif mutation == "new-kind":
                        original = new_record.read_bytes()
                        new_record.unlink()
                        external = synthetic.sandbox / "new-external"
                        external.write_bytes(original)
                        new_record.symlink_to(external)
                    elif mutation == "target-content":
                        changed = json.loads(target.read_text())
                        changed["path_migration"]["actor"] = "changed-target"
                        target.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n")
                    else:
                        original = staged_source.read_bytes()
                        staged_source.unlink()
                        external = synthetic.sandbox / "staged-external"
                        external.write_bytes(original)
                        staged_source.symlink_to(external)

                    evidence_paths = (source, target, new_record, staged_source)
                    drifted_snapshot = {str(item): snapshot_entry(item) for item in evidence_paths}
                    result = service.migrate_lease(
                        path=path,
                        plan_digest=recovery.plan_digest,
                        roles_ended=True,
                    )

                    self.assertEqual(result.exit_status, 2)
                    self.assertIn("digest", result.errors[-1])
                    self.assertEqual(
                        {str(item): snapshot_entry(item) for item in evidence_paths},
                        drifted_snapshot,
                    )
                finally:
                    synthetic.close()

    def test_synthetic_migration_preserves_preexisting_real_state_snapshot(self):
        real_before = snapshot_preexisting_real_state()
        path = self.synthetic.add_worktree("real-state-isolation")
        service = self.synthetic.service(actor="migration-session")
        self.synthetic.write_legacy_lease(service, path, state="active")
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 0)
        self.assertEqual(snapshot_preexisting_real_state(), real_before)

    def test_each_interruption_is_recoverable_and_idempotent(self):
        transitions = (
            "new-record-written",
            "staged-source-linked",
            "canonical-published",
            "source-staging-linked",
            "source-staged",
            "canonical-linked",
            "new-record-removed",
            "source-removed",
            "source-quarantined",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                synthetic = SyntheticRepo()
                try:
                    synthetic.enable_external_boundary()
                    path = synthetic.add_worktree(f"interrupt-{transition}")

                    def interrupt(current):
                        if current == transition:
                            raise OSError(f"simulated {transition}")

                    service = synthetic.service(
                        actor="migration-session",
                        migration_hook=interrupt,
                    )
                    synthetic.write_legacy_lease(service, path, state="active")
                    reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
                    interrupted = service.migrate_lease(
                        path=path,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )
                    self.assertEqual(interrupted.exit_status, 1)

                    service.migration_hook = lambda current: None
                    recovery = service.reconcile_lease_plan(path=path, roles_ended=True)
                    if recovery.classification == cleanup.ELIGIBLE_LEASE_MIGRATION:
                        completed = service.migrate_lease(
                            path=path,
                            plan_digest=recovery.plan_digest,
                            roles_ended=True,
                        )
                        self.assertEqual(completed.exit_status, 0)
                    else:
                        self.assertEqual(
                            recovery.classification,
                            cleanup.LEASE_MIGRATION_NOT_REQUIRED,
                        )
                    canonical = service._lease_path(path)
                    self.assertTrue(canonical.exists())
                    self.assertEqual(service.read_lease(path)[0]["state"], "active")
                    _, new_record, staged_source = service._migration_paths(path)
                    self.assertFalse(new_record.exists())
                    self.assertFalse(staged_source.exists())
                    json_records = list(service.lease_dir.glob("*.json"))
                    self.assertEqual(json_records, [canonical])
                finally:
                    synthetic.close()

    def test_cross_actor_recovery_refuses_and_preserves_original_provenance(self):
        path = self.synthetic.add_worktree("cross-actor-recovery")

        def interrupt(current):
            if current == "canonical-published":
                raise OSError("leave actor-bound canonical recovery state")

        original_actor = self.synthetic.service(
            actor="operator-a",
            migration_hook=interrupt,
        )
        self.synthetic.write_legacy_lease(original_actor, path)
        reviewed = original_actor.reconcile_lease_plan(path=path, roles_ended=True)
        interrupted = original_actor.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(interrupted.exit_status, 1)
        target, _, _ = original_actor._migration_paths(path)
        self.assertEqual(json.loads(target.read_text())["path_migration"]["actor"], "operator-a")

        different_actor = self.synthetic.service(actor="operator-b")
        recovery = different_actor.reconcile_lease_plan(path=path, roles_ended=True)
        before = {entry.name: snapshot_entry(entry) for entry in different_actor.lease_dir.iterdir()}
        refused = different_actor.migrate_lease(
            path=path,
            plan_digest=recovery.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(recovery.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        self.assertIn("current attributable actor", "\n".join(recovery.errors))
        self.assertEqual(refused.exit_status, 2)
        self.assertEqual(refused.completed_actions, [])
        self.assertEqual(
            {entry.name: snapshot_entry(entry) for entry in different_actor.lease_dir.iterdir()},
            before,
        )
        self.assertEqual(json.loads(target.read_text())["path_migration"]["actor"], "operator-a")

        original_actor.migration_hook = lambda current: None
        original_recovery = original_actor.reconcile_lease_plan(path=path, roles_ended=True)
        completed = original_actor.migrate_lease(
            path=path,
            plan_digest=original_recovery.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(completed.exit_status, 0)
        self.assertEqual(json.loads(target.read_text())["path_migration"]["actor"], "operator-a")

    def test_late_alternate_alias_refuses_before_reviewed_source_quarantine(self):
        path = self.synthetic.add_worktree("late-alternate-alias")
        holder = {}

        def inject_alternate_alias(current):
            if current != "source-staging-linked":
                return
            alternate_root = self.synthetic.sandbox / "late-alternate-root"
            alternate_root.symlink_to(self.synthetic.external_worktrees, target_is_directory=True)
            alternate_path = alternate_root / path.name
            alternate = holder["service"].lease_dir / f"{cleanup.lease_key(alternate_path)}.json"
            payload = dict(holder["lease"])
            payload["path"] = str(alternate_path)
            alternate.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            holder["alternate"] = alternate
            holder["alternate_snapshot"] = snapshot_entry(alternate)
            holder["source_snapshot"] = snapshot_entry(holder["source"])

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=inject_alternate_alias,
        )
        holder["service"] = service
        source, lease = self.synthetic.write_legacy_lease(service, path)
        holder["source"] = source
        holder["lease"] = lease
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)

        result = service.migrate_lease(
            path=path,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.completed_actions, [])
        self.assertIn("JSON entry manifest changed", result.errors[-1])
        self.assertEqual(snapshot_entry(source), holder["source_snapshot"])
        self.assertEqual(snapshot_entry(holder["alternate"]), holder["alternate_snapshot"])
        self.assertEqual(list(service.lease_dir.glob(".lease-quarantine-legacy-source-*.evidence")), [])

    def test_quarantine_syscall_manifest_drift_rolls_back_exact_reviewed_source(self):
        path = self.synthetic.add_worktree("quarantine-syscall-manifest-drift")
        service = self.synthetic.service(actor="migration-session")
        source, lease = self.synthetic.write_legacy_lease(service, path)
        source_before = snapshot_entry(source)
        alternate_root = self.synthetic.sandbox / "quarantine-boundary-alternate-root"
        alternate_root.symlink_to(self.synthetic.external_worktrees, target_is_directory=True)
        alternate_path = alternate_root / path.name
        alternate = service.lease_dir / f"{cleanup.lease_key(alternate_path)}.json"
        alternate_payload = dict(lease)
        alternate_payload["path"] = str(alternate_path)
        original_rename_noreplace = cleanup.rename_noreplace
        holder = {"injected": False}

        def inject_at_legacy_quarantine(
            source_fd,
            source_name,
            destination_fd,
            destination_name,
        ):
            if source_name == source.name and not holder["injected"]:
                alternate.write_text(json.dumps(alternate_payload, sort_keys=True, indent=2) + "\n")
                holder["alternate_snapshot"] = snapshot_entry(alternate)
                holder["injected"] = True
            return original_rename_noreplace(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )

        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        with mock.patch.object(cleanup, "rename_noreplace", inject_at_legacy_quarantine):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        self.assertTrue(holder["injected"])
        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.completed_actions, [])
        self.assertIn("JSON entry manifest changed at legacy-source quarantine boundary", result.errors[-1])
        self.assertEqual(snapshot_entry(source), source_before)
        self.assertEqual(snapshot_entry(alternate), holder["alternate_snapshot"])
        self.assertEqual(list(service.lease_dir.glob(".lease-quarantine-legacy-source-*.evidence")), [])

    def test_final_lookup_manifest_drift_refuses_before_recording_success(self):
        path = self.synthetic.add_worktree("final-lookup-manifest-drift")
        holder = {"armed": False, "injected": False}

        def arm_final_lookup(current):
            if current == "final-proof":
                holder["armed"] = True

        service = self.synthetic.service(
            actor="migration-session",
            migration_hook=arm_final_lookup,
        )
        _, lease = self.synthetic.write_legacy_lease(service, path)
        reviewed = service.reconcile_lease_plan(path=path, roles_ended=True)
        original_lookup = service._lookup_lease

        def lookup_then_inject(*args, **kwargs):
            lookup = original_lookup(*args, **kwargs)
            if holder["armed"] and not holder["injected"]:
                alternate_root = self.synthetic.sandbox / "final-alternate-root"
                alternate_root.symlink_to(self.synthetic.external_worktrees, target_is_directory=True)
                alternate_path = alternate_root / path.name
                alternate = service.lease_dir / f"{cleanup.lease_key(alternate_path)}.json"
                payload = dict(lease)
                payload["path"] = str(alternate_path)
                alternate.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
                holder["alternate"] = alternate
                holder["alternate_snapshot"] = snapshot_entry(alternate)
                holder["target_snapshot"] = snapshot_entry(service._lease_path(path))
                holder["injected"] = True
            return lookup

        with mock.patch.object(service, "_lookup_lease", side_effect=lookup_then_inject):
            result = service.migrate_lease(
                path=path,
                plan_digest=reviewed.plan_digest,
                roles_ended=True,
            )

        target, _, _ = service._migration_paths(path)
        self.assertEqual(result.exit_status, 1)
        self.assertEqual(result.completed_actions, [])
        self.assertTrue(holder["injected"])
        self.assertIn("JSON entry manifest changed", result.errors[-1])
        self.assertEqual(snapshot_entry(holder["alternate"]), holder["alternate_snapshot"])
        self.assertEqual(snapshot_entry(target), holder["target_snapshot"])
        self.assertEqual(service._lookup_lease(path).source, "invalid")

    def test_existing_canonical_lease_is_unchanged_and_not_reconciled(self):
        path = self.synthetic.add_worktree("canonical")
        service = self.synthetic.service()
        canonical = service.create_lease(path=path, issue=1491, role="tester")
        before = canonical.read_bytes()

        classification = service.classify_path(path)
        reconciliation = service.reconcile_lease_plan(path=path)

        self.assertEqual(classification.classification, cleanup.RETAIN_ACTIVE_LIFECYCLE)
        self.assertEqual(classification.lease_lookup_source, "canonical")
        self.assertEqual(reconciliation.classification, cleanup.LEASE_MIGRATION_NOT_REQUIRED)
        self.assertEqual(canonical.read_bytes(), before)


@tag("core")
class ClassificationContractTest(SyntheticRepoTestCase):
    def test_symlinked_boundary_uses_same_semantics_for_every_cleanup_mode(self):
        boundary = self.synthetic.root / ".claude" / "worktrees"
        boundary.parent.mkdir(parents=True, exist_ok=True)
        resolved_boundary = self.synthetic.external_worktrees
        resolved_boundary.mkdir()
        boundary.symlink_to(resolved_boundary, target_is_directory=True)
        path = self.synthetic.add_worktree("classified")
        service = self.synthetic.service()
        self.synthetic.terminal(service, path)

        plan = service.classify_path(path)

        self.assertEqual(service.boundary(), resolved_boundary)
        self.assertEqual(plan.classification, cleanup.ELIGIBLE_REMOVE)

        removed = service.remove(path=path, issue=1442, plan_digest=plan.plan_digest)
        self.assertEqual(removed.exit_status, 0)
        self.assertFalse(path.exists())

        adopted = self.synthetic.add_worktree("adopted")
        service.close_lease(
            path=adopted,
            issue=1442,
            merge_sha=self.synthetic.head,
            run_id="12345",
            run_head_sha=self.synthetic.head,
            adopt_legacy=True,
        )
        self.assertEqual(service.classify_path(adopted).classification, cleanup.ELIGIBLE_REMOVE)

        stale = self.synthetic.add_worktree("stale")
        self.synthetic.terminal(service, stale)
        shutil.rmtree(stale)
        stale_plan = next(plan for plan in service.classify_stale() if plan.path == str(stale))
        self.assertEqual(stale_plan.classification, cleanup.STALE_REGISTRATION_ELIGIBLE_PRUNE)

    def test_strict_boundary_rejects_equal_prefix_sibling_outside_and_ambiguous_paths(self):
        configured = self.synthetic.root / ".claude" / "worktrees"
        configured.parent.mkdir(parents=True, exist_ok=True)
        boundary = self.synthetic.external_worktrees
        boundary.mkdir()
        configured.symlink_to(boundary, target_is_directory=True)
        inside = self.synthetic.add_worktree("inside")
        prefix_sibling = self.synthetic.add_worktree(
            "prefix-sibling",
            parent=boundary.parent / f"{boundary.name}-sibling",
        )
        outside = self.synthetic.add_worktree("outside", parent=self.synthetic.sandbox / "outside")
        service = self.synthetic.service()

        self.assertEqual(service.classify_path(self.synthetic.root).classification, cleanup.PROTECTED_SHARED_MAIN)
        for path in (boundary, prefix_sibling, outside):
            plan = service.classify_path(path)
            self.assertEqual(plan.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
            self.assertNotEqual(plan.classification, cleanup.ELIGIBLE_REMOVE)

        worktrees = service.worktrees()
        registered_inside = next(worktree for worktree in worktrees if worktree.path == inside)
        service.worktrees = lambda: [*worktrees, registered_inside]
        ambiguous = service.classify_path(inside)
        self.assertEqual(ambiguous.classification, cleanup.RETAIN_MISSING_OR_UNCLASSIFIED)
        with self.assertRaisesMessage(cleanup.CleanupError, "exactly one registered worktree"):
            service.create_lease(path=inside, issue=1476, role="software-engineer")

    def test_broken_looping_and_non_directory_boundaries_fail_closed(self):
        configured = self.synthetic.root / ".claude" / "worktrees"
        configured.parent.mkdir(parents=True, exist_ok=True)
        service = self.synthetic.service()

        configured.symlink_to(self.synthetic.sandbox / "missing", target_is_directory=True)
        with self.assertRaisesMessage(cleanup.CleanupError, "cannot be resolved safely"):
            service.classify()

        configured.unlink()
        configured.symlink_to(configured)
        with self.assertRaisesMessage(cleanup.CleanupError, "cannot be resolved safely"):
            service.classify()

        configured.unlink()
        configured.write_text("not a directory\n")
        with self.assertRaisesMessage(cleanup.CleanupError, "is not a directory"):
            service.classify()

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
        mutating_calls = [
            call for call, _ in runner.calls if call[:3] in (["git", "worktree", "remove"], ["git", "branch", "-d"])
        ]
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
