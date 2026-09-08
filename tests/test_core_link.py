"""Helper checks for the community-base link tooling and source guard (A0.1)."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from scripts import community_base_link
from scripts.check_community_base_source import main as check_main

TAG_SHA = "fae109b4e34c0afe20c935c6778a476eb968e9cb"

PYPROJECT = """\
[project]
name = "ai-shipping-labs"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "community-base @ git+https://github.com/DataTalksClub/community-base@v0.3.0",
]

[tool.ruff]
line-length = 100
"""

LOCK = f"""\
version = 1
requires-python = ">=3.13"

[[package]]
name = "community-base"
version = "0.3.0"
source = {{ git = "https://github.com/DataTalksClub/community-base?rev=v0.3.0#{TAG_SHA}" }}
"""

SIBLING_PYPROJECT = """\
[project]
name = "community-base"
version = "0.3.0"
"""


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_repo(root: Path) -> Path:
    repo = root / "site"
    write(repo / "pyproject.toml", PYPROJECT)
    write(repo / "uv.lock", LOCK)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(root),
    }
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    return repo


def make_sibling(root: Path) -> Path:
    sibling = root / "community-base"
    write(sibling / "pyproject.toml", SIBLING_PYPROJECT)
    return sibling


def commit_all(repo: Path, root: Path, message: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(root),
    }
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", message], check=True, env=env, capture_output=True
    )


class LinkRoundTripTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.sibling = make_sibling(self.root)
        self.snapshot = self.repo / ".tmp" / "core-link"
        self.original_pyproject = (self.repo / "pyproject.toml").read_bytes()
        self.original_lock = (self.repo / "uv.lock").read_bytes()
        patcher = mock.patch.object(community_base_link, "run_uv")
        self.run_uv = patcher.start()
        self.addCleanup(patcher.stop)

    def run_tool(self, *args) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = community_base_link.main(["--repo", str(self.repo), *args])
        return code, stderr.getvalue()

    def link(self, *extra) -> tuple[int, str]:
        return self.run_tool("link", "--sibling", str(self.sibling), *extra)

    def unlink(self) -> tuple[int, str]:
        return self.run_tool("unlink")

    def test_link_appends_source_table_and_snapshots_originals(self):
        code, _ = self.link()
        self.assertEqual(code, 0)
        text = (self.repo / "pyproject.toml").read_text()
        self.assertIn("[tool.uv.sources]", text)
        self.assertIn("editable = true", text)
        self.assertTrue(self.snapshot.is_dir())
        self.assertEqual((self.snapshot / "pyproject.toml.orig").read_bytes(), self.original_pyproject)
        self.assertEqual((self.snapshot / "uv.lock.orig").read_bytes(), self.original_lock)
        meta = json.loads((self.snapshot / "snapshot.json").read_text())
        self.assertEqual(meta["package"], "community-base")
        self.run_uv.assert_called_once_with(["sync"], self.repo)

    def test_round_trip_restores_exact_bytes(self):
        self.assertEqual(self.link()[0], 0)
        self.assertEqual(self.unlink()[0], 0)
        self.assertEqual((self.repo / "pyproject.toml").read_bytes(), self.original_pyproject)
        self.assertEqual((self.repo / "uv.lock").read_bytes(), self.original_lock)
        self.assertFalse(self.snapshot.exists())
        self.run_uv.assert_any_call(["sync", "--locked"], self.repo)

    def test_link_refuses_dirty_dependency_files(self):
        (self.repo / "uv.lock").write_text(LOCK + "\n# stray edit\n")
        code, stderr = self.link()
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes", stderr)
        self.assertFalse(self.snapshot.exists())

    def test_link_refuses_when_snapshot_exists(self):
        self.assertEqual(self.link()[0], 0)
        # Commit the link edit so the dirty-files refusal does not shadow the
        # snapshot check and the linked tree looks like a clean linked state.
        commit_all(self.repo, self.root, "linked")
        code, stderr = self.link()
        self.assertEqual(code, 1)
        self.assertIn("core-unlink", stderr)

    def test_link_inserts_into_existing_sources_table(self):
        existing = (
            PYPROJECT + '\n[tool.uv.sources]\nasl-cli = { path = "asl_cli", editable = true }\n'
        )
        write(self.repo / "pyproject.toml", existing)
        commit_all(self.repo, self.root, "with sources table")
        self.assertEqual(self.link()[0], 0)
        text = (self.repo / "pyproject.toml").read_text()
        self.assertEqual(text.count("[tool.uv.sources]"), 1)
        self.assertIn("asl-cli", text)
        self.assertIn("community-base = { path = ", text)
        self.assertEqual(self.unlink()[0], 0)
        self.assertEqual((self.repo / "pyproject.toml").read_text(), existing)

    def test_link_refuses_existing_community_base_entry(self):
        write(
            self.repo / "pyproject.toml",
            PYPROJECT
            + '\n[tool.uv.sources]\ncommunity-base = { path = "../community-base", editable = true }\n',
        )
        commit_all(self.repo, self.root, "pre-linked")
        code, stderr = self.link()
        self.assertEqual(code, 1)
        self.assertIn("already contains a [tool.uv.sources] entry", stderr)
        # Refusal happens before any mutation: no snapshot, no file changes.
        self.assertFalse(self.snapshot.exists())

    def test_link_refuses_missing_sibling(self):
        code, stderr = self.run_tool("link", "--sibling", str(self.root / "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("No community-base checkout", stderr)

    def test_unlink_refuses_without_snapshot(self):
        code, stderr = self.unlink()
        self.assertEqual(code, 1)
        self.assertIn("No link snapshot", stderr)

    def test_unlink_refuses_on_unrelated_pyproject_edit(self):
        self.assertEqual(self.link()[0], 0)
        (self.repo / "pyproject.toml").write_text(
            (self.repo / "pyproject.toml").read_text() + "\n# unrelated manual edit\n"
        )
        code, stderr = self.unlink()
        self.assertEqual(code, 1)
        self.assertIn("changes beyond the link edit", stderr)
        # Recovery state survives so the situation stays recoverable.
        self.assertTrue((self.snapshot / "snapshot.json").exists())

    def test_failed_sync_restores_files_and_removes_snapshot(self):
        self.run_uv.side_effect = community_base_link.LinkError("uv sync failed")
        code, _ = self.link()
        self.assertEqual(code, 1)
        self.assertEqual((self.repo / "pyproject.toml").read_bytes(), self.original_pyproject)
        self.assertEqual((self.repo / "uv.lock").read_bytes(), self.original_lock)
        self.assertFalse(self.snapshot.exists())


class SourceGuardTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)

    def check(self) -> int:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = check_main(["--repo", str(self.repo)])
        self.detained_stderr = stderr.getvalue()
        return code

    def test_tagged_git_source_passes(self):
        self.assertEqual(self.check(), 0)

    def test_missing_dependency_fails(self):
        write(
            self.repo / "pyproject.toml",
            PYPROJECT.replace(
                '    "community-base @ git+https://github.com/DataTalksClub/community-base@v0.3.0",\n',
                "",
            ),
        )
        self.assertEqual(self.check(), 1)

    def test_path_override_fails(self):
        text = (self.repo / "pyproject.toml").read_text()
        write(
            self.repo / "pyproject.toml",
            text
            + '\n[tool.uv.sources]\ncommunity-base = { path = "../community-base", editable = true }\n',
        )
        self.assertEqual(self.check(), 1)
        self.assertIn("core-unlink", self.detained_stderr)

    def test_branch_ref_in_lock_fails(self):
        write(self.repo / "uv.lock", LOCK.replace(f"rev=v0.3.0#{TAG_SHA}", "rev=main"))
        self.assertEqual(self.check(), 1)

    def test_registry_source_in_lock_fails(self):
        write(
            self.repo / "uv.lock",
            LOCK.replace(
                f'source = {{ git = "https://github.com/DataTalksClub/community-base?rev=v0.3.0#{TAG_SHA}" }}',
                'source = { registry = "https://pypi.org/simple" }',
            ),
        )
        self.assertEqual(self.check(), 1)

    def test_cli_exit_codes(self):
        good = subprocess.run(
            [sys.executable, "scripts/check_community_base_source.py", "--repo", str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("rev=v0.3.0", good.stdout)
        write(
            self.repo / "uv.lock",
            LOCK.replace(
                f'source = {{ git = "https://github.com/DataTalksClub/community-base?rev=v0.3.0#{TAG_SHA}" }}',
                'source = { directory = "../community-base" }',
            ),
        )
        bad = subprocess.run(
            [sys.executable, "scripts/check_community_base_source.py", "--repo", str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertTrue(bad.stderr.strip())
