"""
Playwright E2E test configuration.

Provides fixtures to start the Django dev server in a background thread
for Playwright tests to run against.
"""

import threading
import time
from pathlib import Path

import pytest
from django.core.management import call_command
from playwright.sync_api import sync_playwright

from website.test_database_guard import assert_playwright_database_is_safe

DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8765
DJANGO_BASE_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"


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

    # Enable Stripe Checkout so that upgrade/downgrade/cancel buttons and
    # the JS-based checkout flow are rendered in templates.  The setting
    # defaults to False (payment-links mode) but E2E tests for the account
    # page and pricing page expect the full checkout UI.
    settings.STRIPE_CHECKOUT_ENABLED = True

    assert_playwright_database_is_safe(connection.settings_dict)

    # Run migrations first (uses in-memory or file-based sqlite)
    call_command("migrate", "--run-syncdb", verbosity=0)

    # Start the server in a daemon thread
    original_argv = sys.argv
    sys.argv = [
        "manage.py",
        "runserver",
        f"{DJANGO_HOST}:{DJANGO_PORT}",
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

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{DJANGO_BASE_URL}/", timeout=2)
            return thread
        except (urllib.error.URLError, ConnectionError, OSError):
            # Intentional: server-startup probe, not a test wait. There is
            # no Playwright `page` here (the server is starting up); we
            # poll the listening socket via urllib instead. See issue #290.
            time.sleep(0.5)  # noqa: PLR0915
    raise RuntimeError("Django dev server did not start in time")


@pytest.fixture(scope="session")
def django_server(django_db_setup, django_db_blocker):
    """Start the Django dev server for the test session."""
    with django_db_blocker.unblock():
        _start_django_server()
        yield DJANGO_BASE_URL


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
