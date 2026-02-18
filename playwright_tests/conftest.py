"""
Playwright visual regression test configuration.

Provides fixtures to:
- Start the Django dev server in a background thread
- Capture baseline screenshots from the live site (aishippinglabs.com)
- Compare Django site screenshots against the baselines
"""

import os
import threading
import time

import pytest
from django.core.management import call_command
from playwright.sync_api import sync_playwright


DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8765
DJANGO_BASE_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"
LIVE_BASE_URL = "https://aishippinglabs.com"

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")

# Pages to test: (name, path)
PAGES = [
    ("home", "/"),
    ("about", "/about"),
    ("activities", "/activities"),
    ("blog", "/blog"),
    ("projects", "/projects"),
    ("event_recordings", "/event-recordings"),
    ("collection", "/collection"),
    ("tutorials", "/tutorials"),
]


def _ensure_screenshot_dir():
    """Create screenshot directories if they don't exist."""
    for subdir in ("baseline", "django", "diff"):
        os.makedirs(os.path.join(SCREENSHOT_DIR, subdir), exist_ok=True)


def _start_django_server():
    """Start Django dev server in a thread."""
    from django.core.management import execute_from_command_line
    import sys

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


@pytest.fixture(scope="session")
def screenshot_dirs():
    """Ensure screenshot directories exist and return their paths."""
    _ensure_screenshot_dir()
    return {
        "baseline": os.path.join(SCREENSHOT_DIR, "baseline"),
        "django": os.path.join(SCREENSHOT_DIR, "django"),
        "diff": os.path.join(SCREENSHOT_DIR, "diff"),
    }
