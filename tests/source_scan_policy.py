"""The Tailwind source lint's scan set, shared with the affected-tests map.

``tests/test_tailwind_build.py::TailwindSourceScanTest`` walks every template,
every first-party ``static/js`` file and every ``*.py`` outside the excluded
directories below. Rule 14 in ``scripts/affected_tests.py`` has to select that
lint for exactly the files it reads, so the exclusion set is defined once, here,
and imported by both sides -- the same structural binding the Tailwind producer
list gets through ``scripts.verify_tailwind_build.PRODUCER_FILES``.

Stdlib only and deliberately Django-free: ``scripts/affected_tests.py`` imports
this module and must keep running without ``DJANGO_SETTINGS_MODULE``.

The set mirrors the ignore list in ``tailwind.config.js`` (``!./**/tests/**``,
``!./tests/**``, ``!./playwright_tests/**``, ``!./**/migrations/**``,
``!./node_modules/**``, ``!./staticfiles/**``) plus the scratch/virtualenv
directories the lint skips so an agent worktree copy cannot poison the walk.
"""

from __future__ import annotations

SOURCE_SCAN_EXCLUDED_PARTS: tuple[str, ...] = (
    ".tmp",
    ".venv",
    "migrations",
    "node_modules",
    "playwright_tests",
    "staticfiles",
    "tests",
    "venv",
)


def excluded_path_globs(parts: tuple[str, ...] = SOURCE_SCAN_EXCLUDED_PARTS) -> tuple[str, ...]:
    """fnmatch globs for "a repo-relative path with one of ``parts`` in it".

    ``fnmatch``'s ``*`` spans ``/``, so ``*/tests/*`` covers every depth.
    """
    globs: list[str] = []
    for part in parts:
        globs.append(f"{part}/*")
        globs.append(f"*/{part}/*")
    return tuple(globs)
