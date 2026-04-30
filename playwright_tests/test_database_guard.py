import pytest
from django.db import connection

from website.test_database_guard import is_database_test_scoped


@pytest.mark.django_db(transaction=True)
def test_playwright_database_is_pytest_scoped():
    assert is_database_test_scoped(connection.settings_dict)


@pytest.mark.django_db(transaction=True)
def test_playwright_server_starts_on_pytest_database(django_server):
    assert django_server.startswith('http://127.0.0.1:')
