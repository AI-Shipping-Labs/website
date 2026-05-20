"""Static-analysis regression for descriptive ``async_task`` names.

Issue #717: every production ``async_task(...)`` call site must pass a
``task_name=`` keyword so the resulting worker-history row carries a
descriptive name instead of a Django-Q random codename (e.g.
``texas-texas-oscar-earth``). #628 closed the gap by introducing
``jobs.tasks.names.build_task_name`` and threading ``task_name=`` through
every direct call site; this test exists to make sure no new site lands
without ``task_name=``.

The test parses each ``.py`` file under the repo with ``ast`` and asserts
that every ``Call`` whose function is named ``async_task`` includes
``task_name`` in its keyword arguments.

Out of scope (explicitly excluded with documented rationale):

- ``jobs/tasks/helpers.py`` — this is the wrapper. The inner call to
  ``q_async_task`` (aliased ``async_task``) forwards ``**kwargs`` from
  the caller, so ``task_name`` rides along when the caller supplied it.
- ``integrations/services/content_sync_queue.py:_enqueue_async_task`` —
  another wrapper. It builds a ``task_name`` (defaulting via
  ``_content_sync_task_name``) into a ``kwargs`` dict and splats it into
  ``async_task(..., **kwargs)``.
- ``studio/views/worker.py:_resubmit_failed`` — passes ``task_name=`` as
  a literal keyword and is therefore covered by the assertion (no
  exclusion needed; it's listed here for clarity).
- Files under ``tests/`` or named ``test_*.py`` — test scaffolding.
- Files under ``.venv/`` and other vendored dirs.
- Docstring-only ``async_task(...)`` references inside ``\"\"\"`` blocks
  — these are not ``ast.Call`` nodes and are skipped naturally by AST
  parsing.
"""

import ast
import pathlib
from collections.abc import Iterable

from django.test import SimpleTestCase

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# Files allowed to call async_task() without task_name=. Each entry is a
# repo-relative posix path and must carry an inline justification above.
EXEMPT_FILES = frozenset({
    # The helper that wraps django-q's async_task. The inner call forwards
    # **kwargs from its caller, so task_name rides along when the caller
    # supplied it. The helper itself does not synthesize a name.
    'jobs/tasks/helpers.py',
    # Wrapper around async_task. Builds a default task_name via
    # _content_sync_task_name() into a kwargs dict and splats it into
    # async_task(..., **kwargs). The kwargs dict construction is verified
    # by integrations/tests/test_content_sync_queue.py.
    'integrations/services/content_sync_queue.py',
})


SKIP_DIR_NAMES = frozenset({
    '.venv',
    '.git',
    'node_modules',
    '__pycache__',
    '.tox',
    '.mypy_cache',
    '.pytest_cache',
    'static',
    'staticfiles',
    'media',
    '.claude',
})


def _iter_python_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in root.rglob('*.py'):
        # Filter against repo-relative parts. Filtering ``path.parts``
        # (absolute) would silently exclude the whole tree when the repo
        # lives under a directory whose name is in SKIP_DIR_NAMES — e.g.
        # an orchestrator worktree at ``.../.claude/worktrees/<id>/`` —
        # making the test pass vacuously. See issue #745.
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIR_NAMES for part in rel_parts):
            continue
        # Skip test scaffolding.
        if 'tests' in rel_parts:
            continue
        if path.name.startswith('test_'):
            continue
        yield path


def _call_func_name(call: ast.Call) -> str | None:
    """Return the bare name of the callable in a ``Call`` node, if any."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _has_task_name_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        # Direct ``task_name=...`` kwarg.
        if kw.arg == 'task_name':
            return True
        # ``**kwargs`` splat — the caller may forward task_name through it.
        # We treat this as a wrapper signal and trust the call site
        # (these sites are explicitly listed in EXEMPT_FILES with
        # justification, so a bare ``**kwargs`` splat outside that
        # allowlist must still fail the test).
    return False


class AsyncTaskTaskNameStaticAnalysisTest(SimpleTestCase):
    """Every async_task(...) call site in production code passes task_name=."""

    def test_every_async_task_call_has_task_name(self):
        offenders = []

        for path in _iter_python_files(PROJECT_ROOT):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel in EXEMPT_FILES:
                continue

            source = path.read_text(encoding='utf-8')
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:  # pragma: no cover - malformed file
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_func_name(node) != 'async_task':
                    continue
                if _has_task_name_kwarg(node):
                    continue
                offenders.append(f"{rel}:{node.lineno}")

        self.assertFalse(
            offenders,
            msg=(
                "Issue #717: every production async_task(...) call must pass "
                "a task_name= keyword so the worker-history row carries a "
                "descriptive name. Offending sites:\n  - "
                + "\n  - ".join(offenders)
                + "\n\nFix: build a descriptive name via "
                "jobs.tasks.names.build_task_name(action, target, source) "
                "and pass it as task_name=. If your call site is an internal "
                "wrapper that forwards **kwargs (and therefore task_name) "
                "from its caller, add it to EXEMPT_FILES with a justification."
            ),
        )

    def test_iterator_yields_production_files(self):
        """Guard against zero-yield regressions in ``_iter_python_files``.

        Issue #745: a previous version filtered ``SKIP_DIR_NAMES`` against
        ``path.parts`` of the absolute path, so when the repo lived under
        a worktree directory whose name (``.claude``) was in the skip
        list, the iterator yielded nothing and the suite passed
        vacuously. This test pins a concrete lower bound on the number
        of yielded production files so any future regression that
        collapses the walk to zero (or near-zero) is caught.
        """
        files = list(_iter_python_files(PROJECT_ROOT))
        self.assertGreater(
            len(files),
            10,
            msg=(
                f"_iter_python_files yielded only {len(files)} file(s) under "
                f"{PROJECT_ROOT}. Expected the production tree to contain at "
                "least a dozen Python modules; a near-zero count means the "
                "walk filter is excluding everything. See issue #745."
            ),
        )

    def test_exempt_files_still_exist(self):
        """If an EXEMPT file is renamed/removed, drop it from the allowlist.

        This guards against a stale exemption silently masking a new
        unnamed call site that happens to share the old path.
        """
        for rel in EXEMPT_FILES:
            path = PROJECT_ROOT / rel
            self.assertTrue(
                path.exists(),
                msg=f"EXEMPT_FILES entry {rel!r} does not exist; drop it.",
            )
