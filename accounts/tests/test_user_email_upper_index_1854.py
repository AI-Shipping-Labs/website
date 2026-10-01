"""Expression-index contract for case-insensitive user lookup (issue #1854)."""

import importlib

from django.db import connection, migrations
from django.db.migrations.operations.special import SeparateDatabaseAndState
from django.test import TestCase

from accounts.models import User

index_migration = importlib.import_module(
    "accounts.migrations.0034_user_email_upper_index",
)
INDEX_NAME = index_migration.INDEX_NAME
EmailUpperIndexMigration = index_migration.Migration
create_email_upper_index = index_migration.create_email_upper_index


class UserEmailUpperIndexTest(TestCase):
    def test_model_and_database_declare_email_upper_index(self):
        model_index = None
        for index in User._meta.indexes:
            if index.name == INDEX_NAME:
                model_index = index
                break
        self.assertIsNotNone(model_index)
        self.assertEqual(str(model_index.expressions[0]), "Upper(F(email))")

        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                User._meta.db_table,
            )
        self.assertTrue(constraints[INDEX_NAME]["index"])

    def test_migration_uses_concurrent_non_atomic_database_operations(self):
        self.assertFalse(EmailUpperIndexMigration.atomic)
        operation = EmailUpperIndexMigration.operations[0]
        self.assertIsInstance(operation, SeparateDatabaseAndState)
        self.assertTrue(
            any(
                isinstance(item, migrations.AddIndex)
                and item.index.name == INDEX_NAME
                for item in operation.state_operations
            )
        )
        self.assertTrue(
            any(
                isinstance(value, str) and "CONCURRENTLY" in value
                for value in create_email_upper_index.__code__.co_consts
            )
        )
