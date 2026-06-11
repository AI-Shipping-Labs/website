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
``_resolved_local_port()``. If ``PLAYWRIGHT_DJANGO_PORT`` is set and non-empty
that exact port is used; otherwise the OS assigns a free ephemeral port
(``_pick_free_port()``). The same resolved port is used by ``runserver``, the
startup probe, and the base URL the browser navigates to — they are equal by
construction. This lets several worktrees run Playwright concurrently without
colliding on a single fixed port. See ``_docs/testing-guidelines.md``
("Running Playwright in isolation / parallel across worktrees").
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

from website.test_database_guard import assert_playwright_database_is_safe

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

DJANGO_HOST = "127.0.0.1"

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


def _resolved_local_port():
    """Return the in-process Django server port, resolved once per session.

    Uses ``PLAYWRIGHT_DJANGO_PORT`` verbatim when set and non-empty (preserves
    pinned/CI usage and lets a developer force a known port), otherwise asks
    the OS for a free ephemeral port. The value is memoized so ``runserver``,
    the startup probe, and the browser base URL all use the identical port.
    """
    global _LOCAL_PORT
    if _LOCAL_PORT is None:
        override = os.environ.get("PLAYWRIGHT_DJANGO_PORT", "").strip()
        _LOCAL_PORT = int(override) if override else _pick_free_port()
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


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Force Playwright pytest runs onto a dedicated test database file."""
    from django.conf import settings

    database_settings = settings.DATABASES['default']
    if database_settings.get('ENGINE') == 'django.db.backends.sqlite3':
        database_settings.setdefault('TEST', {})['NAME'] = str(
            Path(settings.BASE_DIR) / 'test_playwright_db.sqlite3'
        )


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
def browser():
    """Launch a single Chromium instance for the entire test session.

    This avoids the ~1-2s overhead of launching a new browser per test.
    Each test gets a fresh browser context via the ``page`` fixture.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


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

VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_PASSWORD = "TestPass123!"


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
