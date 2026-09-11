import pytest
from django.conf import settings
from django.db import connection

from playwright_tests import conftest
from website.test_database_guard import (
    UnsafeTestDatabaseError,
    assert_playwright_database_is_safe,
    is_database_test_scoped,
)

pytest_plugins = ["playwright_tests.conftest"]


@pytest.mark.django_db(transaction=True)
def test_playwright_database_is_pytest_scoped():
    assert is_database_test_scoped(connection.settings_dict)


@pytest.mark.django_db(transaction=True)
def test_playwright_server_starts_on_pytest_database(django_server):
    assert django_server.startswith('http://127.0.0.1:')
    assert connection.settings_dict['NAME'].endswith('test_playwright_db.sqlite3')
    assert_playwright_database_is_safe(connection.settings_dict)


def test_remote_server_path_does_not_request_local_database_or_server(
    monkeypatch,
):
    remote_url = 'https://dev.example.test/'
    monkeypatch.setenv('PLAYWRIGHT_BASE_URL', remote_url)

    class RemoteRequest:
        def getfixturevalue(self, name):
            raise AssertionError(f'remote path requested local fixture {name}')

    fixture = conftest.django_server.__wrapped__(RemoteRequest())

    assert next(fixture) == remote_url.rstrip('/')
    with pytest.raises(StopIteration):
        next(fixture)


def test_local_server_path_requests_only_the_explicitly_db_dependent_fixture(
    monkeypatch,
):
    local_url = 'http://127.0.0.1:8765'
    requested_fixtures = []
    monkeypatch.setattr(conftest, '_resolved_base_url', lambda: local_url)

    class LocalRequest:
        def getfixturevalue(self, name):
            requested_fixtures.append(name)
            if name != '_local_django_server':
                raise AssertionError(f'unexpected dynamic fixture request: {name}')
            return local_url

    fixture = conftest.django_server.__wrapped__(LocalRequest())

    assert next(fixture) == local_url
    assert requested_fixtures == ['_local_django_server']
    with pytest.raises(StopIteration):
        next(fixture)


def test_local_server_fixture_unblocks_database_before_starting_server(
    monkeypatch,
):
    local_url = 'http://127.0.0.1:8765'
    events = []

    def resolve_database_setup():
        events.append('django_db_setup')
        return object()

    class UnblockedDatabase:
        def __enter__(self):
            events.append('database_unblocked')

        def __exit__(self, exc_type, exc_value, traceback):
            events.append('database_reblocked')

    class DatabaseBlocker:
        def unblock(self):
            return UnblockedDatabase()

    class Config:
        pass

    class LocalRequest:
        config = Config()

    def start_server():
        events.append('server_started')
        return object()

    monkeypatch.setattr(conftest, '_local_base_url', lambda: local_url)
    monkeypatch.setattr(conftest, '_start_django_server', start_server)
    fixture = conftest._local_django_server.__wrapped__(
        LocalRequest(),
        resolve_database_setup(),
        DatabaseBlocker(),
    )

    assert next(fixture) == local_url
    assert events == ['django_db_setup', 'database_unblocked', 'server_started']
    with pytest.raises(StopIteration):
        next(fixture)
    assert events == [
        'django_db_setup',
        'database_unblocked',
        'server_started',
        'database_reblocked',
    ]


@pytest.mark.parametrize(
    'database_settings',
    [
        {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': settings.BASE_DIR / 'db.sqlite3',
        },
        {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': settings.BASE_DIR / 'db.sqlite3',
            'TEST': {'NAME': settings.BASE_DIR / 'test_declared_only.sqlite3'},
        },
        {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'aisl',
            'TEST': {'NAME': 'test_aisl'},
        },
    ],
)
def test_guard_rejects_an_active_non_test_database(database_settings):
    with pytest.raises(UnsafeTestDatabaseError):
        assert_playwright_database_is_safe(database_settings)
