"""
Playwright E2E test configuration.

Provides fixtures to start the Django dev server in a background thread
for Playwright tests to run against.

The fixtures honor ``PLAYWRIGHT_BASE_URL``. When that env var points at a
remote host (e.g. ``https://dev.aishippinglabs.com``), the in-process
``runserver`` thread is NOT started and tests marked ``local_only`` or
``creates_data`` are skipped automatically. The ``django_db`` marker is
NOT auto-skipped — anonymous tests carrying it without actually issuing
queries still run against dev. Tests that genuinely need the local DB
must tag themselves ``local_only``. See
``.github/workflows/scheduled-playwright-dev.yml`` and
``_docs/testing-guidelines.md`` ("Marker taxonomy") for the dev-suite
policy.

Local-server port: when no remote ``PLAYWRIGHT_BASE_URL`` is configured the
in-process ``runserver`` binds a port resolved once per session by
``_resolved_local_port()``. If ``PLAYWRIGHT_DJANGO_PORT`` is set to a valid
positive port that exact port is used; otherwise (unset, empty, ``0``,
negative, out of range, or non-integer) the OS assigns a free ephemeral port
(``_pick_free_port()``). The same resolved port is used by ``runserver``, the
startup probe, and the base URL the browser navigates to — they are equal by
construction. This lets several worktrees run Playwright concurrently without
colliding on a single fixed port. See ``_docs/testing-guidelines.md``
("Running Playwright in isolation / parallel across worktrees").

Local same-worktree guard: before local Playwright sessions can migrate the
SQLite test database, start ``runserver``, seed fixtures, or launch Chromium,
pytest claims a repo-local ``.tmp/playwright-session.lock``. A second local
session in the same git worktree fails fast with holder details. Non-local
``PLAYWRIGHT_BASE_URL`` runs do not claim this guard because they do not touch
the local SQLite test database.

pytest-xdist parallelism (issue #1470): ``make test-playwright[-core]`` runs
``pytest -n <workers> --dist loadfile``. Each xdist worker is a full pytest
session in its own subprocess, so it independently resolves its own free
``runserver`` port (above) AND its own SQLite test database file
(``test_playwright_db_gw0.sqlite3``, see ``playwright_test_database_name``).
Only the controller process claims the worktree guard; workers detect
``PYTEST_XDIST_WORKER`` and skip claiming, so sibling workers of one invocation
run together while a genuinely separate second invocation is still blocked.
"""

import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.core.management import call_command
from playwright.sync_api import sync_playwright

from playwright_tests.worktree_guard import (
    PlaywrightWorktreeGuard,
    WorktreeGuardAlreadyHeld,
    current_xdist_worker_id,
)
from website.test_database_guard import assert_playwright_database_is_safe

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

DJANGO_HOST = "127.0.0.1"

# Stem of the per-worktree SQLite test database file. Must keep "test" in the
# name: ``website.test_database_guard`` refuses to start the in-process
# runserver against anything that does not look test-owned.
PLAYWRIGHT_TEST_DB_STEM = "test_playwright_db"

# Sentinel for "read the worker id from the ambient environment". Distinct from
# ``None``, which explicitly means "serial run, no worker suffix" — the naming
# rule has to be assertable for both cases from inside a test that is itself
# running in an xdist worker.
_AMBIENT_WORKER = object()

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}

# Cached, session-scoped local-server port. Resolved lazily on the
# local-server path only (never allocated when running against a remote
# ``PLAYWRIGHT_BASE_URL``). ``runserver``, the startup probe, and the yielded
# base URL all read this same value so they are guaranteed identical.
_LOCAL_PORT = None


