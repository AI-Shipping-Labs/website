"""Native guards for Playwright tests eligible to run against deployed dev.

The scheduled dev workflow (``scheduled-playwright-dev.yml``) runs the Playwright
suite against ``https://dev.aishippinglabs.com`` on a 3-hour cron with the marker
filter::

    not manual_visual and not slow_platform
    and not visual_regression and not local_only and not creates_data

Because ``PLAYWRIGHT_BASE_URL`` is then a non-local host, the centrally
configured browser-journey policy also auto-skips anything marked ``local_only``
/ ``creates_data``. The net effect: only the module-level dev-eligible files
actually run against dev.

Against live dev on a cold/contended 4-shard runner -- especially mid
rolling-deploy -- a bare ``page.goto(...)`` (no bounded 5xx retry) followed by an
immediate ``.evaluate`` (no attach-wait) can race element attachment or hit a
transient 5xx, producing red scheduled runs that are not real regressions and
auto-file ``[CI]`` noise (Issue #1083, then the #1084 sweep). The shared helpers
``goto_with_retry`` (#928) and ``SETTLE_TIMEOUT_MS`` (#903) fix this.

This native policy test computes the dev-eligible file set the same way the dev
workflow does and fails if any of those files use a bare ``page.goto(`` without
also routing navigation through ``goto_with_retry``. The affected-test mapper
runs it whenever a Playwright module changes, so a regression fails before the
next scheduled dev run.

It also checks each collected test function for direct calls to canonical local
database/session helpers. Those calls require ``local_only`` or ``creates_data``
ownership at module or item scope; otherwise a runner-local user or cookie can
leak into remote selection even though it cannot authenticate the deployed host.

The check is intentionally coarse (presence of ``goto_with_retry`` in a file that
contains ``page.goto(``), not full data-flow analysis -- its job is to catch the
obvious regression and stay maintainable, not to be a perfect linter.
"""

import ast
import re
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_DIR = ROOT / "playwright_tests"
SCRATCH = ROOT / ".tmp" / "dev-goto-resilience-guard"

# Inline opt-out: a ``page.goto(`` on a line ending with this marker is treated
# as a deliberate, reviewed exception (e.g. a navigation that genuinely cannot
# use the wrapper). Keep this for documented edge cases only.
INLINE_OPT_OUT = "# dev-goto-ok"

# Detects a module-level ``local_only`` marker, covering both the single-marker
# form ``pytestmark = pytest.mark.local_only`` and the list form
# ``pytestmark = [pytest.mark.local_only, ...]`` (possibly spanning lines).
_PYTESTMARK_BLOCK_RE = re.compile(r"^pytestmark\s*=\s*(.+?)(?=^\S|\Z)", re.MULTILINE | re.DOTALL)
_BARE_GOTO_RE = re.compile(r"\bpage\.goto\s*\(")
_LOCAL_STATE_HELPERS = frozenset({
    "auth_context",
    "create_session_for_user",
    "create_staff_user",
    "create_user",
    "ensure_site_config_tiers",
    "ensure_tiers",
})
_REMOTE_EXCLUSION_MARKS = frozenset({"creates_data", "local_only"})


def _module_level_marks(source):
    """Return the raw text of the module-level ``pytestmark`` assignment(s)."""
    return "\n".join(m.group(1) for m in _PYTESTMARK_BLOCK_RE.finditer(source))


def _is_module_level_local_only(source):
    """True when the file marks its whole module ``local_only`` / ``creates_data``.

    Such files never run against dev (auto-skipped by the central selector on a
    non-local base URL), so they are out of scope for this guard.
    """
    marks = _module_level_marks(source)
    return "local_only" in marks or "creates_data" in marks


