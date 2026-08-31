"""Synthetic safety contracts for ``scripts/retire-agent-branches.py``.

Every mutation is confined to a fresh repository below project-local
``.tmp/``.  The shared repository and its historical agent refs are only ever
snapshotted read-only.
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
MODULE_PATH = PROJECT_ROOT / "scripts" / "retire-agent-branches.py"
TEST_RUNNER_OUTPUT_PATHS = frozenset({"test-output.log"})
_spec = importlib.util.spec_from_file_location("retire_agent_branches", MODULE_PATH)
assert _spec and _spec.loader
retire = importlib.util.module_from_spec(_spec)
sys.modules["retire_agent_branches"] = retire
_spec.loader.exec_module(retire)


def path_identity(path):
    metadata = path.lstat()
    if path.is_symlink():
        return ("symlink", metadata.st_mode, os.readlink(path))
    if path.is_file():
        return (
            "file",
            metadata.st_mode,
            metadata.st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return ("other", metadata.st_mode, metadata.st_size)


def preexisting_repository_manifest():
    """Seal the exact pre-test tree once, before parallel test workers run."""
    paths = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.split(b"\0")
    # Deploy Dev pipes this shard through ``tee test-output.log``.  That live,
    # workflow-owned transcript is expected to grow while tests execute; it is
    # not repository state that the retirement helper can or should preserve.
    # Keep every existing tracked and other non-ignored untracked path in the
    # seal. A tracked path may be intentionally deleted in the uncommitted
    # diff, in which case there is no filesystem identity to preserve.
    manifest = []
    for relative in sorted(raw.decode(errors="strict") for raw in paths if raw):
        if relative in TEST_RUNNER_OUTPUT_PATHS:
            continue
        try:
            identity = path_identity(PROJECT_ROOT / relative)
        except FileNotFoundError:
            continue
        manifest.append((relative, identity))
    return tuple(manifest)


PREEXISTING_REPOSITORY_MANIFEST = preexisting_repository_manifest()


class RepositoryManifestTests(SimpleTestCase):
    def test_manifest_ignores_deleted_tracked_path_and_keeps_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kept.txt").write_text("kept\n", encoding="utf-8")
            (root / "kept-link").symlink_to("kept.txt")
            tracked_paths = b"deleted.txt\0kept-link\0kept.txt\0"

            with (
                mock.patch.object(sys.modules[__name__], "PROJECT_ROOT", root),
                mock.patch.object(
                    subprocess,
                    "run",
                    return_value=mock.Mock(stdout=tracked_paths),
                ),
            ):
                manifest = preexisting_repository_manifest()

            self.assertEqual(
                [relative for relative, _identity in manifest],
                ["kept-link", "kept.txt"],
            )
            self.assertEqual(manifest[0][1][0], "symlink")
            self.assertEqual(manifest[1][1][0], "file")


class StaticScanner:
    def __init__(self, result=None):
        self.result = result or retire.ProcessEvidence(True)

    def scan(self, branch, paths):
        return self.result


class RecordingRunner:
    def __init__(self):
        self.calls = []
        self.inputs = []
        self.before_call = None
        self.after_call = None
        self.delegate = retire.CommandRunner()

    def __call__(self, args, *, cwd=None, input_bytes=None):
        self.calls.append(tuple(args))
        self.inputs.append(input_bytes)
        if self.before_call is not None:
            self.before_call(tuple(args), input_bytes)
        result = self.delegate(args, cwd=cwd, input_bytes=input_bytes)
        if self.after_call is not None:
            self.after_call(tuple(args), input_bytes, result)
        return result


class SyntheticRepo:
    def __init__(self, issue=1260):
        temp_root = PROJECT_ROOT / ".tmp" / "test-retire-agent-branches"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.sandbox = Path(tempfile.mkdtemp(prefix="repo-", dir=temp_root)).resolve()
        self.root = self.sandbox / "repo"
        self.root.mkdir()
        self.issue = issue
        self.branch = f"worktree-agent-{issue}"
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Retirement Tests")
        self.git("config", "user.email", "tests@example.com")
        self.write("base.txt", "base\n")
        self.git("add", "base.txt")
        self.git("commit", "-m", "base")
        self.base = self.head
        self.issue_state = "CLOSED"
        self.labels = []
        self.issue_author = "alexeygrigorev"
        self.comments = []
        self.run_result = "success"
        self.run_workflow = retire.DEPLOY_WORKFLOW
        self.run_status = "completed"
        self.runner = RecordingRunner()

    def close(self):
        assert self.sandbox.is_relative_to(PROJECT_ROOT / ".tmp")
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", *map(str, args)],
            cwd=self.root,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write(self, path, contents):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)

    @property
    def head(self):
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def oid(self, ref):
        result = self.git("rev-parse", "--verify", ref, check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else ""

    def create_patch_equivalent(self):
        self.git("switch", "-c", self.branch)
        self.write("feature.txt", "accepted behavior\n")
        self.git("add", "feature.txt")
        self.git("commit", "-m", f"stale implementation (#{self.issue})")
        self.tip = self.head
        self.git("switch", "main")
        self.write("feature.txt", "accepted behavior\n")
        self.git("add", "feature.txt")
        self.git("commit", "-m", f"accepted implementation (#{self.issue})")
        self.replacement = self.head
        self.git("update-ref", "refs/remotes/origin/main", self.head)
        self.comments = [
            {
                "author": {"login": "alexeygrigorev"},
                "body": (
                    f"{retire.TERMINAL_EVIDENCE_MARKER}\n"
                    + json.dumps(
                        {"decision": "accepted-terminal-green", "run_id": "12345"},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
                "url": "https://example.test/accepted",
            }
        ]
        return self

    def create_unique(self):
        self.git("switch", "-c", self.branch)
        self.write("stale.txt", "unique stale change\n")
        self.git("add", "stale.txt")
        self.git("commit", "-m", f"stale implementation (#{self.issue})")
        self.tip = self.head
        self.git("switch", "main")
        self.write("replacement.txt", "different accepted change\n")
        self.git("add", "replacement.txt")
        self.git("commit", "-m", f"accepted replacement (#{self.issue})")
        self.replacement = self.head
        self.git("update-ref", "refs/remotes/origin/main", self.head)
        self.comments = [
            {
                "author": {"login": "alexeygrigorev"},
                "body": (
                    f"{retire.TERMINAL_EVIDENCE_MARKER}\n"
                    + json.dumps(
                        {"decision": "accepted-terminal-green", "run_id": "12345"},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
                "url": "https://example.test/accepted",
            }
        ]
        return self

    def authorize_supersession(self, *, author="alexeygrigorev"):
        self.comments.append(
            {
                "author": {"login": author},
                "body": f"{retire.SUPERSESSION_EVIDENCE_MARKER}\n"
                + json.dumps(
                    {
                        "decision": "retire-as-superseded",
                        "branch": self.branch,
                        "tip": self.tip,
                        "replacement_commit": self.replacement,
                        "reason": "Accepted replacement makes the stale implementation obsolete.",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "url": "https://example.test/supersession",
            }
        )

    def gh(self, args):
        if list(args[:2]) == ["issue", "view"]:
            return {
                "number": self.issue,
                "state": self.issue_state,
                "labels": [{"name": label} for label in self.labels],
                "author": {"login": self.issue_author},
                "url": f"https://example.test/issues/{self.issue}",
                "comments": self.comments,
            }
        if list(args[:2]) == ["run", "view"]:
            return {
                "databaseId": 12345,
                "status": self.run_status,
                "conclusion": self.run_result,
                "workflowName": self.run_workflow,
                "headSha": self.head,
                "url": "https://example.test/runs/12345",
            }
        raise AssertionError(f"unexpected gh call: {args}")

    def service(self, *, scanner=None, now=None, hook=None):
        return retire.RetirementService(
            self.root,
            actor="operator:test",
            runner=self.runner,
            gh_runner=self.gh,
            process_scanner=scanner or StaticScanner(),
            now=now or (lambda: "2026-08-29T10:00:00Z"),
            failure_hook=hook,
        )

    def refs(self):
        return self.git("for-each-ref", "--format=%(refname) %(objectname)").stdout


class SyntheticRepoTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.synthetic = SyntheticRepo().create_patch_equivalent()

    def tearDown(self):
        self.synthetic.close()
        super().tearDown()


@tag("core")
class ReadOnlyClassifierTest(SyntheticRepoTestCase):
    def test_closed_patch_equivalent_branch_reports_exact_eligible_evidence(self):
        before = self.synthetic.refs()
        plan = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)

        self.assertEqual(plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertEqual(plan.tip, self.synthetic.tip)
        self.assertEqual(plan.parent, self.synthetic.base)
        self.assertEqual(plan.main_sha, self.synthetic.replacement)
        self.assertEqual(plan.main_sha, plan.origin_main_sha)
        self.assertEqual(plan.ahead, 1)
        self.assertEqual(plan.changed_paths, ["feature.txt"])
        self.assertTrue(plan.patch_id)
        self.assertTrue(plan.cherry.startswith("- "))
        self.assertEqual(plan.matching_main_commit, self.synthetic.replacement)
        self.assertEqual(plan.issue, self.synthetic.issue)
        self.assertEqual(plan.terminal_run_id, "12345")
        self.assertTrue(plan.plan_digest)
        self.assertEqual(self.synthetic.refs(), before)
        self.assertFalse(Path(plan.archive_record_path).exists())
        self.assertFalse(self.synthetic.oid(plan.archive_ref))

    def test_default_no_registry_assertion_protects_otherwise_eligible_branch(self):
        plan = self.synthetic.service().plan(self.synthetic.branch)

        self.assertEqual(plan.classification, retire.PROTECTED_ACTIVE_ROLE_OR_LEASE)
        self.assertIn(retire.RETAIN_UNMERGED_UNARCHIVED, plan.reasons)

    def test_open_human_issue_is_protected_despite_patch_equivalence(self):
        self.synthetic.issue_state = "OPEN"
        self.synthetic.labels = ["human"]

        plan = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)

        self.assertEqual(plan.classification, retire.PROTECTED_OPEN_OR_HUMAN_ISSUE)
        self.assertNotEqual(plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

    def test_unique_patch_requires_exact_allowed_owner_supersession(self):
        self.synthetic.close()
        self.synthetic = SyntheticRepo(issue=1291).create_unique()
        service = self.synthetic.service()

        missing = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION, missing.reasons)

        self.synthetic.authorize_supersession(author="untrusted-user")
        untrusted = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION, untrusted.reasons)

        self.synthetic.authorize_supersession()
        accepted = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertEqual(accepted.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertEqual(accepted.replacement_commit, self.synthetic.replacement)

        duplicate = dict(self.synthetic.comments[-1])
        self.synthetic.comments.append(duplicate)
        ambiguous = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION, ambiguous.reasons)

    def test_negated_or_prose_owner_comments_never_authorize_retirement(self):
        self.synthetic.comments = [
            {
                "author": {"login": "alexeygrigorev"},
                "body": "PM did not accept this work; Deploy Dev run 12345 is terminal green.",
                "url": "https://example.test/negated-acceptance",
            }
        ]
        patch_equivalent = self.synthetic.service().plan(
            self.synthetic.branch,
            roles_ended=True,
        )
        self.assertNotEqual(patch_equivalent.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertEqual(patch_equivalent.accepted_author_input, [])

        self.synthetic.close()
        self.synthetic = SyntheticRepo(issue=1291).create_unique()
        self.synthetic.comments.append(
            {
                "author": {"login": "alexeygrigorev"},
                "body": (
                    f"{self.synthetic.branch} at {self.synthetic.tip} is NOT superseded by "
                    f"{self.synthetic.replacement}. Reason: retain this unique implementation."
                ),
                "url": "https://example.test/negated-supersession",
            }
        )
        unique = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION, unique.reasons)
        self.assertNotIn("https://example.test/negated-supersession", unique.accepted_author_input)

    def test_structured_evidence_rejects_extra_prose_unknown_fields_and_ambiguity(self):
        valid = self.synthetic.comments[0]["body"]
        self.synthetic.comments[0]["body"] = f"Please accept this.\n{valid}"
        prose = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(prose.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

        self.synthetic.comments[0]["body"] = f"{retire.TERMINAL_EVIDENCE_MARKER}\n" + json.dumps(
            {"decision": "accepted-terminal-green", "run_id": "12345", "note": "extra"}
        )
        extra = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(extra.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

    def test_structured_evidence_rejects_unicode_whitespace_wrapping_and_withdrawal(self):
        canonical = dict(self.synthetic.comments[0])
        bodies = (
            canonical["body"].replace("agent", "аgent", 1),
            "\N{NO-BREAK SPACE}" + canonical["body"],
            "\n" + canonical["body"],
            canonical["body"] + "\n",
        )
        for body in bodies:
            with self.subTest(body=repr(body)):
                self.synthetic.comments = [{**canonical, "body": body}]
                plan = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
                self.assertNotEqual(plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

        for contradiction in (
            "I never accepted this branch.",
            "I withdraw acceptance; retain it.",
            "Do not retire this branch.",
            "I hereby revoke my acceptance.",
            "I deny acceptance of this candidate.",
            "Acceptance is rescinded.",
            "Retirement is prohibited.",
            "Do not ever retire this branch.",
            "This candidate must not be accepted.",
            "You mustn't retire this branch.",
        ):
            with self.subTest(contradiction=contradiction):
                self.synthetic.comments = [
                    canonical,
                    {
                        "author": {"login": "alexeygrigorev"},
                        "body": contradiction,
                        "url": "https://example.test/withdrawal",
                    },
                ]
                plan = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
                self.assertNotEqual(plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

    def test_duplicate_json_keys_records_and_owner_negation_are_ambiguous(self):
        marker = retire.TERMINAL_EVIDENCE_MARKER
        self.synthetic.comments[0]["body"] = (
            f'{marker}\n{{"decision":"rejected","decision":"accepted-terminal-green","run_id":"12345"}}'
        )
        duplicate_key = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(duplicate_key.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

        self.synthetic.close()
        self.synthetic = SyntheticRepo().create_patch_equivalent()
        self.synthetic.comments.append(dict(self.synthetic.comments[0]))
        duplicate_record = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(duplicate_record.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

        self.synthetic.comments.pop()
        self.synthetic.comments.append(
            {
                "author": {"login": "alexeygrigorev"},
                "body": "I did not accept this candidate; retain it.",
                "url": "https://example.test/owner-negation",
            }
        )
        contradicted = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(contradicted.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)

        self.synthetic.close()
        self.synthetic = SyntheticRepo(issue=1291).create_unique()
        self.synthetic.authorize_supersession()
        supersession = self.synthetic.comments[-1]
        supersession["body"] = (
            f"{retire.SUPERSESSION_EVIDENCE_MARKER}\n"
            f'{{"decision":"retain","decision":"retire-as-superseded",'
            f'"branch":"{self.synthetic.branch}","tip":"{self.synthetic.tip}",'
            f'"replacement_commit":"{self.synthetic.replacement}",'
            '"reason":"Accepted replacement makes this obsolete."}'
        )
        duplicate_supersession_key = self.synthetic.service().plan(
            self.synthetic.branch,
            roles_ended=True,
        )
        self.assertIn(
            retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION,
            duplicate_supersession_key.reasons,
        )

        self.synthetic.close()
        self.synthetic = SyntheticRepo(issue=1291).create_unique()
        self.synthetic.authorize_supersession()
        self.synthetic.comments.append(
            {
                "author": {"login": "alexeygrigorev"},
                "body": (
                    f"{self.synthetic.branch} at {self.synthetic.tip} is never superseded; "
                    "do not retire it, retain this candidate."
                ),
                "url": "https://example.test/supersession-negation",
            }
        )
        contradicted_supersession = self.synthetic.service().plan(
            self.synthetic.branch,
            roles_ended=True,
        )
        self.assertIn(
            retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION,
            contradicted_supersession.reasons,
        )

        self.synthetic.comments[-1] = {
            "author": {"login": "alexeygrigorev"},
            "body": "I deny supersession and retirement of this branch.",
            "url": "https://example.test/supersession-denial",
        }
        denied_supersession = self.synthetic.service().plan(
            self.synthetic.branch,
            roles_ended=True,
        )
        self.assertIn(
            retire.PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION,
            denied_supersession.reasons,
        )

    def test_invalid_revision_glob_nonagent_and_missing_names_are_rejected(self):
        service = self.synthetic.service()
        for branch in (
            "main",
            "worktree-agent-1260~1",
            "worktree-agent-*",
            "refs/heads/worktree-agent-1260",
            "worktree-agent-9999",
            "worktree-agent-01260",
        ):
            with self.subTest(branch=branch):
                plan = service.plan(branch, roles_ended=True)
                self.assertEqual(
                    plan.classification,
                    retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE,
                )

    def test_attached_already_merged_and_multicommit_branches_are_protected(self):
        service = self.synthetic.service()
        worktree = self.synthetic.sandbox / "attached"
        self.synthetic.git("worktree", "add", worktree, self.synthetic.branch)
        attached = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertEqual(attached.classification, retire.PROTECTED_ATTACHED_WORKTREE)
        self.synthetic.git("worktree", "remove", worktree)

        self.synthetic.git("branch", "worktree-agent-1261", "main")
        merged = service.plan("worktree-agent-1261", roles_ended=True)
        self.assertIn(retire.PROTECTED_ALREADY_MERGED, merged.reasons)

        self.synthetic.git("switch", self.synthetic.branch)
        self.synthetic.write("second.txt", "second\n")
        self.synthetic.git("add", "second.txt")
        self.synthetic.git("commit", "-m", "second divergent commit")
        self.synthetic.git("switch", "main")
        multiple = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, multiple.reasons)

    def test_active_or_malformed_lease_process_recovery_and_incomplete_scan_protect(self):
        service = self.synthetic.service()
        lease_dir = service.common_dir / retire.LEASE_DIRNAME
        lease_dir.mkdir()
        lease = lease_dir / "matching.json"
        lease.write_text(
            json.dumps(
                {
                    "version": 1,
                    "issue": self.synthetic.issue,
                    "state": "active",
                    "path": "/x",
                    "actor": "operator:test",
                    "created_at": "2026-08-29T10:00:00Z",
                    "updated_at": "2026-08-29T10:00:00Z",
                }
            )
        )
        active = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, active.reasons)

        lease.write_text("not-json")
        malformed = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, malformed.reasons)

        lease.unlink()
        scanner = StaticScanner(retire.ProcessEvidence(False, (456,), ("pid=456:cmdline-branch",), ("denied",)))
        process = self.synthetic.service(scanner=scanner).plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, process.reasons)
        self.assertFalse(process.process_scan_complete)

        boundary = self.synthetic.root / ".claude" / "worktrees"
        recovery = boundary / f"agent-{self.synthetic.issue}-recovery"
        recovery.mkdir(parents=True)
        recovered = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertIn(str(recovery.resolve()), recovered.recovery_paths)
        self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, recovered.reasons)

    def test_symlinked_lease_directory_is_incomplete_and_protected(self):
        service = self.synthetic.service()
        lease_dir = service.common_dir / retire.LEASE_DIRNAME
        outside = self.synthetic.sandbox / "outside-leases"
        outside.mkdir()
        lease_dir.symlink_to(outside, target_is_directory=True)

        plan = service.plan(self.synthetic.branch, roles_ended=True)

        self.assertNotEqual(plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, plan.reasons)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, plan.reasons)
        self.assertTrue(any("lease directory unreadable" in error for error in plan.errors))

        lease_dir.unlink()
        lease_dir.mkdir()
        outside_lease = outside / "lease.json"
        outside_lease.write_text("{}")
        (lease_dir / "linked.json").symlink_to(outside_lease)
        file_plan = service.plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(file_plan.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, file_plan.reasons)
        self.assertTrue(any("regular no-follow file" in error for error in file_plan.errors))

    def test_broken_and_looped_recovery_boundaries_fail_closed(self):
        boundary = self.synthetic.root / ".claude" / "worktrees"
        boundary.parent.mkdir()
        boundary.symlink_to(self.synthetic.root / "missing-recovery-root", target_is_directory=True)
        broken = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(broken.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, broken.reasons)

        boundary.unlink()
        boundary.symlink_to(boundary, target_is_directory=True)
        looped = self.synthetic.service().plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(looped.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, looped.reasons)

    def test_common_git_directory_inode_swap_refuses_before_mutation(self):
        original = self.synthetic.sandbox / "sealed-common-git-dir"
        swapped = False

        def swap_common_dir(stage):
            nonlocal swapped
            if stage == "before-archive":
                service.common_dir.rename(original)
                service.common_dir.mkdir()
                swapped = True

        service = self.synthetic.service(hook=swap_common_dir)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        try:
            with self.assertRaises(retire.RetirementError):
                service.archive_retire(
                    self.synthetic.branch,
                    plan_digest=reviewed.plan_digest,
                    roles_ended=True,
                )
            self.assertTrue(swapped)
            original_tip = (
                subprocess.run(
                    ["git", f"--git-dir={original}", "rev-parse", "--verify", reviewed.branch_ref],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                .stdout.decode()
                .strip()
            )
            self.assertEqual(original_tip, self.synthetic.tip)
            self.assertFalse(any(service.common_dir.iterdir()))
        finally:
            if swapped:
                service.common_dir.rmdir()
                original.rename(service.common_dir)

    def test_main_issue_run_and_ref_drift_change_digest_and_block_apply(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        self.synthetic.write("drift.txt", "drift\n")
        self.synthetic.git("add", "drift.txt")
        self.synthetic.git("commit", "-m", "main drift")
        self.synthetic.git("update-ref", "refs/remotes/origin/main", self.synthetic.head)

        refused = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(refused.exit_status, 2)
        self.assertTrue(self.synthetic.oid(f"refs/heads/{self.synthetic.branch}"))
        self.assertFalse(self.synthetic.oid(reviewed.archive_ref))

    def test_record_directory_and_file_symlink_boundaries_fail_closed(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        self.assertEqual(reviewed.classification, retire.ELIGIBLE_ARCHIVE_RETIRE)
        outside = self.synthetic.sandbox / "outside-records"
        outside.mkdir()
        service.record_dir.symlink_to(outside, target_is_directory=True)

        plan = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        self.assertEqual(
            plan.classification,
            retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE,
        )
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(plan.branch_ref), self.synthetic.tip)
        self.assertFalse(self.synthetic.oid(plan.archive_ref))
        self.assertEqual(list(outside.iterdir()), [])

        service.record_dir.unlink()
        service.record_dir.mkdir()
        outside_record = outside / "record.json"
        outside_record.write_text("valuable\n")
        service._record_path(plan.branch_ref).symlink_to(outside_record)
        file_plan = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        self.assertEqual(
            file_plan.classification,
            retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE,
        )
        self.assertEqual(outside_record.read_text(), "valuable\n")


@tag("core")
class ArchiveAndRestoreTest(SyntheticRepoTestCase):
    def write_terminal_lease(self, service, *, actor="operator:test", updated_at="2026-08-29T10:00:00Z"):
        lease = service.common_dir / retire.LEASE_DIRNAME / "terminal.json"
        lease.parent.mkdir(exist_ok=True)
        lease.write_text(
            json.dumps(
                {
                    "version": 1,
                    "issue": self.synthetic.issue,
                    "path": str(self.synthetic.root),
                    "state": "terminal",
                    "actor": actor,
                    "created_at": "2026-08-29T10:00:00Z",
                    "updated_at": updated_at,
                    "terminal": {
                        "merge_sha": self.synthetic.replacement,
                        "run_id": "12345",
                        "run_head_sha": self.synthetic.replacement,
                        "result": "success",
                        "roles_ended": True,
                    },
                },
                sort_keys=True,
            )
        )
        return lease

    def retire_branch(self, *, service=None):
        service = service or self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        return service, reviewed, result

    def test_archive_record_precede_expected_tip_delete_and_restore_preserves_archive(self):
        service, reviewed, result = self.retire_branch()

        self.assertEqual(result.classification, retire.ARCHIVED_RETIRED)
        self.assertEqual(result.exit_status, 0)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)
        record = json.loads(Path(reviewed.archive_record_path).read_text())
        archive, error = service._archive_payload(reviewed.archive_ref)
        self.assertEqual(error, "")
        self.assertEqual(archive, record)
        self.assertEqual(record["tip"], self.synthetic.tip)
        self.assertEqual(record["actor"], "operator:test")
        self.assertEqual(record["plan_digest"], reviewed.plan_digest)
        self.assertEqual(record["backup_ref"], reviewed.backup_ref)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)
        self.assertEqual(restore_plan.classification, retire.ELIGIBLE_RESTORE)
        restored = service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(restored.exit_status, 0)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)
        self.assertEqual(json.loads(Path(reviewed.archive_record_path).read_text()), record)
        self.assertEqual(len(service._worktrees()), 1)
        self.assertEqual(self.synthetic.git("symbolic-ref", "--short", "HEAD").stdout.decode().strip(), "main")

    def test_wrong_digest_missing_role_assertion_and_ref_drift_never_archive(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        before = self.synthetic.refs()
        for digest, roles_ended in (("bad", True), (reviewed.plan_digest, False)):
            result = service.archive_retire(
                self.synthetic.branch,
                plan_digest=digest,
                roles_ended=roles_ended,
            )
            self.assertEqual(result.exit_status, 2)
            self.assertEqual(self.synthetic.refs(), before)

        self.synthetic.git("update-ref", reviewed.branch_ref, self.synthetic.base, self.synthetic.tip)
        drifted = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )
        self.assertEqual(drifted.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.base)

    def test_archive_or_record_collision_fails_closed_without_overwrite(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        self.synthetic.git("tag", reviewed.archive_ref.removeprefix("refs/tags/"), self.synthetic.base)
        before = self.synthetic.refs()

        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.refs(), before)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)

    def test_archive_ref_drift_at_atomic_delete_boundary_preserves_source_tip(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        drifted = False

        def drift_before_transaction(args, input_bytes):
            nonlocal drifted
            if args == ("git", "update-ref", "--stdin") and not drifted:
                drifted = True
                self.synthetic.git("update-ref", reviewed.archive_ref, self.synthetic.base)

        self.synthetic.runner.before_call = drift_before_transaction
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertTrue(drifted)
        self.assertNotEqual(result.exit_status, 0)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        containing = self.synthetic.git("for-each-ref", "--contains", self.synthetic.tip).stdout
        self.assertTrue(containing)

    def test_archive_drift_after_committed_delete_recreates_exact_source_without_trusting_archive(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        drifted = False

        def drift_after_transaction(args, input_bytes, result):
            nonlocal drifted
            script = (input_bytes or b"").decode()
            if (
                args == ("git", "update-ref", "--stdin")
                and f"delete {reviewed.branch_ref} {self.synthetic.tip}" in script
                and result.returncode == 0
                and not drifted
            ):
                drifted = True
                self.synthetic.git("update-ref", reviewed.archive_ref, self.synthetic.base)

        self.synthetic.runner.after_call = drift_after_transaction
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertTrue(drifted)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertNotEqual(self.synthetic.oid(reviewed.archive_ref), "")
        containing = self.synthetic.git("for-each-ref", "--contains", self.synthetic.tip).stdout
        self.assertIn(reviewed.branch_ref.encode(), containing)

    def test_retire_postcommit_archive_and_source_collision_keeps_immutable_backup(self):
        reviewed = None

        def collide_after_commit(stage):
            if stage == "after-retire-transaction":
                self.synthetic.git("update-ref", reviewed.archive_ref, self.synthetic.base)
                self.synthetic.git("update-ref", reviewed.branch_ref, self.synthetic.base)

        service = self.synthetic.service(hook=collide_after_commit)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.base)
        self.assertEqual(self.synthetic.oid(reviewed.archive_ref), self.synthetic.base)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)
        containing = self.synthetic.git("for-each-ref", "--contains", self.synthetic.tip).stdout
        self.assertIn(reviewed.backup_ref.encode(), containing)

    def test_after_commit_archive_drift_hook_recreates_source_at_sealed_tip(self):
        reviewed = None

        def drift_after_commit(stage):
            if stage == "after-retire-transaction":
                self.synthetic.git("update-ref", reviewed.archive_ref, self.synthetic.base)

        service = self.synthetic.service(hook=drift_after_commit)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        containing = self.synthetic.git("for-each-ref", "--contains", self.synthetic.tip).stdout
        self.assertIn(reviewed.branch_ref.encode(), containing)

    def test_active_lease_created_inside_retire_ref_call_rolls_back_source_delete(self):
        service = self.synthetic.service()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        lease_path = service.common_dir / retire.LEASE_DIRNAME / "active-retire-race.json"
        created = False

        def create_lease_before_ref_call(args, input_bytes):
            nonlocal created
            script = (input_bytes or b"").decode()
            if (
                args == ("git", "update-ref", "--stdin")
                and f"delete {reviewed.branch_ref} {self.synthetic.tip}" in script
                and not created
            ):
                created = True
                lease_path.parent.mkdir(exist_ok=True)
                lease_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "issue": self.synthetic.issue,
                            "path": str(self.synthetic.root),
                            "state": "active",
                            "actor": "tester:retire-transaction-race",
                            "created_at": "2026-08-29T10:00:00Z",
                            "updated_at": "2026-08-29T10:00:00Z",
                        }
                    )
                )

        self.synthetic.runner.before_call = create_lease_before_ref_call
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertTrue(created)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)

    def test_terminal_lease_byte_drift_at_final_retire_boundary_refuses_delete(self):
        service = None

        def drift_terminal(stage):
            if stage == "before-retire-transaction":
                self.write_terminal_lease(
                    service,
                    actor="operator:changed",
                    updated_at="2026-08-29T10:00:01Z",
                )

        service = self.synthetic.service(hook=drift_terminal)
        lease = self.write_terminal_lease(service)
        inode = lease.stat().st_ino
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(lease.stat().st_ino, inode)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_empty_lease_directory_inode_swap_at_final_boundary_refuses_delete(self):
        service = None
        original = self.synthetic.sandbox / "original-empty-leases"

        def swap_empty_lease_directory(stage):
            if stage == "before-retire-transaction":
                lease_dir.rename(original)
                lease_dir.mkdir()

        service = self.synthetic.service(hook=swap_empty_lease_directory)
        lease_dir = service.common_dir / retire.LEASE_DIRNAME
        lease_dir.mkdir()
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        reviewed_inode = reviewed.lease_boundary["path_identity"]["inode"]
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertNotEqual(lease_dir.stat().st_ino, reviewed_inode)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_empty_recovery_target_inode_swap_at_final_boundary_refuses_delete(self):
        service = None
        configured = self.synthetic.root / ".claude" / "worktrees"
        target = self.synthetic.sandbox / "recovery-target"
        original = self.synthetic.sandbox / "original-recovery-target"
        target.mkdir()
        configured.parent.mkdir()
        configured.symlink_to(target, target_is_directory=True)

        def swap_empty_recovery_target(stage):
            if stage == "before-retire-transaction":
                target.rename(original)
                target.mkdir()

        service = self.synthetic.service(hook=swap_empty_recovery_target)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        reviewed_inode = reviewed.recovery_boundary["resolved_identity"]["inode"]
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertNotEqual(target.stat().st_ino, reviewed_inode)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_in_place_terminal_to_active_lease_at_final_boundary_refuses_delete(self):
        service = None

        def activate_terminal(stage):
            if stage == "before-retire-transaction":
                lease.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "issue": self.synthetic.issue,
                            "path": str(self.synthetic.root),
                            "state": "active",
                            "actor": "operator:test",
                            "created_at": "2026-08-29T10:00:00Z",
                            "updated_at": "2026-08-29T10:00:01Z",
                        },
                        sort_keys=True,
                    )
                )

        service = self.synthetic.service(hook=activate_terminal)
        lease = self.write_terminal_lease(service)
        inode = lease.stat().st_ino
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)
        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(lease.stat().st_ino, inode)
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_interrupted_retirement_is_recoverable_and_rerun_is_idempotent(self):
        for stage in ("before-archive", "after-archive", "after-record", "before-delete", "after-delete"):
            with self.subTest(stage=stage):
                if hasattr(self, "synthetic"):
                    self.synthetic.close()
                self.synthetic = SyntheticRepo().create_patch_equivalent()

                def fail_at(current):
                    if current == stage:
                        raise RuntimeError(f"simulated {stage}")

                interrupted_service = self.synthetic.service(hook=fail_at)
                reviewed = interrupted_service.plan(
                    self.synthetic.branch,
                    mode="archive-retire",
                    roles_ended=True,
                )
                with self.assertRaises(RuntimeError):
                    interrupted_service.archive_retire(
                        self.synthetic.branch,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )
                self.assertTrue(
                    self.synthetic.oid(reviewed.branch_ref)
                    or interrupted_service._archive_commit(reviewed.archive_ref) == self.synthetic.tip
                )

                resumed = self.synthetic.service().archive_retire(
                    self.synthetic.branch,
                    plan_digest=reviewed.plan_digest,
                    roles_ended=True,
                )
                self.assertEqual(resumed.exit_status, 0)
                self.assertEqual(resumed.classification, retire.ARCHIVED_RETIRED)
                self.assertEqual(
                    self.synthetic.service()._archive_commit(reviewed.archive_ref),
                    self.synthetic.tip,
                )

    def test_restore_requires_unchanged_digest_and_absent_unowned_branch(self):
        service, reviewed, _result = self.retire_branch()
        before = self.synthetic.refs()

        bad = service.restore(self.synthetic.branch, plan_digest="bad", roles_ended=True)
        self.assertEqual(bad.exit_status, 2)
        self.assertEqual(self.synthetic.refs(), before)

        self.synthetic.git("update-ref", reviewed.branch_ref, self.synthetic.base)
        conflict = service.restore_plan(self.synthetic.branch, roles_ended=True)
        self.assertNotEqual(conflict.classification, retire.ELIGIBLE_RESTORE)
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)

    def test_restore_rejects_cross_issue_forged_record_and_archive(self):
        service, reviewed, _result = self.retire_branch()
        record_path = Path(reviewed.archive_record_path)
        forged = json.loads(record_path.read_text())
        forged["issue"] = 9999
        record_path.write_text(json.dumps(forged, sort_keys=True, indent=2) + "\n")
        forged_tag = service._tag_object(reviewed, forged)
        self.synthetic.git("update-ref", reviewed.archive_ref, forged_tag)

        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)

        self.assertNotEqual(restore_plan.classification, retire.ELIGIBLE_RESTORE)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, restore_plan.reasons)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_restore_rejects_fully_consistent_triplet_transferred_to_another_issue(self):
        service, reviewed, _result = self.retire_branch()
        forged_branch = "worktree-agent-9999"
        forged_branch_ref = f"refs/heads/{forged_branch}"
        forged_archive_ref = retire._archive_ref(forged_branch, self.synthetic.tip)
        forged_backup_ref = retire._backup_ref(forged_branch, self.synthetic.tip)
        original = json.loads(Path(reviewed.archive_record_path).read_text())
        forged = {
            **original,
            "branch": forged_branch,
            "branch_ref": forged_branch_ref,
            "issue": 9999,
            "archive_ref": forged_archive_ref,
            "backup_ref": forged_backup_ref,
            "record_path": str(service._record_path(forged_branch_ref)),
        }
        forged_plan = retire.Plan(
            timestamp=original["timestamp"],
            actor=original["actor"],
            mode="archive-retire",
            repository=str(self.synthetic.root),
            common_dir=str(service.common_dir),
            branch=forged_branch,
            branch_ref=forged_branch_ref,
            tip=self.synthetic.tip,
            archive_ref=forged_archive_ref,
        )
        tag_oid = service._tag_object(forged_plan, forged)
        self.synthetic.git("update-ref", forged_archive_ref, tag_oid)
        self.synthetic.git("update-ref", forged_backup_ref, self.synthetic.tip)
        service._write_record(service._record_path(forged_branch_ref), forged)

        def forged_issue_gh(args):
            payload = self.synthetic.gh(args)
            if list(args[:2]) == ["issue", "view"] and args[2] == "9999":
                return {
                    **payload,
                    "number": 9999,
                    "url": "https://example.test/issues/9999",
                }
            return payload

        forged_service = retire.RetirementService(
            self.synthetic.root,
            actor="operator:test",
            runner=self.synthetic.runner,
            gh_runner=forged_issue_gh,
            process_scanner=StaticScanner(),
            now=lambda: "2026-08-29T10:00:00Z",
        )
        restore_plan = forged_service.restore_plan(forged_branch, roles_ended=True)

        self.assertNotEqual(restore_plan.classification, retire.ELIGIBLE_RESTORE)
        self.assertIn(retire.PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE, restore_plan.reasons)
        self.assertTrue(any("same-issue match" in error for error in restore_plan.errors))
        self.assertFalse(self.synthetic.oid(forged_branch_ref))
        self.assertEqual(self.synthetic.oid(forged_backup_ref), self.synthetic.tip)

    def test_restore_revalidates_authoritative_issue_comment_and_run_evidence(self):
        service, reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)

        def revoke_before_restore(stage):
            if stage == "before-restore-transaction":
                self.synthetic.comments.append(
                    {
                        "author": {"login": "alexeygrigorev"},
                        "body": "I hereby revoke my acceptance. Do not ever retire this branch.",
                        "url": "https://example.test/revoked-after-retirement",
                    }
                )

        restore_service = self.synthetic.service(hook=revoke_before_restore)
        result = restore_service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_retirement_record_drift_at_restore_transaction_is_rolled_back(self):
        service, reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)
        record_path = Path(reviewed.archive_record_path)
        drifted = False

        def drift_before_transaction(args, input_bytes):
            nonlocal drifted
            if args == ("git", "update-ref", "--stdin") and not drifted:
                drifted = True
                payload = json.loads(record_path.read_text())
                payload["actor"] = "attacker:drift"
                record_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

        self.synthetic.runner.before_call = drift_before_transaction
        result = service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertTrue(drifted)
        self.assertEqual(result.exit_status, 2)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)

    def test_active_lease_at_restore_plan_to_create_boundary_refuses_before_mutation(self):
        service, reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)
        lease_path = service.common_dir / retire.LEASE_DIRNAME / "active-restore.json"

        def create_lease(stage):
            if stage != "before-restore-transaction":
                return
            lease_path.parent.mkdir(exist_ok=True)
            lease_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "issue": self.synthetic.issue,
                        "path": str(self.synthetic.root),
                        "state": "active",
                        "actor": "tester:boundary",
                        "created_at": "2026-08-29T10:00:00Z",
                        "updated_at": "2026-08-29T10:00:00Z",
                    }
                )
            )

        restore_service = retire.RetirementService(
            self.synthetic.root,
            actor="operator:test",
            runner=self.synthetic.runner,
            gh_runner=self.synthetic.gh,
            process_scanner=StaticScanner(),
            now=lambda: "2026-08-29T10:00:00Z",
            failure_hook=create_lease,
        )
        result = restore_service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)

    def test_in_place_terminal_to_active_lease_at_final_restore_boundary_refuses_create(self):
        service, reviewed, _result = self.retire_branch()
        lease = self.write_terminal_lease(service)
        inode = lease.stat().st_ino
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)

        def activate_terminal(stage):
            if stage == "before-restore-transaction":
                lease.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "issue": self.synthetic.issue,
                            "path": str(self.synthetic.root),
                            "state": "active",
                            "actor": "operator:test",
                            "created_at": "2026-08-29T10:00:00Z",
                            "updated_at": "2026-08-29T10:00:01Z",
                        },
                        sort_keys=True,
                    )
                )

        restore_service = self.synthetic.service(hook=activate_terminal)
        result = restore_service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(lease.stat().st_ino, inode)
        self.assertEqual(result.exit_status, 2)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_active_lease_created_inside_restore_ref_call_rolls_back_transient_branch(self):
        service, reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)
        lease_path = service.common_dir / retire.LEASE_DIRNAME / "active-restore-race.json"
        created = False

        def create_lease_before_ref_call(args, input_bytes):
            nonlocal created
            script = (input_bytes or b"").decode()
            if (
                args == ("git", "update-ref", "--stdin")
                and f"create {reviewed.branch_ref} {self.synthetic.tip}" in script
                and not created
            ):
                created = True
                lease_path.parent.mkdir(exist_ok=True)
                lease_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "issue": self.synthetic.issue,
                            "path": str(self.synthetic.root),
                            "state": "active",
                            "actor": "tester:transaction-race",
                            "created_at": "2026-08-29T10:00:00Z",
                            "updated_at": "2026-08-29T10:00:00Z",
                        }
                    )
                )

        self.synthetic.runner.before_call = create_lease_before_ref_call
        result = service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertTrue(created)
        self.assertEqual(result.exit_status, 2)
        self.assertFalse(self.synthetic.oid(reviewed.branch_ref))
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)

    def test_restore_postcommit_archive_and_source_collision_keeps_immutable_backup(self):
        service, reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)

        def collide_after_restore(stage):
            if stage == "after-restore-ref-transaction":
                self.synthetic.git("update-ref", reviewed.archive_ref, self.synthetic.base)
                self.synthetic.git("update-ref", reviewed.branch_ref, self.synthetic.base)

        restore_service = self.synthetic.service(hook=collide_after_restore)
        result = restore_service.restore(
            self.synthetic.branch,
            plan_digest=restore_plan.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.base)
        self.assertEqual(self.synthetic.oid(reviewed.archive_ref), self.synthetic.base)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)
        containing = self.synthetic.git("for-each-ref", "--contains", self.synthetic.tip).stdout
        self.assertIn(reviewed.backup_ref.encode(), containing)

    def test_evidence_drift_after_archival_still_blocks_source_delete(self):
        def drift_before_delete(stage):
            if stage == "before-delete":
                self.synthetic.issue_state = "OPEN"

        service = self.synthetic.service(hook=drift_before_delete)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)

        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(service._archive_commit(reviewed.archive_ref), self.synthetic.tip)
        self.assertTrue(Path(reviewed.archive_record_path).exists())

    def test_issue_state_drift_at_final_retire_boundary_blocks_source_delete(self):
        def drift_at_final_boundary(stage):
            if stage == "before-retire-transaction":
                self.synthetic.issue_state = "OPEN"

        service = self.synthetic.service(hook=drift_at_final_boundary)
        reviewed = service.plan(self.synthetic.branch, mode="archive-retire", roles_ended=True)

        result = service.archive_retire(
            self.synthetic.branch,
            plan_digest=reviewed.plan_digest,
            roles_ended=True,
        )

        self.assertEqual(result.exit_status, 2)
        self.assertEqual(self.synthetic.oid(reviewed.branch_ref), self.synthetic.tip)
        self.assertEqual(self.synthetic.oid(reviewed.backup_ref), self.synthetic.tip)

    def test_github_authority_drift_inside_retire_transaction_rolls_back_source(self):
        drift_cases = (
            "issue-state",
            "issue-label",
            "comments",
            "run-result",
            "run-workflow",
        )
        for drift_case in drift_cases:
            with self.subTest(drift_case=drift_case):
                synthetic = SyntheticRepo().create_patch_equivalent()
                try:
                    service = synthetic.service()
                    reviewed = service.plan(synthetic.branch, mode="archive-retire", roles_ended=True)
                    drifted = False

                    def drift_before_ref_call(args, input_bytes):
                        nonlocal drifted
                        script = (input_bytes or b"").decode()
                        if (
                            args == ("git", "update-ref", "--stdin")
                            and f"delete {reviewed.branch_ref} {synthetic.tip}" in script
                            and not drifted
                        ):
                            drifted = True
                            if drift_case == "issue-state":
                                synthetic.issue_state = "OPEN"
                            elif drift_case == "issue-label":
                                synthetic.labels = ["human"]
                            elif drift_case == "comments":
                                synthetic.comments.append(
                                    {
                                        "author": {"login": "alexeygrigorev"},
                                        "body": "I deny acceptance and retirement of this branch.",
                                        "url": "https://example.test/final-denial",
                                    }
                                )
                            elif drift_case == "run-result":
                                synthetic.run_result = "failure"
                            else:
                                synthetic.run_workflow = "Unrelated Workflow"

                    synthetic.runner.before_call = drift_before_ref_call
                    result = service.archive_retire(
                        synthetic.branch,
                        plan_digest=reviewed.plan_digest,
                        roles_ended=True,
                    )

                    self.assertTrue(drifted)
                    self.assertEqual(result.exit_status, 2)
                    self.assertEqual(synthetic.oid(reviewed.branch_ref), synthetic.tip)
                    self.assertEqual(synthetic.oid(reviewed.backup_ref), synthetic.tip)
                finally:
                    synthetic.close()

    def test_only_compare_update_refs_mutate_and_no_forbidden_operations_run(self):
        service, _reviewed, _result = self.retire_branch()
        restore_plan = service.restore_plan(self.synthetic.branch, roles_ended=True)
        service.restore(self.synthetic.branch, plan_digest=restore_plan.plan_digest, roles_ended=True)
        commands = [" ".join(call) for call in self.synthetic.runner.calls]
        forbidden = (
            " checkout ",
            " switch ",
            " worktree add",
            " worktree remove",
            " branch -D",
            " merge ",
            " rebase ",
            " cherry-pick ",
            " reset ",
            " commit ",
            " push ",
            " prune ",
            " kill ",
            " lease-create",
            " lease-close",
        )
        for command in commands:
            padded = f" {command} "
            self.assertFalse(any(token in padded for token in forbidden), command)
        mutations = [call for call in self.synthetic.runner.calls if call[:2] == ("git", "update-ref")]
        self.assertEqual(len(mutations), 4)
        transaction_inputs = [
            raw.decode()
            for call, raw in zip(self.synthetic.runner.calls, self.synthetic.runner.inputs, strict=True)
            if call == ("git", "update-ref", "--stdin")
        ]
        self.assertEqual(len(transaction_inputs), 2)
        self.assertIn(
            f"delete refs/heads/{self.synthetic.branch} {self.synthetic.tip}",
            transaction_inputs[0],
        )
        self.assertIn(
            f"create refs/heads/{self.synthetic.branch} {self.synthetic.tip}",
            transaction_inputs[1],
        )
        self.assertIn("verify refs/tags/retired-agent-branches/", transaction_inputs[0])
        self.assertIn("verify refs/tags/retired-agent-branches/", transaction_inputs[1])
        self.assertIn("verify refs/retired-agent-backups/", transaction_inputs[0])
        self.assertIn("verify refs/retired-agent-backups/", transaction_inputs[1])


@tag("core")
class RepositoryImmutabilityAndProcessContractTest(SimpleTestCase):
    @staticmethod
    def process_identity(pid):
        proc = Path("/proc") / str(pid)
        try:
            raw_stat = (proc / "stat").read_text()
            fields_after_comm = raw_stat.rsplit(") ", 1)[1].split()
            return {
                "pid": pid,
                "start_time": fields_after_comm[19],
                "exe": os.readlink(proc / "exe"),
                "cwd": os.readlink(proc / "cwd"),
                "cmdline_sha256": hashlib.sha256((proc / "cmdline").read_bytes()).hexdigest(),
            }
        except (FileNotFoundError, PermissionError, OSError, IndexError):
            return None

    def all_project_process_identities(self):
        markers = (
            str(PROJECT_ROOT),
            "/data/agents/ai-shipping-labs",
            "/home/alexey/git/ai-shipping-labs",
        )
        identities = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            identity = self.process_identity(int(entry.name))
            if identity is None:
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if any(marker in cmdline or marker in identity["cwd"] for marker in markers):
                identities[identity["pid"]] = identity
        return identities

    def snapshot_shared_repository(self):
        def run(*args):
            return subprocess.run(
                ["git", *args],
                cwd=PROJECT_ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout

        common = Path(run("rev-parse", "--git-common-dir").decode().strip())
        if not common.is_absolute():
            common = (PROJECT_ROOT / common).resolve()
        leases = common / retire.LEASE_DIRNAME
        try:
            lease_root_identity = path_identity(leases)
        except FileNotFoundError:
            lease_root_identity = ("absent",)
        lease_snapshot = []
        if lease_root_identity[0] == "other":
            lease_snapshot = sorted((str(path.relative_to(common)), path_identity(path)) for path in leases.rglob("*"))
        repository_tree = tuple(
            (relative, path_identity(PROJECT_ROOT / relative))
            for relative, _identity in PREEXISTING_REPOSITORY_MANIFEST
        )
        return {
            "refs": run("for-each-ref", "--format=%(refname) %(objectname)"),
            "worktrees": run("worktree", "list", "--porcelain", "-z"),
            "lease_root_identity": lease_root_identity,
            "leases": lease_snapshot,
            "repository_tree": repository_tree,
        }

    def test_synthetic_archive_restore_does_not_mutate_any_preexisting_real_state(self):
        sentinel = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(120)",
                "worktree-agent-1260",
                str(PROJECT_ROOT),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            sentinel_before = self.process_identity(sentinel.pid)
            self.assertIsNotNone(sentinel_before)
            before_repository = self.snapshot_shared_repository()
            self.assertEqual(
                before_repository["repository_tree"],
                PREEXISTING_REPOSITORY_MANIFEST,
            )
            before_processes = self.all_project_process_identities()
            synthetic = SyntheticRepo().create_patch_equivalent()
            try:
                owned_plan = synthetic.service(scanner=retire.ProcessScanner()).plan(
                    synthetic.branch,
                    mode="archive-retire",
                    roles_ended=True,
                )
                self.assertIn(retire.PROTECTED_ACTIVE_ROLE_OR_LEASE, owned_plan.reasons)
                self.assertIn(sentinel.pid, owned_plan.process_ids)
                self.assertIsNone(sentinel.poll())
                service = synthetic.service()
                plan = service.plan(synthetic.branch, mode="archive-retire", roles_ended=True)
                retired = service.archive_retire(
                    synthetic.branch,
                    plan_digest=plan.plan_digest,
                    roles_ended=True,
                )
                self.assertEqual(retired.classification, retire.ARCHIVED_RETIRED)
                restore_plan = service.restore_plan(synthetic.branch, roles_ended=True)
                restored = service.restore(
                    synthetic.branch,
                    plan_digest=restore_plan.plan_digest,
                    roles_ended=True,
                )
                self.assertEqual(restored.exit_status, 0)
                self.assertEqual(synthetic.oid(plan.branch_ref), synthetic.tip)
            finally:
                synthetic.close()
            self.assertEqual(self.snapshot_shared_repository(), before_repository)
            for pid, before_identity in before_processes.items():
                after_identity = self.process_identity(pid)
                if after_identity is not None:
                    self.assertEqual(after_identity, before_identity)
            self.assertIsNone(sentinel.poll())
            self.assertEqual(self.process_identity(sentinel.pid), sentinel_before)
        finally:
            sentinel.terminate()
            try:
                sentinel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sentinel.kill()
                sentinel.wait(timeout=5)

    def test_process_contract_keeps_retirement_separate_explicit_and_recoverable(self):
        text = " ".join((PROJECT_ROOT / "_docs" / "PROCESS.md").read_text().split())
        for phrase in (
            "Unmerged agent branch retirement",
            "never invokes archive retirement automatically",
            "one exact agent branch",
            "tip-addressed backup ref",
            "common-Git-dir retirement record",
            "expected-old-tip compare-and-delete",
            "restore compare-and-creates only the original local branch",
            "does not check out or create a worktree",
        ):
            self.assertIn(phrase, text)
