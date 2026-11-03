"""Link the pinned community-base dependency to a local sibling checkout.

Implements playbook P1 from DataTalksClub/community-base (`docs/03-playbooks.md`):
`make core-link` points the pinned git dependency at a local checkout for
development, `make core-unlink` restores the pinned dependency exactly.

Contract:

- The root dependency files (`pyproject.toml`, `uv.lock`) and, when present,
  `asl_cli/pyproject.toml` must be clean to link; their exact original bytes
  are snapshotted under `.tmp/core-link/`.
- Link refuses when a snapshot already exists, when the sibling checkout is
  missing or is not a community-base checkout, or when the dependency files
  carry uncommitted changes.
- Unlink refuses when there is no snapshot or when a dependency manifest has
  changes beyond the link edit; it never discards unrelated edits and never
  uses a blanket `git checkout --`.
- Nothing outside the dependency files and `.tmp/core-link/` is mutated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")
ASL_CLI_DEPENDENCY_FILE = "asl_cli/pyproject.toml"
_COMMUNITY_BASE_GIT_REQUIREMENT = re.compile(
    r"(?P<quote>['\"])(?P<requirement>community-base(?:\[[^]]+\])?)"
    r"\s*@\s*git\+[^'\"]+(?P=quote)"
)
PACKAGE_NAME = "community-base"
SNAPSHOT_DIRNAME = Path(".tmp") / "core-link"
SNAPSHOT_META = "snapshot.json"
SOURCE_TABLE = "[tool.uv.sources]"
DEFAULT_SIBLING = Path("..") / "community-base"


class LinkError(Exception):
    """A refusal with actionable text; the command exits nonzero."""


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def snapshot_dir(repo: Path) -> Path:
    return repo / SNAPSHOT_DIRNAME


def dependency_paths(repo: Path) -> dict[str, Path]:
    paths = {name: repo / name for name in DEPENDENCY_FILES}
    cli_dependency = repo / ASL_CLI_DEPENDENCY_FILE
    if cli_dependency.is_file():
        paths[ASL_CLI_DEPENDENCY_FILE] = cli_dependency
    return paths


def snapshot_path(target: Path, dependency: str) -> Path:
    """Return a flat snapshot filename for a repository-relative path."""
    return target / f"{dependency.replace('/', '__')}.orig"


def git(args: list[str], repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise LinkError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def assert_dependency_files_clean(repo: Path) -> None:
    dependency_names = tuple(dependency_paths(repo))
    for staged_args in (["diff", "--quiet", "--"], ["diff", "--cached", "--quiet", "--"]):
        result = subprocess.run(
            ["git", "-C", str(repo), *staged_args, *dependency_names],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise LinkError(
                "Dependency files have uncommitted changes. Commit or stash them before running `make core-link`."
            )


def run_uv(args: list[str], repo: Path) -> None:
    try:
        result = subprocess.run(["uv", *args], cwd=repo)
    except FileNotFoundError as exc:
        raise LinkError("uv is not installed or not on PATH; it is required to link.") from exc
    if result.returncode != 0:
        raise LinkError(f"uv {' '.join(args)} failed with exit code {result.returncode}.")


def resolve_sibling(repo: Path, sibling_arg: Path | None) -> Path:
    sibling = (sibling_arg or DEFAULT_SIBLING).expanduser()
    if not sibling.is_absolute():
        sibling = repo / sibling
    sibling = sibling.resolve()
    project_toml = sibling / "pyproject.toml"
    if not project_toml.is_file():
        raise LinkError(
            f"No community-base checkout at {sibling}. Clone DataTalksClub/community-base "
            f"there or pass the checkout with --sibling."
        )
    try:
        with open(project_toml, "rb") as handle:
            name = tomllib.load(handle).get("project", {}).get("name")
    except tomllib.TOMLDecodeError as exc:
        raise LinkError(f"{project_toml} is not valid TOML: {exc}") from exc
    if name != PACKAGE_NAME:
        raise LinkError(f"{project_toml} declares project name {name!r}, expected {PACKAGE_NAME!r}.")
    return sibling


def source_entry_line(relative_path: str) -> str:
    return f'{PACKAGE_NAME} = {{ path = "{relative_path}", editable = true }}'


def existing_source_entry(pyproject_bytes: bytes) -> object | None:
    try:
        data = tomllib.loads(pyproject_bytes.decode())
    except tomllib.TOMLDecodeError as exc:
        raise LinkError(f"pyproject.toml is not valid TOML: {exc}") from exc
    return data.get("tool", {}).get("uv", {}).get("sources", {}).get(PACKAGE_NAME)


def link_edit(pyproject_bytes: bytes, relative_path: str) -> bytes:
    """Return the pyproject bytes with the link entry added.

    Inserts the community-base key into an existing [tool.uv.sources] table
    (directly after its header) when one is present, and appends a new table
    otherwise. Deterministic: the same original bytes and relative path always
    produce the same result, so unlink can recompute the expected state.
    """
    entry = source_entry_line(relative_path)
    lines = pyproject_bytes.decode().splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == SOURCE_TABLE:
            lines.insert(index + 1, entry + "\n")
            return "".join(lines).encode()
    block = (
        f"\n# Added by `make core-link` (community-base playbook P1). Remove with `make core-unlink`.\n"
        f"{SOURCE_TABLE}\n"
        f"{entry}\n"
    )
    return pyproject_bytes + block.encode()


def link_asl_cli_edit(pyproject_bytes: bytes) -> bytes:
    """Drop the nested CLI project's Git URL so the root source can win.

    AISL's root project and its editable ``asl-cli`` project both depend on
    community-base. uv rejects a local path override when the nested project
    still declares a conflicting direct Git URL. Replacing only that URL
    keeps any requested extras and lets the root ``tool.uv.sources`` entry
    select the shared checkout for the whole environment.
    """
    content = pyproject_bytes.decode()
    linked = _COMMUNITY_BASE_GIT_REQUIREMENT.sub(
        lambda match: f"{match.group('quote')}{match.group('requirement')}{match.group('quote')}",
        content,
    )
    return linked.encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_link(repo: Path, sibling_arg: Path | None) -> None:
    paths = dependency_paths(repo)
    assert_dependency_files_clean(repo)
    target = snapshot_dir(repo)
    if target.exists():
        raise LinkError(
            f"{target} already exists; a link is already active. "
            "Run `make core-unlink` first (recovery state is never overwritten)."
        )
    sibling = resolve_sibling(repo, sibling_arg)
    relative_path = os.path.relpath(sibling, repo).replace(os.sep, "/")
    originals = {name: path.read_bytes() for name, path in paths.items()}
    existing = existing_source_entry(originals["pyproject.toml"])
    if existing is not None:
        raise LinkError(
            f"pyproject.toml already contains a [tool.uv.sources] entry for {PACKAGE_NAME} "
            f"({existing!r}); remove it manually before running `make core-link`."
        )
    target.mkdir(parents=True)
    try:
        for name, data in originals.items():
            snapshot_path(target, name).write_bytes(data)
        (target / SNAPSHOT_META).write_text(
            json.dumps(
                {
                    "package": PACKAGE_NAME,
                    "sibling_relative_path": relative_path,
                    "files": {name: sha256(data) for name, data in originals.items()},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        linked = {
            "pyproject.toml": link_edit(originals["pyproject.toml"], relative_path),
        }
        if ASL_CLI_DEPENDENCY_FILE in originals:
            linked[ASL_CLI_DEPENDENCY_FILE] = link_asl_cli_edit(originals[ASL_CLI_DEPENDENCY_FILE])
        for name, data in linked.items():
            paths[name].write_bytes(data)
        run_uv(["sync"], repo)
    except Exception:
        for name, data in originals.items():
            paths[name].write_bytes(data)
        for name in originals:
            snapshot_path(target, name).unlink(missing_ok=True)
        (target / SNAPSHOT_META).unlink(missing_ok=True)
        target.rmdir()
        raise

    print(f"Linked {PACKAGE_NAME} to {sibling} ({relative_path}).")
    print(
        'Verify with: uv run python -c "import community_base, pathlib; '
        'print(pathlib.Path(community_base.__file__).resolve())"'
    )


def expected_linked_files(repo: Path) -> dict[str, bytes]:
    target = snapshot_dir(repo)
    meta = json.loads((target / SNAPSHOT_META).read_text())
    expected = {}
    for name in meta["files"]:
        if name == "uv.lock":
            # uv sync rewrites the lock for the linked source; restore the
            # snapshotted bytes below instead of treating that as a conflict.
            continue
        original = snapshot_path(target, name).read_bytes()
        if name == "pyproject.toml":
            expected[name] = link_edit(original, meta["sibling_relative_path"])
        elif name == ASL_CLI_DEPENDENCY_FILE:
            expected[name] = link_asl_cli_edit(original)
        else:
            expected[name] = original
    return expected


def cmd_unlink(repo: Path) -> None:
    paths = dependency_paths(repo)
    target = snapshot_dir(repo)
    if not target.is_dir():
        raise LinkError(
            f"No link snapshot at {target}; there is nothing to unlink. "
            "If a link was interrupted, restore the dependency files by hand."
        )
    meta = json.loads((target / SNAPSHOT_META).read_text())
    expected = expected_linked_files(repo)
    for name, content in expected.items():
        if name not in paths or paths[name].read_bytes() != content:
            raise LinkError(
                f"{name} has changes beyond the link edit. Resolve them manually "
                "(keep your edits, remove the link changes), then delete "
                f"{target} once the dependency files are in the pinned state."
            )
    restored = {name: snapshot_path(target, name).read_bytes() for name in meta["files"]}
    for name, data in restored.items():
        paths[name].write_bytes(data)
    run_uv(["sync", "--locked"], repo)
    for name in restored:
        snapshot_path(target, name).unlink()
    (target / SNAPSHOT_META).unlink()
    target.rmdir()
    print(f"Restored the pinned {PACKAGE_NAME} dependency from the snapshot.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["link", "unlink"])
    parser.add_argument(
        "--repo",
        type=Path,
        default=repo_root_from_script(),
        help="Site repository root (default: the checkout containing this script).",
    )
    parser.add_argument(
        "--sibling",
        type=Path,
        default=None,
        help="Local community-base checkout (default: ../community-base).",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == "link":
            cmd_link(repo, args.sibling)
        else:
            cmd_unlink(repo)
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
