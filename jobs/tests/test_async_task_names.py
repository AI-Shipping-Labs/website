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


# Modules from which ``async_task`` is imported. An import like
# ``from django_q.tasks import async_task as enqueue`` binds the enqueue
# callable to ``enqueue``; calls through that alias must still be audited.
_ASYNC_TASK_SOURCE_MODULES = frozenset({
    'django_q.tasks',
    'jobs.tasks',
    'jobs.tasks.helpers',
})


def _async_task_aliases(tree: ast.AST) -> set[str]:
    """Return every local name bound to the enqueue ``async_task`` callable.

    Resolves ``from <module> import async_task`` and
    ``from <module> import async_task as <alias>`` (both module-level and
    function-local) so a call through an alias such as
    ``from django_q.tasks import async_task as enqueue`` is audited too.
    The bare name ``async_task`` is always included.
    """
    aliases = {'async_task'}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in _ASYNC_TASK_SOURCE_MODULES:
            continue
        for alias in node.names:
            if alias.name == 'async_task':
                aliases.add(alias.asname or alias.name)
    return aliases


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

            # Resolve any aliased ``async_task`` imports so calls through an
            # alias (e.g. ``async_task as enqueue``) are audited too.
            callable_names = _async_task_aliases(tree)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_func_name(node) not in callable_names:
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

    def test_aliased_async_task_import_is_audited(self):
        """A ``from ... import async_task as <alias>`` call is still checked.

        The bare-name match alone would miss an aliased enqueue. This pins the
        alias-resolution so introducing ``async_task as enqueue`` without a
        ``task_name=`` is caught.
        """
        source = (
            "from django_q.tasks import async_task as enqueue\n"
            "enqueue('some.task')\n"
        )
        tree = ast.parse(source)
        aliases = _async_task_aliases(tree)
        self.assertIn('enqueue', aliases)

        offending = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_func_name(node) in aliases
            and not _has_task_name_kwarg(node)
        ]
        self.assertEqual(
            len(offending),
            1,
            msg="Aliased async_task call without task_name= must be flagged.",
        )

    def test_aliased_async_task_with_task_name_passes(self):
        """An aliased enqueue that passes ``task_name=`` is not flagged."""
        source = (
            "from django_q.tasks import async_task as enqueue\n"
            "enqueue('some.task', task_name='Descriptive name')\n"
        )
        tree = ast.parse(source)
        aliases = _async_task_aliases(tree)
        offending = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_func_name(node) in aliases
            and not _has_task_name_kwarg(node)
        ]
        self.assertEqual(offending, [])

    def test_every_schedule_passes_static_name(self):
        """Every ``schedule(...)`` in setup_schedules.py passes ``name=``.

        Issue #920 acceptance criterion: no schedule may rely on the func-path
        default name. This AST guard fails if a new schedule is registered
        without a descriptive static ``name=`` keyword.
        """
        path = PROJECT_ROOT / 'jobs' / 'management' / 'commands' / 'setup_schedules.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_func_name(node) != 'schedule':
                continue
            name_kw = next(
                (kw for kw in node.keywords if kw.arg == 'name'), None
            )
            if name_kw is None or not (
                isinstance(name_kw.value, ast.Constant)
                and isinstance(name_kw.value.value, str)
                and name_kw.value.value.strip()
            ):
                offenders.append(f"setup_schedules.py:{node.lineno}")
        self.assertFalse(
            offenders,
            msg=(
                "Every schedule(...) must pass a non-empty static name=. "
                "Offending sites:\n  - " + "\n  - ".join(offenders)
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
