from unittest.mock import patch

from django.db.backends.base.base import BaseDatabaseWrapper
from django.test import SimpleTestCase

from website.settings import (
    _configure_aisl_database_name,
    _configure_postgresql_connection_safety,
)


class DatabaseConnectionPolicyTests(SimpleTestCase):
    def test_postgres_options_merge_with_existing_statement_timeout(self):
        database = {
            'ENGINE': 'django.db.backends.postgresql',
            'OPTIONS': {'options': '-c statement_timeout=10000'},
        }

        configured = _configure_postgresql_connection_safety(database)

        self.assertIs(configured, database)
        self.assertTrue(configured['CONN_HEALTH_CHECKS'])
        self.assertEqual(configured['OPTIONS']['connect_timeout'], 3)
        self.assertEqual(configured['OPTIONS']['keepalives'], 1)
        self.assertEqual(configured['OPTIONS']['keepalives_idle'], 30)
        self.assertEqual(
            configured['OPTIONS']['options'],
            '-c statement_timeout=10000',
        )

    def test_non_postgres_database_is_not_given_postgres_options(self):
        database = {
            'ENGINE': 'django.db.backends.sqlite3',
            'OPTIONS': {'timeout': 30},
        }
        original = database.copy()

        configured = _configure_postgresql_connection_safety(database)

        self.assertIs(configured, database)
        self.assertEqual(configured, original)

    def test_isolated_dev_runtime_targets_aisl_dev(self):
        database = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'aisl',
        }

        configured = _configure_aisl_database_name(
            database, {'AISL_DATABASE_NAME': 'aisl_dev'},
        )

        self.assertIs(configured, database)
        self.assertEqual(database['NAME'], 'aisl_dev')

    def test_isolated_dev_runtime_rejects_other_databases_and_sqlite(self):
        from django.core.exceptions import ImproperlyConfigured

        cases = (
            ({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'aisl'}, 'aisl'),
            ({'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}, 'aisl_dev'),
        )
        for database, name in cases:
            with self.subTest(engine=database['ENGINE'], name=name):
                with self.assertRaises(ImproperlyConfigured):
                    _configure_aisl_database_name(
                        database, {'AISL_DATABASE_NAME': name},
                    )

    def test_ping_does_not_open_a_database_connection(self):
        with patch.object(
            BaseDatabaseWrapper,
            'ensure_connection',
            side_effect=AssertionError('health endpoint touched the database'),
        ):
            response = self.client.get('/ping')

        self.assertEqual(response.status_code, 200)
