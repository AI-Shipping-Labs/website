"""Database safety checks for browser-driven test servers."""

from pathlib import Path

from django.conf import settings


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when an E2E test server would write to a non-test database."""


def configured_database_label(database_settings):
    """Return a human-readable database name/path for error messages."""
    name = database_settings.get('NAME') or ''
    return str(name)


def is_database_test_scoped(database_settings, *, base_dir=None):
    """Return True when the configured DB name looks test-owned.

    Pytest-django rewrites Django's default DB to a test database before
    fixtures run. For SQLite this is usually a file whose name contains
    ``test``; for Postgres/MySQL it is normally a database named ``test_*``.
    The Playwright server runs in-process and must only start after that
    rewrite has happened.
    """
    engine = database_settings.get('ENGINE') or ''
    name = str(database_settings.get('NAME') or '')

    if not name:
        return False

    if engine == 'django.db.backends.sqlite3':
        if name == ':memory:':
            return True
        db_path = Path(name).expanduser()
        if not db_path.is_absolute():
            db_path = Path(base_dir or settings.BASE_DIR) / db_path
        db_path = db_path.resolve()
        dev_db = (Path(base_dir or settings.BASE_DIR) / 'db.sqlite3').resolve()
        if db_path == dev_db:
            return False
        return 'test' in db_path.name.lower()

    return 'test' in name.lower()


def assert_playwright_database_is_safe(database_settings=None, *, base_dir=None):
    """Fail fast if Playwright would write fixtures to a development DB."""
    database_settings = database_settings or settings.DATABASES['default']
    if is_database_test_scoped(database_settings, base_dir=base_dir):
        return

    label = configured_database_label(database_settings)
    raise UnsafeTestDatabaseError(
        "Unsafe Playwright database configuration: the django_server fixture "
        f"is about to run against {label!r}, which is not a pytest-managed "
        "test database. Do not start Playwright fixture helpers from a Django "
        "shell or a manually configured runserver against db.sqlite3. Run "
        "`uv run pytest playwright_tests/...` so pytest-django creates an "
        "isolated test database before the browser server starts."
    )