def _dev_eligible_files(playwright_dir=PLAYWRIGHT_DIR):
    """Source files in ``playwright_tests/`` that run against the deployed dev env.

    Mirrors the dev workflow: a file is dev-eligible unless its module-level
    markers exclude it (``local_only`` / ``creates_data``). Per-test markers are
    intentionally NOT consulted here -- a file with at least one dev-eligible test
    still reaches dev and must keep its navigations resilient.
    """
    eligible = []
    for path in sorted(playwright_dir.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if _is_module_level_local_only(source):
            continue
        eligible.append(path)
    return eligible


def _navigation_offenders(paths):
    offenders = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        bare_goto_lines = [
            (line_number, line)
            for line_number, line in enumerate(source.splitlines(), start=1)
            if _BARE_GOTO_RE.search(line) and INLINE_OPT_OUT not in line
        ]
        if not bare_goto_lines:
            continue
        if "goto_with_retry" not in source:
            offenders.append(
                f"{path.name}: has bare page.goto( at lines "
                f"{[line_number for line_number, _ in bare_goto_lines]} "
                f"but never references goto_with_retry. Route dev-eligible "
                f"navigations through goto_with_retry (or annotate a "
                f"reviewed exception with '{INLINE_OPT_OUT}')."
            )
    return offenders


def _pytest_marker_name(node):
    """Return the name from an exact ``pytest.mark.<name>`` expression."""
    if not isinstance(node, ast.Attribute):
        return None
    mark = node.value
    if not isinstance(mark, ast.Attribute) or mark.attr != "mark":
        return None
    if not isinstance(mark.value, ast.Name) or mark.value.id != "pytest":
        return None
    return node.attr


def _has_remote_exclusion_marker(nodes):
    return any(
        _pytest_marker_name(candidate) in _REMOTE_EXCLUSION_MARKS
        for node in nodes
        for candidate in ast.walk(node)
    )


def _module_has_remote_exclusion_marker(tree):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in targets
        ):
            continue
        if _has_remote_exclusion_marker([node.value]):
            return True
    return False


def _canonical_helper_bindings(tree):
    """Map direct imports from Playwright conftest to canonical helper names."""
    bindings = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "playwright_tests.conftest":
            continue
        for imported in node.names:
            if imported.name in _LOCAL_STATE_HELPERS:
                bindings[imported.asname or imported.name] = imported.name
    return bindings


