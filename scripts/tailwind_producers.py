"""Discover repository-owned Tailwind class producers."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCER_RELATIVE_FILES = (
    "accounts/templatetags/accounts_extras.py",
    "content/templatetags/member_badges.py",
    "content/models/project.py",
    "content/models/download.py",
    "content/views/courses.py",
    "content/views/tags.py",
    "studio/templatetags/studio_filters.py",
    "plans/models.py",
    "email_app/ses_explain.py",
)
PRODUCER_FILES = [ROOT / relative for relative in PRODUCER_RELATIVE_FILES]
PRODUCER_VIEW_FAMILY = "studio/views"
PRODUCER_PART_FAMILIES = ("forms", "widgets")
PRODUCER_PART_EXCLUSIONS = ("tests", "migrations", ".venv")
PRODUCER_JS_FAMILY = "static/js"
PRODUCER_JS_EXCLUDED_PART = "vendor"
PRODUCER_TOOL_FILES = (
    "scripts/verify_tailwind_build.py",
    "scripts/tailwind_producers.py",
)


class ProducerDiscoveryError(RuntimeError):
    """The repository-owned Tailwind source boundary could not be read."""


def _git_source_paths(root):
    command = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProducerDiscoveryError(f"cannot discover repository-owned sources in {root}") from exc

    paths = []
    for raw_path in completed.stdout.split(b"\0"):
        if raw_path:
            paths.append(Path(os.fsdecode(raw_path)))
    if not paths:
        raise ProducerDiscoveryError(f"Git returned no repository-owned sources in {root}")
    return sorted(set(paths))


def repository_source_files(root=ROOT):
    """Return existing tracked and non-ignored untracked files."""
    root = Path(root).resolve()
    files = []
    for relative in _git_source_paths(root):
        path = root / relative
        if path.is_file():
            files.append(path)
    if not files:
        raise ProducerDiscoveryError(f"Git returned no readable repository-owned sources in {root}")
    return files


def _is_python_producer(relative):
    if relative.suffix != ".py":
        return False
    if relative.as_posix() in PRODUCER_RELATIVE_FILES:
        return True
    if relative.parent.as_posix() == PRODUCER_VIEW_FAMILY:
        return True
    parts = set(relative.parts)
    return bool(parts.intersection(PRODUCER_PART_FAMILIES)) and not parts.intersection(PRODUCER_PART_EXCLUSIONS)


def producer_python_files(root=ROOT):
    """Return repository-owned Python files that can produce class literals."""
    root = Path(root).resolve()
    files = []
    for path in repository_source_files(root):
        if _is_python_producer(path.relative_to(root)):
            files.append(path)
    return sorted(set(files))


def _is_javascript_producer(relative):
    if relative.suffix != ".js":
        return False
    family = Path(PRODUCER_JS_FAMILY)
    try:
        family_relative = relative.relative_to(family)
    except ValueError:
        return False
    return PRODUCER_JS_EXCLUDED_PART not in family_relative.parts


def producer_javascript_files(root=ROOT):
    """Return repository-owned first-party JavaScript class producers."""
    root = Path(root).resolve()
    files = []
    for path in repository_source_files(root):
        if _is_javascript_producer(path.relative_to(root)):
            files.append(path)
    return sorted(set(files))


def producer_paths(root=ROOT):
    """Return every file read by the Tailwind producer verifier."""
    return [*producer_python_files(root), *producer_javascript_files(root)]
