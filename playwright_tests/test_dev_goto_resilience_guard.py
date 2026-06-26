"""Guard against reintroducing the bare-``page.goto`` anti-pattern in dev tests.

The scheduled dev workflow (``scheduled-playwright-dev.yml``) runs the Playwright
suite against ``https://dev.aishippinglabs.com`` on a 3-hour cron with the marker
filter::

    not manual_visual and not slow_platform
    and not visual_regression and not local_only and not creates_data

Because ``PLAYWRIGHT_BASE_URL`` is then a non-local host,
``conftest.pytest_collection_modifyitems`` also auto-skips anything marked
``local_only`` / ``creates_data``. The net effect: only the module-level
dev-eligible files actually run against dev.

Against live dev on a cold/contended 4-shard runner -- especially mid
rolling-deploy -- a bare ``page.goto(...)`` (no bounded 5xx retry) followed by an
immediate ``.evaluate`` (no attach-wait) can race element attachment or hit a
transient 5xx, producing red scheduled runs that are not real regressions and
auto-file ``[CI]`` noise (Issue #1083, then the #1084 sweep). The shared helpers
``goto_with_retry`` (#928) and ``SETTLE_TIMEOUT_MS`` (#903) fix this.

This pure-Python ``core`` meta-test (precedent: ``test_conftest_port_resolution``,
``test_legacy_url_guard_595``) computes the dev-eligible file set the same way the
dev workflow does and fails if any of those files use a bare ``page.goto(``
without also routing navigation through ``goto_with_retry``. It runs on every push
via ``make test-playwright-core`` so a regression fails fast in PR CI rather than
3 hours later in the scheduled run.

The check is intentionally coarse (presence of ``goto_with_retry`` in a file that
contains ``page.goto(``), not full data-flow analysis -- its job is to catch the
obvious regression and stay maintainable, not to be a perfect linter.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

PLAYWRIGHT_DIR = Path(__file__).resolve().parent

# Inline opt-out: a ``page.goto(`` on a line ending with this marker is treated
# as a deliberate, reviewed exception (e.g. a navigation that genuinely cannot
# use the wrapper). Keep this for documented edge cases only.
INLINE_OPT_OUT = "# dev-goto-ok"

# Detects a module-level ``local_only`` marker, covering both the single-marker
# form ``pytestmark = pytest.mark.local_only`` and the list form
# ``pytestmark = [pytest.mark.local_only, ...]`` (possibly spanning lines).
_PYTESTMARK_BLOCK_RE = re.compile(
    r"^pytestmark\s*=\s*(.+?)(?=^\S|\Z)", re.MULTILINE | re.DOTALL
)
_BARE_GOTO_RE = re.compile(r"\bpage\.goto\s*\(")


def _module_level_marks(source):
    """Return the raw text of the module-level ``pytestmark`` assignment(s)."""
    return "\n".join(m.group(1) for m in _PYTESTMARK_BLOCK_RE.finditer(source))


def _is_module_level_local_only(source):
    """True when the file marks its whole module ``local_only`` / ``creates_data``.

    Such files never run against dev (auto-skipped by
    ``pytest_collection_modifyitems`` on a non-local base URL), so they are out
    of scope for this guard.
    """
    marks = _module_level_marks(source)
    return "local_only" in marks or "creates_data" in marks


def _dev_eligible_files():
    """Source files in ``playwright_tests/`` that run against the deployed dev env.

    Mirrors the dev workflow: a file is dev-eligible unless its module-level
    markers exclude it (``local_only`` / ``creates_data``). Per-test markers are
    intentionally NOT consulted here -- a file with at least one dev-eligible test
    still reaches dev and must keep its navigations resilient.
    """
    eligible = []
    for path in sorted(PLAYWRIGHT_DIR.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if _is_module_level_local_only(source):
            continue
        eligible.append(path)
    return eligible


def test_dev_eligible_universe_is_discoverable():
    """Sanity check: the dev-eligible set is non-empty and includes the file we
    just hardened (so a refactor that accidentally empties the set is caught)."""
    names = {p.name for p in _dev_eligible_files()}
    assert names, "Expected at least one dev-eligible Playwright file"
    assert "test_testimonials_layout.py" in names, (
        "test_testimonials_layout.py should be dev-eligible (it is not "
        "module-level local_only); the guard must cover it."
    )


def test_dev_eligible_files_route_navigation_through_goto_with_retry():
    """Every dev-eligible file that navigates must use ``goto_with_retry``.

    A dev-eligible file that contains a bare ``page.goto(`` (not annotated with
    the ``# dev-goto-ok`` opt-out) must also reference ``goto_with_retry`` so its
    live-dev navigations get the bounded 5xx retry. This is the regression the
    #1084 sweep guards against.
    """
    offenders = []
    for path in _dev_eligible_files():
        source = path.read_text(encoding="utf-8")
        bare_goto_lines = [
            (i, line)
            for i, line in enumerate(source.splitlines(), start=1)
            if _BARE_GOTO_RE.search(line) and INLINE_OPT_OUT not in line
        ]
        if not bare_goto_lines:
            continue
        if "goto_with_retry" not in source:
            offenders.append(
                f"{path.name}: has bare page.goto( at lines "
                f"{[ln for ln, _ in bare_goto_lines]} but never references "
                f"goto_with_retry. Route dev-eligible navigations through "
                f"goto_with_retry (or annotate a reviewed exception with "
                f"'{INLINE_OPT_OUT}')."
            )

    assert not offenders, (
        "Dev-eligible Playwright files must route navigation through "
        "goto_with_retry to survive rolling dev deploys (Issue #1084):\n"
        + "\n".join(offenders)
    )