def _iter_test_functions(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                yield node, node.name, []
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith(
                "test_"
            ):
                yield method, f"{node.name}.{method.name}", node.decorator_list


def _local_state_helper_offenders(paths):
    """Find remote-eligible tests that directly call local-state helpers."""
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bindings = _canonical_helper_bindings(tree)
        if not bindings:
            continue
        module_is_owned = _module_has_remote_exclusion_marker(tree)
        for test, qualified_name, class_decorators in _iter_test_functions(tree):
            called_helpers = sorted({
                bindings[call.func.id]
                for call in ast.walk(test)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in bindings
            })
            if not called_helpers:
                continue
            function_is_owned = _has_remote_exclusion_marker(
                [*class_decorators, *test.decorator_list]
            )
            if module_is_owned or function_is_owned:
                continue
            offenders.append(
                f"{path.name}:{test.lineno} {qualified_name} directly calls local "
                f"Playwright helper(s) {', '.join(called_helpers)} without "
                "pytest.mark.local_only or pytest.mark.creates_data ownership. "
                "Classify the journey for the local lane; do not weaken its assertions."
            )
    return offenders


class DevGotoResilienceGuardTest(SimpleTestCase):
    def test_dev_eligible_universe_is_discoverable(self):
        """The eligible set is non-empty and includes a known dev module."""
        eligible = _dev_eligible_files()
        names = {path.name for path in eligible}

        self.assertTrue(names, "Expected at least one dev-eligible Playwright file")
        self.assertEqual(eligible, sorted(eligible))
        self.assertIn(
            "test_testimonials_layout.py",
            names,
            "test_testimonials_layout.py should be dev-eligible (it is not "
            "module-level local_only); the guard must cover it.",
        )

        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="universe-", dir=SCRATCH) as root:
            synthetic_dir = Path(root)
            synthetic_dir.joinpath("test_new_journey.py").write_text(
                "def test_new_journey():\n    return None\n",
                encoding="utf-8",
            )
            synthetic_dir.joinpath("test_local_only.py").write_text(
                "import pytest\n\npytestmark = pytest.mark.local_only\n",
                encoding="utf-8",
            )
            synthetic_dir.joinpath("helper.py").write_text(
                "not a collected test module\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [path.name for path in _dev_eligible_files(synthetic_dir)],
                ["test_new_journey.py"],
                "Discovery must include new test_*.py modules without a "
                "frozen file list and exclude only module-level policy marks.",
            )

    def test_dev_eligible_files_route_navigation_through_goto_with_retry(self):
        """Every dev-eligible file that navigates uses ``goto_with_retry``.

        A dev-eligible file that contains a bare ``page.goto(`` (not annotated
        with the ``# dev-goto-ok`` opt-out) must also reference
        ``goto_with_retry`` so its live-dev navigations get the bounded 5xx
        retry. This is the regression the #1084 sweep guards against.
        """
        offenders = _navigation_offenders(_dev_eligible_files())

        self.assertEqual(
            offenders,
            [],
            "Dev-eligible Playwright files must route navigation through "
            "goto_with_retry to survive rolling dev deploys (Issue #1084):\n"
            + "\n".join(offenders),
        )

        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bypass-", dir=SCRATCH) as root:
            bypass = Path(root) / "test_new_bypass.py"
            bypass.write_text(
                "def test_new_bypass(page):\n"
                "    destination = '/events'\n"
                "    page.goto(destination)\n",
                encoding="utf-8",
            )

            diagnostics = _navigation_offenders([bypass])
            self.assertEqual(len(diagnostics), 1)
            self.assertIn("test_new_bypass.py", diagnostics[0])
            self.assertIn("lines [3]", diagnostics[0])
            self.assertIn("Route dev-eligible navigations", diagnostics[0])
            self.assertIn("# dev-goto-ok", diagnostics[0])

    def test_remote_eligible_tests_do_not_call_local_state_helpers(self):
        offenders = _local_state_helper_offenders(
            sorted(PLAYWRIGHT_DIR.glob("test_*.py"))
        )

        self.assertEqual(
            offenders,
            [],
            "Remote-eligible Playwright tests cannot depend directly on local "
            "database/session helpers (Issue #1600):\n" + "\n".join(offenders),
        )

    def test_unmarked_direct_local_helper_call_reports_ownership_action(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="local-helper-", dir=SCRATCH) as root:
            path = Path(root) / "test_unmarked_helpers.py"
            path.write_text(
                "from playwright_tests.conftest import (\n"
                "    auth_context, create_session_for_user, create_staff_user,\n"
                "    create_user, ensure_site_config_tiers, ensure_tiers,\n"
                ")\n\n"
                "def test_authenticated_journey():\n"
                "    auth_context()\n"
                "    create_session_for_user()\n"
                "    create_staff_user()\n"
                "    create_user()\n"
                "    ensure_site_config_tiers()\n"
                "    ensure_tiers()\n",
                encoding="utf-8",
            )

            diagnostics = _local_state_helper_offenders([path])

        self.assertEqual(len(diagnostics), 1)
        self.assertIn("test_unmarked_helpers.py:6", diagnostics[0])
        self.assertIn("test_authenticated_journey", diagnostics[0])
        for helper in sorted(_LOCAL_STATE_HELPERS):
            self.assertIn(helper, diagnostics[0])
        self.assertIn("pytest.mark.local_only", diagnostics[0])
        self.assertIn("pytest.mark.creates_data", diagnostics[0])
        self.assertIn("do not weaken its assertions", diagnostics[0])

    def test_module_and_function_ownership_preserve_mixed_remote_module(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="owned-helper-", dir=SCRATCH) as root:
            synthetic_dir = Path(root)
            module_owned = synthetic_dir / "test_module_owned.py"
            module_owned.write_text(
                "import pytest\n"
                "from playwright_tests.conftest import create_user\n\n"
                "pytestmark = pytest.mark.local_only\n\n"
                "def test_member():\n"
                "    create_user()\n",
                encoding="utf-8",
            )
            mixed = synthetic_dir / "test_mixed.py"
            mixed.write_text(
                "import pytest\n"
                "from playwright_tests.conftest import auth_context, create_user\n\n"
                "def test_anonymous_read_only():\n"
                "    return None\n\n"
                "@pytest.mark.local_only\n"
                "def test_authenticated():\n"
                "    auth_context()\n\n"
                "@pytest.mark.creates_data\n"
                "def test_writer():\n"
                "    create_user()\n",
                encoding="utf-8",
            )

            diagnostics = _local_state_helper_offenders([module_owned, mixed])
            eligible_names = [
                path.name for path in _dev_eligible_files(synthetic_dir)
            ]

        self.assertEqual(diagnostics, [])
        self.assertEqual(eligible_names, ["test_mixed.py"])

    def test_conditional_dev_smoke_fixture_remains_remote_eligible(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="dev-smoke-", dir=SCRATCH) as root:
            synthetic_dir = Path(root)
            smoke = synthetic_dir / "test_dev_smoke_example.py"
            smoke.write_text(
                "import pytest\n"
                "from playwright_tests.conftest import (\n"
                "    base_url_is_local, ensure_tiers,\n"
                ")\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def _seed_locally_only():\n"
                "    if not base_url_is_local():\n"
                "        return\n"
                "    ensure_tiers()\n\n"
                "def test_anonymous_read_only():\n"
                "    return None\n",
                encoding="utf-8",
            )

            diagnostics = _local_state_helper_offenders([smoke])
            eligible_names = [
                path.name for path in _dev_eligible_files(synthetic_dir)
            ]

        self.assertEqual(diagnostics, [])
        self.assertEqual(eligible_names, ["test_dev_smoke_example.py"])
