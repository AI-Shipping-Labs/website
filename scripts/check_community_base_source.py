"""Fail-closed guard for the community-base dependency source.

Runs in CI (ci.yml) and Deploy Dev (deploy-dev.yml) before `uv sync --locked`.
It verifies that `community-base` is the pinned tagged git release from
DataTalksClub/community-base in BOTH `pyproject.toml` and `uv.lock`:

- accepted: `community-base @ git+https://github.com/DataTalksClub/community-base@vX.Y.Z`
  with the matching tagged git source in `uv.lock`;
- rejected: local path, file/editable source, a `[tool.uv.sources]` override,
  a branch or bare-commit ref, a registry copy, or a missing dependency.

A local path/editable/branch source would ship unreviewed package code to the
site runtime; this guard fails nonzero with actionable text before any
installation. Standard library only (runs before dependencies are installed).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PACKAGE_NAME = "community-base"
GITHUB_SLUG = "datatalksclub/community-base"
TAGGED_DIRECT_REF_RE = re.compile(
    rf"^{PACKAGE_NAME}(?:\[[a-z0-9_, -]+\])?\s*@\s*git\+https://github\.com/{GITHUB_SLUG}@v\d+[0-9A-Za-z.\-]*$",
    re.IGNORECASE,
)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def canonical_name(raw: str) -> str:
    return re.sub(r"[-_]+", "-", raw).lower()


def dependency_requirement(project: dict) -> str | None:
    for entry in project.get("dependencies", []):
        if not isinstance(entry, str):
            continue
        name = re.split(r"\s*[\[=<>!~@;]", entry, maxsplit=1)[0]
        if canonical_name(name) == PACKAGE_NAME:
            return entry.strip()
    return None


def check(repo: Path) -> int:
    try:
        with open(repo / "pyproject.toml", "rb") as handle:
            pyproject = tomllib.load(handle)
        with open(repo / "uv.lock", "rb") as handle:
            lock = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return fail(f"cannot read dependency metadata: {exc}")

    requirement = dependency_requirement(pyproject.get("project", {}))
    if requirement is None:
        return fail(
            f"the {PACKAGE_NAME} dependency is missing from pyproject.toml; "
            f"expected the pinned git dependency ({GITHUB_SLUG} at a v* tag)."
        )
    if not TAGGED_DIRECT_REF_RE.match(requirement):
        return fail(
            f"pyproject.toml declares {PACKAGE_NAME!r} as {requirement!r}; "
            f"expected 'community-base @ git+https://github.com/{GITHUB_SLUG}@vX.Y.Z'. "
            "Local path, editable, branch, and registry sources are not allowed to commit."
        )

    override = pyproject.get("tool", {}).get("uv", {}).get("sources", {}).get(PACKAGE_NAME)
    if override is not None:
        return fail(
            f"[tool.uv.sources] overrides {PACKAGE_NAME} ({override!r}); this is a local "
            "development link. Run `make core-unlink` before committing."
        )

    locked = [p for p in lock.get("package", []) if canonical_name(p.get("name", "")) == PACKAGE_NAME]
    if not locked:
        return fail(f"uv.lock has no {PACKAGE_NAME} package entry; run `uv lock` and commit it.")
    if len(locked) > 1:
        return fail(f"uv.lock has {len(locked)} {PACKAGE_NAME} entries; regenerate uv.lock.")
    source = locked[0].get("source", {})
    git_url = source.get("git")
    if not isinstance(git_url, str) or GITHUB_SLUG not in git_url.lower():
        return fail(
            f"uv.lock resolves {PACKAGE_NAME} from source {source!r}; "
            f"expected the pinned git source ({GITHUB_SLUG} at a v* tag)."
        )
    if "rev=v" not in git_url.lower():
        return fail(
            f"uv.lock pins {PACKAGE_NAME} to a non-tag git ref ({git_url!r}); "
            "pin the dependency to a released vX.Y.Z tag."
        )

    print(f"community-base source OK: {git_url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Site repository root (default: the checkout containing this script).",
    )
    args = parser.parse_args(argv)
    return check(args.repo.resolve())


if __name__ == "__main__":
    sys.exit(main())