def _pick_free_port():
    """Ask the OS for a free TCP port on ``DJANGO_HOST``.

    Binds a socket to port 0, reads the kernel-assigned port from
    ``getsockname()``, closes the socket, and returns the port. There is a
    small TOCTOU window between closing this probe socket and ``runserver``
    binding the same port, but two concurrent worktrees landing on the same
    ephemeral port in that window is vanishingly unlikely — far safer than a
    deterministic worktree-path hash, which can collide outright.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((DJANGO_HOST, 0))
        return probe.getsockname()[1]


def _parse_port_override(raw):
    """Parse ``PLAYWRIGHT_DJANGO_PORT`` into a valid explicit port or ``None``.

    Returns the port only when ``raw`` is a positive integer (1-65535). Treats
    empty / whitespace-only, non-positive (``0`` or negative), out-of-range, and
    non-integer ("garbage") values as "not set" by returning ``None``, so the
    caller falls back to ``_pick_free_port()``. This guards against ``=0`` being
    bound literally as port 0, which makes ``runserver`` listen on an OS-picked
    port that the probe never finds — every test then errors with "Django dev
    server did not start in time" (issue #911).
    """
    try:
        port = int((raw or "").strip())
    except (TypeError, ValueError):
        return None
    if 0 < port <= 65535:
        return port
    return None


def _resolved_local_port():
    """Return the in-process Django server port, resolved once per session.

    Uses ``PLAYWRIGHT_DJANGO_PORT`` verbatim when it is a valid positive port
    (preserves pinned/CI usage and lets a developer force a known port).
    Otherwise — unset, empty, ``0``, negative, out of range, or non-integer —
    asks the OS for a free ephemeral port. The value is memoized so
    ``runserver``, the startup probe, and the browser base URL all use the
    identical port.
    """
    global _LOCAL_PORT
    if _LOCAL_PORT is None:
        override = _parse_port_override(os.environ.get("PLAYWRIGHT_DJANGO_PORT"))
        _LOCAL_PORT = override if override is not None else _pick_free_port()
    return _LOCAL_PORT


def _local_base_url():
    """Return the in-process Django dev server URL for the resolved port."""
    return f"http://{DJANGO_HOST}:{_resolved_local_port()}"


def _resolved_base_url():
    """Return the effective Playwright base URL.

    If ``PLAYWRIGHT_BASE_URL`` is set, use it verbatim. Otherwise fall back to
    the in-process Django dev server URL on the session-resolved local port
    (``http://127.0.0.1:<dynamic-port>``).
    """
    return os.environ.get("PLAYWRIGHT_BASE_URL", "").strip() or _local_base_url()


def _base_url_is_local(url):
    """Return True when the configured base URL points at a local host.

    Local hosts always use the in-process ``runserver`` thread + the SQLite
    test DB. Non-local hosts (dev / prod) must NOT start a local server and
    must skip tests that depend on local DB fixtures.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return True
    return host in _LOCAL_HOSTS


def base_url_is_local():
    """Public helper: True when running against a local Django runserver."""
    return _base_url_is_local(_resolved_base_url())


def requested_xdist_worker_count(config):
    """Return the ``-n`` worker count requested on the command line, or 0.

    ``-n auto`` / ``-n logical`` are resolved by xdist into an int before we
    see them; anything unparseable is reported as 0 (no parallelism claimed).
    """
    raw = getattr(config.option, "numprocesses", None)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _assert_pinned_port_is_not_parallel(config):
    """Reject ``PLAYWRIGHT_DJANGO_PORT`` combined with ``-n`` (issue #1470).

    A pinned port is a single port. Every xdist worker starts its own
    ``runserver``, so under ``-n N`` all N workers would try to bind that one
    port: the first wins and the rest die with "Django dev server did not start
    in time" — N-1 workers' worth of confusing, unrelated-looking reds. Fail
    fast at configure time with an actionable message instead.
    """
    if getattr(getattr(config, "option", None), "collectonly", False):
        return
    if current_xdist_worker_id() is not None:
        return
    if not _base_url_is_local(_resolved_base_url()):
        return
    if _parse_port_override(os.environ.get("PLAYWRIGHT_DJANGO_PORT")) is None:
        return
    workers = requested_xdist_worker_count(config)
    if workers <= 0:
        return
    raise pytest.UsageError(
        "PLAYWRIGHT_DJANGO_PORT cannot be combined with pytest-xdist "
        f"parallelism (-n {workers}): a pinned port can only be bound by one "
        "Django server, but each xdist worker starts its own.\n"
        "Either drop PLAYWRIGHT_DJANGO_PORT and let each worker take a free "
        "OS-assigned port, or run serially with PLAYWRIGHT_XDIST_WORKERS=0 "
        "(equivalently -n 0)."
    )


def _claim_playwright_worktree_guard(config):
    """Claim the local worktree guard for this pytest session when needed."""
    if getattr(getattr(config, "option", None), "collectonly", False):
        return None
    if not _base_url_is_local(_resolved_base_url()):
        return None

    if current_xdist_worker_id() is not None:
        # pytest-xdist worker (issue #1470). The controller process of THIS
        # invocation already holds the worktree lock; its workers are siblings
        # of that single claim, not competing sessions, and each one owns a
        # private SQLite database + runserver port. Claiming again would
        # deadlock the run against its own controller. A genuinely separate
        # second invocation is still blocked, because its controller — which
        # never has PYTEST_XDIST_WORKER set — hits the held lock and exits
        # before it can spawn any workers.
        return None

    guard = PlaywrightWorktreeGuard.for_current_worktree()
    try:
        guard.acquire()
    except WorktreeGuardAlreadyHeld as exc:
        pytest.exit(str(exc), returncode=2)

    config._playwright_worktree_guard = guard
    return guard


def _release_playwright_worktree_guard(config):
    guard = getattr(config, "_playwright_worktree_guard", None)
    if guard is None:
        return
    guard.release()
    delattr(config, "_playwright_worktree_guard")


def pytest_configure(config):
    _assert_pinned_port_is_not_parallel(config)


def pytest_sessionstart(session):
    _claim_playwright_worktree_guard(session.config)


def pytest_sessionfinish(session, exitstatus):
    _release_playwright_worktree_guard(session.config)


@pytest.fixture(autouse=True)
def _reset_integration_config_cache():
    """Drop the process-wide IntegrationSetting cache around every test (#1470).

    ``integrations.config`` memoizes every ``IntegrationSetting`` row in a
    module-level ``_cache``, and DB overrides win over ``settings`` and env in
    ``get_config()``. Nothing invalidates that cache when a
    ``django_db(transaction=True)`` test truncates the table at teardown, and
    the cross-process stamp lives in the ``django_q`` DatabaseCache table,
    which the same flush also truncates — so a value seeded by one module
    survives, invisibly, into every later test in the same process.

    Serially the poison usually gets healed by chance (some later module calls
    ``clear_config_cache()``, which republishes a stamp). Under
    ``--dist loadfile`` the file order changes per worker, so whether a module
    runs before or after its accidental antidote becomes a coin flip. That is
    what made ``test_workshop_copy_file.py`` (a stale
    ``AWS_S3_CONTENT_BUCKET`` sending the sync at real S3 with blank
    credentials) and ``test_plan_sprints_ingest_889.py`` (a stale Slack
    channel/environment value making the ingest no-op and return ``None``)
    order-dependent.

    Resetting here is in-memory only — no DB write, no stamp — so it is safe
    for tests with no database access: the next ``get_config()`` simply
    repopulates from whatever the database currently holds. That the 33
    Playwright modules already hand-rolling ``clear_config_cache()`` in their
    own fixtures exist at all is the evidence this belongs in one place.
    """
    from integrations.config import reset_local_config_cache

    reset_local_config_cache()
    yield
    reset_local_config_cache()


def pytest_terminal_summary(terminalreporter):
    """Record the shared browser's final resource state when it was used."""
    final_resources = getattr(
        terminalreporter.config,
        "_playwright_final_browser_resources",
        None,
    )
    if final_resources is None:
        return
    contexts, pages = final_resources
    terminalreporter.write_line(
        f"Playwright session browser final state: {contexts} contexts / {pages} pages"
    )


def pytest_collection_modifyitems(config, items):
    """Skip local-only / creates_data tests when running against a deployed env.

    When ``PLAYWRIGHT_BASE_URL`` points at a non-local host the in-process
    Django server is not started and the SQLite test database does not exist.
    Tests explicitly marked ``local_only`` or ``creates_data`` are skipped so
    the dev suite only runs the anonymous, read-only subset. Tests that carry
    the pytest-django ``django_db`` marker are NOT auto-skipped here: many
    Playwright tests use ``django_db`` to allow stray ORM reads in helpers
    that never actually touch the local DB on a dev-targeted run. Each such
    file is responsible for tagging itself ``local_only`` when it genuinely
    needs the local DB. Local runs (default ``PLAYWRIGHT_BASE_URL`` unset,
    or set to a 127.0.0.1 / localhost URL) are unaffected.
    """
    if config.option.collectonly:
        return

    base_url = _resolved_base_url()
    if _base_url_is_local(base_url):
        return

    skip_local = pytest.mark.skip(
        reason=(
            f"Skipped: requires local Django runserver "
            f"(PLAYWRIGHT_BASE_URL={base_url!r} is non-local)."
        )
    )
    for item in items:
        if item.get_closest_marker("local_only") or item.get_closest_marker("creates_data"):
            item.add_marker(skip_local)


def playwright_test_database_name(base_dir, worker_id=_AMBIENT_WORKER):
    """Return the SQLite test database path for this pytest process.

    Serial runs (and the xdist controller) get ``test_playwright_db.sqlite3``,
    unchanged from before #1470, so the existing per-worktree isolation from
    #885 is untouched: each git worktree still owns exactly one such file under
    its own ``BASE_DIR``.

    Under ``pytest -n N`` every worker additionally gets its own suffixed file
    (``test_playwright_db_gw0.sqlite3``, ``test_playwright_db_gw1.sqlite3``, …).
    Workers run concurrently in one worktree, so without the suffix they would
    all migrate, seed, and truncate the same SQLite file and corrupt or lock
    each other's fixture state. The suffix is the standard pytest-django +
    pytest-xdist isolation pattern.

    ``worker_id`` defaults to the ambient ``current_xdist_worker_id()``; pass it
    explicitly (``None`` for a serial run, ``"gw0"`` for a worker) so the naming
    rule is assertable without spawning real workers.
    """
    if worker_id is _AMBIENT_WORKER:
        worker_id = current_xdist_worker_id()
    suffix = f"_{worker_id}" if worker_id else ""
    return str(Path(base_dir) / f"{PLAYWRIGHT_TEST_DB_STEM}{suffix}.sqlite3")


def apply_playwright_test_database(database_settings, base_dir, worker_id=_AMBIENT_WORKER):
    """Point ``database_settings`` at this process's Playwright test database.

    No-op for non-SQLite engines. For SQLite the Playwright name is
    authoritative and overwrites any earlier ``TEST['NAME']`` (including the
    ``DJANGO_TEST_DB_NAME`` value applied in ``website/settings.py``) — that is
    the pre-#1470 behavior, preserved verbatim so the Playwright suite can
    never be pointed at the Django unittest runner's database.
    """
    if database_settings.get('ENGINE') != 'django.db.backends.sqlite3':
        return database_settings
    database_settings.setdefault('TEST', {})['NAME'] = playwright_test_database_name(
        base_dir, worker_id=worker_id
    )
    return database_settings


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Force Playwright pytest runs onto a dedicated test database file."""
    from django.conf import settings

    apply_playwright_test_database(settings.DATABASES['default'], settings.BASE_DIR)


def _start_django_server():
    """Start Django dev server in a thread."""
    import sys

    from django.conf import settings
    from django.core.management import execute_from_command_line
    from django.db import connection

    # Disable Slack API calls for E2E tests so no real messages are posted.
    # post_slack_announcement() exits early when token/channel are empty (line 102),
    # and SlackCommunityService reads SLACK_BOT_TOKEN from settings in __init__.
    settings.SLACK_BOT_TOKEN = ''
    settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID = ''
    settings.SLACK_COMMUNITY_CHANNEL_IDS = []

    # Disable Amazon SES for E2E tests so no real emails are sent (issue #509).
    # EmailService._send_ses and events.services.registration_email._send_raw_email
    # both check SES_ENABLED and return a synthetic noop message id when disabled.
    # Belt-and-suspenders: blanking the AWS credentials means any future code path
    # that slips past the gate would still fail with InvalidClientTokenId rather
    # than reach a real account.
    settings.SES_ENABLED = False
    settings.AWS_ACCESS_KEY_ID = ''
    settings.AWS_SECRET_ACCESS_KEY = ''

    # Silence the SES-disabled-in-prod system check (email_app.E001) during
    # Playwright runs — we deliberately disable SES here for E2E (see above),
    # and pytest-django defaults DEBUG=False, so without this the runserver
    # thread would raise SystemCheckError at startup and kill every E2E test.
    settings.SILENCED_SYSTEM_CHECKS = ['email_app.E001']

    assert_playwright_database_is_safe(connection.settings_dict)

    # Run migrations first (uses in-memory or file-based sqlite)
    call_command("migrate", "--run-syncdb", verbosity=0)

    # Start the server in a daemon thread on the session-resolved port.
    port = _resolved_local_port()
    original_argv = sys.argv
    sys.argv = [
        "manage.py",
        "runserver",
        f"{DJANGO_HOST}:{port}",
        "--noreload",
        "--insecure",
    ]
    thread = threading.Thread(
        target=execute_from_command_line,
        args=(sys.argv,),
        daemon=True,
    )
    sys.argv = original_argv
    thread.start()

    # Wait for server to be ready
    import urllib.error
    import urllib.request

    base_url = _local_base_url()
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{base_url}/", timeout=2)
            return thread
        except (urllib.error.URLError, ConnectionError, OSError):
            # Intentional: server-startup probe, not a test wait. There is
            # no Playwright `page` here (the server is starting up); we
            # poll the listening socket via urllib instead. See issue #290.
            time.sleep(0.5)  # noqa: PLR0915
    raise RuntimeError("Django dev server did not start in time")


@pytest.fixture(scope="session")
def django_server(request):
    """Provide the base URL for Playwright tests.

    When ``PLAYWRIGHT_BASE_URL`` is unset (or points at a local host) this
    starts the in-process Django dev server using pytest-django's test
    database and yields ``http://127.0.0.1:<port>``, where the port is resolved
    once per session (``PLAYWRIGHT_DJANGO_PORT`` if set, else an OS-assigned
    free port). When ``PLAYWRIGHT_BASE_URL`` points at a remote host
    (dev / prod), no local server is started, no port is allocated, and the
    configured URL is yielded as-is — local-only and ``django_db`` tests have
    already been skipped by ``pytest_collection_modifyitems``.
    """
    base_url = _resolved_base_url()
    if not _base_url_is_local(base_url):
        yield base_url.rstrip("/")
        return

    # Local path: run the in-process Django server, using pytest-django's
    # test DB. We request the django_db_setup + django_db_blocker fixtures
    # lazily so the dev-suite run (which has no test DB) is never forced to
    # build one.
    request.getfixturevalue("django_db_setup")
    django_db_blocker = request.getfixturevalue("django_db_blocker")
    with django_db_blocker.unblock():
        _start_django_server()
        yield _local_base_url()


# ---------------------------------------------------------------------------
# Session-scoped browser fixture (Step 6b: reuse browser across all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser(request):
    """Launch a single Chromium instance for the entire test session.

    This avoids the ~1-2s overhead of launching a new browser per test.
    Each test gets a fresh browser context via the ``page`` fixture.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        final_contexts, final_pages = _browser_resource_counts(browser)
        request.config._playwright_final_browser_resources = (
            final_contexts,
            final_pages,
        )
        browser.close()


def _browser_resource_snapshot(browser):
    """Return active contexts plus exact context/page counts."""
    contexts = list(browser.contexts)
    return contexts, len(contexts), sum(len(context.pages) for context in contexts)


def _browser_resource_counts(browser):
    """Return exact active context/page counts for the shared browser."""
    _contexts, context_count, page_count = _browser_resource_snapshot(browser)
    return context_count, page_count


def _close_browser_contexts(browser):
    """Close every context discovered on the shared browser.

    Contexts are closed in the stable order returned by Playwright. A failed
    close never prevents the remaining contexts from being attempted.
    """
    contexts, context_count, page_count = _browser_resource_snapshot(browser)
    close_errors = []
    for index, context in enumerate(contexts, start=1):
        try:
            context.close()
        except Exception as exc:  # noqa: BLE001 - aggregate every teardown failure
            close_errors.append(
                f"context {index}: {type(exc).__name__}: {exc}"
            )

    _remaining, remaining_contexts, remaining_pages = _browser_resource_snapshot(
        browser
    )
    return {
        "before_contexts": context_count,
        "before_pages": page_count,
        "after_contexts": remaining_contexts,
        "after_pages": remaining_pages,
        "close_errors": close_errors,
    }


def _browser_lifecycle_error(nodeid, phase, cleanup):
    """Build one actionable error containing all browser cleanup evidence."""
    close_errors = cleanup["close_errors"]
    lines = [
        "Playwright per-node browser lifecycle violation.",
        f"Node: {nodeid}",
        f"Phase: {phase}",
        (
            "Before cleanup: "
            f"{cleanup['before_contexts']} contexts / "
            f"{cleanup['before_pages']} pages"
        ),
        (
            "After cleanup: "
            f"{cleanup['after_contexts']} contexts / "
            f"{cleanup['after_pages']} pages"
        ),
        f"Context close errors ({len(close_errors)}):",
    ]
    lines.extend(f"- {error}" for error in close_errors)
    if not close_errors:
        lines.append("- none")
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _browser_node_lifecycle(request):
    """Own all shared-browser resources for exactly one test node.

    The fixture stays dormant unless ``browser`` is already in the node's
    declared fixture closure, so static guards do not launch Chromium. As an
    autouse fixture it initializes before ordinary function fixtures and
    therefore finalizes after them: the normal ``page`` teardown runs first,
    then this boundary closes any contexts created directly by the test.
    """
    if "browser" not in request.fixturenames:
        yield
        return

    browser = request.getfixturevalue("browser")
    inherited_contexts, inherited_pages = _browser_resource_counts(browser)
    if (inherited_contexts, inherited_pages) != (0, 0):
        cleanup = _close_browser_contexts(browser)
        raise AssertionError(
            _browser_lifecycle_error(
                request.node.nodeid,
                "precondition: inherited resources from an earlier node",
                cleanup,
            )
        )

    yield

    cleanup = _close_browser_contexts(browser)
    if cleanup["close_errors"] or (
        cleanup["after_contexts"], cleanup["after_pages"]
    ) != (0, 0):
        raise AssertionError(
            _browser_lifecycle_error(
                request.node.nodeid,
                "teardown",
                cleanup,
            )
        )


@pytest.fixture
def page(browser):
    """Provide a fresh browser page in its own context for each test.

    The context is created with a standard viewport and closed after
    the test finishes, ensuring full isolation between tests without
    re-launching the browser.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    yield page
    context.close()


# ---------------------------------------------------------------------------
# Shared E2E helpers
# ---------------------------------------------------------------------------

# Dev-smoke bounded-retry navigation tuning (Issue #928). These are
# test-harness constants, not runtime product settings, so they are plain
# module-level defaults and do NOT go through the IntegrationSetting framework.
# Rationale: the scheduled dev suite runs against a live ECS service that can
# briefly serve a 5xx while a rolling deploy swaps tasks. A bounded retry
# absorbs that transient blip; a persistent 5xx still fails (we return the last
# response so the caller's assertion produces the normal failure message).
GOTO_RETRY_ATTEMPTS = 3
GOTO_RETRY_BACKOFF_SECONDS = 2.0


def _is_retryable_status(status):
    """Return True when a navigation result should be retried.

    Pure decision function (no Playwright coupling) so it is unit-testable
    without a browser. A result is retryable when:

    - ``status is None`` — navigation produced no response (network/nav error).
    - ``status >= 500`` — server error (500, 502, 503, 504): transient during
      an ECS rolling-deploy window.

    Every other status (2xx success, 3xx redirect, 401, 403, 404, etc.) is NOT
    retryable and must be returned to the caller immediately so it asserts on
    the real status exactly as before (the unknown-route test relies on a 404
    coming back as 404 on the first attempt).
    """
    if status is None:
        return True
    return status >= 500


def goto_with_retry(
    page,
    url,
    *,
    expected_status=200,
    wait_until="domcontentloaded",
    attempts=GOTO_RETRY_ATTEMPTS,
    backoff_seconds=GOTO_RETRY_BACKOFF_SECONDS,
):
    """Navigate to ``url`` with a bounded retry on transient server errors.

    Drop-in wrapper around ``page.goto`` for the dev-smoke tests (Issue #928).
    Behaves identically to ``page.goto`` on the happy path: a first-attempt
    non-5xx response (200, redirect, 401/403/404, ...) is returned immediately
    with zero added sleep.

    On a retryable result (``response is None`` or ``status >= 500``, decided by
    ``_is_retryable_status``) it sleeps a constant ``backoff_seconds`` and
    retries, up to ``attempts`` total attempts. If every attempt is retryable it
    returns the LAST response (it does NOT raise and does NOT fabricate a 200),
    so the caller's existing ``assert response.status == expected_status``
    produces the normal, readable failure — a persistent 5xx still fails.

    ``expected_status`` is accepted so call sites read self-documentingly (e.g.
    ``expected_status=404`` for the unknown-route test) but it does NOT change
    the retry decision: only 5xx / ``None`` are retried, never the expected
    status itself.
    """
    response = None
    for attempt in range(attempts):
        response = page.goto(url, wait_until=wait_until)
        status = response.status if response is not None else None
        if not _is_retryable_status(status):
            return response
        if attempt < attempts - 1:
            time.sleep(backoff_seconds)
    return response


VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_PASSWORD = "TestPass123!"

# Shared, load-tolerant settle budget for client-side UI waits
# (``wait_for_function`` / ``locator.click`` / ``expect(...).to_be_visible``).
#
# Rationale (issue #903): the full suite runs 4 shards in parallel on a single
# shared CI runner (``scheduled-playwright.yml``). A tight sub-3s poll that is
# correct in isolation can lose the CPU race on a contended shard — the page IS
# in the right state, but the repaint/scroll-settle lands after the wait budget
# expires, producing a spurious ``Timeout 2000ms exceeded`` red. These reds are
# never real product bugs; they rotate run-to-run to whichever test happens to
# starve. Reusing one generous-but-bounded budget for these settle waits keeps
# the happy path instant (a fast shard still resolves immediately) while giving
# a loaded shard enough headroom to settle.
#
# Do NOT re-introduce a 2000ms (or default-30s ``click``) settle poll for these
# load-sensitive waits — route them through this constant instead.
SETTLE_TIMEOUT_MS = 8000


def settle_click(locator, *, timeout=SETTLE_TIMEOUT_MS):
    """Wait for ``locator`` to be visible, then click it with a load-tolerant
    timeout.

    Thin helper for load-sensitive Studio-list / reader actions (issue #903).
    On a fast shard the visibility wait resolves instantly and the click is a
    no-op-fast; on a contended shard the explicit settle + raised click budget
    absorb the shard-contention delay instead of firing a spurious timeout.
    """
    locator.wait_for(state="visible", timeout=timeout)
    locator.click(timeout=timeout)


def expand_studio_sidebar_section(page, slug):
    """Expand a Studio sidebar section if it is currently collapsed."""
    button = page.locator(
        f'#studio-sidebar-nav [aria-controls="studio-section-{slug}"]'
    )
    if button.get_attribute("aria-expanded") != "true":
        button.click()
    page.locator(f"#studio-sidebar-nav #studio-section-{slug}").wait_for(
        state="visible",
    )


def ensure_tiers():
    """Ensure membership tiers exist in the database.

    Closes the database connection afterward to release SQLite locks
    so the server thread can access the tiers table.
    """
    from django.db import connection

    from payments.models import Tier

    TIERS = [
        {"slug": "free", "name": "Free", "level": 0},
        {"slug": "basic", "name": "Basic", "level": 10},
        {"slug": "main", "name": "Main", "level": 20},
        {"slug": "premium", "name": "Premium", "level": 30},
    ]
    for tier_data in TIERS:
        Tier.objects.get_or_create(
            slug=tier_data["slug"], defaults=tier_data
        )
    connection.close()


def ensure_site_config_tiers():
    """Seed the SiteConfig 'tiers' entry from the tiers.yaml fixture.

    This populates the homepage tier cards and activities page with
    real tier data (Basic, Main, Premium) so that E2E tests can assert
    on tier names and activity titles like 'Closed Community Access'.
    """
    from pathlib import Path

    import yaml
    from django.db import connection

    from content.models import SiteConfig

    fixture_path = Path(__file__).parent.parent / 'content' / 'tests' / 'fixtures' / 'tiers.yaml'
    with open(fixture_path) as f:
        tiers_data = yaml.safe_load(f)
    SiteConfig.objects.update_or_create(
        key='tiers', defaults={'data': tiers_data}
    )
    connection.close()


def create_user(
    email,
    tier_slug="free",
    password=DEFAULT_PASSWORD,
    email_verified=True,
    unsubscribed=False,
    is_staff=False,
    first_name="",
):
    """Create a user with the given tier and options."""
    from django.db import connection

    from accounts.models import User
    from payments.models import Tier

    ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": email_verified},
    )
    user.set_password(password)
    tier = Tier.objects.get(slug=tier_slug)
    user.tier = tier
    user.email_verified = email_verified
    user.unsubscribed = unsubscribed
    user.is_staff = is_staff
    if first_name:
        user.first_name = first_name
    user.save()
    connection.close()
    return user


def create_staff_user(email="admin@test.com", password=DEFAULT_PASSWORD):
    """Create a staff/superuser for admin and studio tests."""
    from django.db import connection

    from accounts.models import User

    ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = True
    user.email_verified = True
    user.save()
    connection.close()
    return user


def create_session_for_user(email):
    """Create a Django session for the given user and return the session key.

    Closes the database connection after creating the session to release
    any SQLite locks held by the test thread. This prevents
    ``database table is locked`` errors when the Django server thread
    (running in the same process) tries to read the session.
    """
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
    )
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import connection

    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    session_key = session.session_key
    # Close the connection to release any SQLite locks before the
    # server thread tries to access the same tables.
    connection.close()
    return session_key


def auth_context(browser, email):
    """Create an authenticated browser context for the given user."""
    session_key = create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context
