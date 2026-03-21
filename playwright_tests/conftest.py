"""
Playwright E2E test configuration.

Provides fixtures to start the Django dev server in a background thread
for Playwright tests to run against.
"""

import threading
import time

import pytest
from django.core.management import call_command
from playwright.sync_api import sync_playwright


DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8765
DJANGO_BASE_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"


def _start_django_server():
    """Start Django dev server in a thread."""
    from django.conf import settings
    from django.core.management import execute_from_command_line
    import sys

    # Disable Slack API calls for E2E tests so no real messages are posted.
    # post_slack_announcement() exits early when token/channel are empty (line 102),
    # and SlackCommunityService reads SLACK_BOT_TOKEN from settings in __init__.
    settings.SLACK_BOT_TOKEN = ''
    settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID = ''
    settings.SLACK_COMMUNITY_CHANNEL_IDS = []

    # Run migrations first (uses in-memory or file-based sqlite)
    call_command("migrate", "--run-syncdb", verbosity=0)

    # Load content from markdown files so pages have real data
    call_command("load_content")

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
    import urllib.request
    import urllib.error

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{DJANGO_BASE_URL}/", timeout=2)
            return thread
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    raise RuntimeError("Django dev server did not start in time")


@pytest.fixture(scope="session")
def django_server(django_db_setup, django_db_blocker):
    """Start the Django dev server for the test session."""
    with django_db_blocker.unblock():
        thread = _start_django_server()
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
    """Ensure membership tiers exist in the database."""
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
    return user


def create_staff_user(email="admin@test.com", password=DEFAULT_PASSWORD):
    """Create a staff/superuser for admin and studio tests."""
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
    return user


def create_session_for_user(email):
    """Create a Django session for the given user and return the session key."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import (
        SESSION_KEY,
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


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
