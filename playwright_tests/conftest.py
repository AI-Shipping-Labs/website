"""
Playwright E2E test configuration.

Provides fixtures to start the Django dev server in a background thread
for Playwright tests to run against.
"""

import threading
import time

import pytest
from django.core.management import call_command


DJANGO_HOST = "127.0.0.1"
DJANGO_PORT = 8765
DJANGO_BASE_URL = f"http://{DJANGO_HOST}:{DJANGO_PORT}"


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
