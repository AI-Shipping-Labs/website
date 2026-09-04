"""Index contract for EmailLog.ses_message_id (issue #1523)."""

import importlib

from django.contrib.auth import get_user_model
from django.db import connection, migrations, models
from django.db.migrations.operations.special import SeparateDatabaseAndState
from django.test import TestCase

from email_app.models import EmailLog

index_migration = importlib.import_module(
    "email_app.migrations.0024_emaillog_ses_msgid_idx",
)
INDEX_NAME = index_migration.INDEX_NAME
SesMessageIdIndexMigration = index_migration.Migration
create_ses_message_id_index = index_migration.create_ses_message_id_index

User = get_user_model()


def _index_sql(name):
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(
                """
                SELECT indexdef
                  FROM pg_indexes
                 WHERE schemaname = current_schema()
                   AND indexname = %s
                """,
                [name],
            )
        else:
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = %s",
                [name],
            )
        row = cursor.fetchone()
    if row is None or not row[0]:
        return None, None
    definition = row[0]
    return definition, " unique " in f" {definition.lower()} "


class EmailLogSesMessageIdIndexTest(TestCase):
    def test_field_stays_non_unique_blank_charfield(self):
        field = EmailLog._meta.get_field("ses_message_id")
        self.assertIsInstance(field, models.CharField)
        self.assertEqual(field.max_length, 255)
        self.assertTrue(field.blank)
        self.assertEqual(field.default, "")
        self.assertFalse(field.null)
        self.assertFalse(field.unique)

    def test_meta_declares_non_unique_partial_btree_index(self):
        index = next(
            item for item in EmailLog._meta.indexes if item.name == INDEX_NAME
        )
        self.assertEqual(list(index.fields), ["ses_message_id"])
        self.assertEqual(index.condition, models.Q(ses_message_id__gt=""))
        unique_ses_constraints = [
            constraint
            for constraint in EmailLog._meta.constraints
            if list(getattr(constraint, "fields", [])) == ["ses_message_id"]
        ]
        self.assertEqual(unique_ses_constraints, [])

    def test_schema_index_excludes_blanks_and_is_not_unique(self):
        definition, unique = _index_sql(INDEX_NAME)
        self.assertIsNotNone(definition)
        self.assertFalse(unique)
        normalized = " ".join(definition.lower().replace('"', "").split())
        self.assertIn("ses_message_id", normalized)
        self.assertTrue(
            any(
                token in normalized
                for token in (
                    "ses_message_id <> ''",
                    "ses_message_id > ''",
                    "ses_message_id != ''",
                    "not (ses_message_id = '')",
                )
            ),
            definition,
        )
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, EmailLog._meta.db_table,
            )
        info = constraints[INDEX_NAME]
        self.assertTrue(info["index"])
        self.assertFalse(info["unique"])
        self.assertEqual(info["columns"], ["ses_message_id"])

    def test_duplicate_non_empty_ses_message_id_is_allowed(self):
        user_a = User.objects.create_user(email="ses-idx-a@example.com")
        user_b = User.objects.create_user(email="ses-idx-b@example.com")
        first = EmailLog.objects.create(
            user=user_a,
            email_type="welcome",
            ses_message_id="ses-shared-1",
        )
        second = EmailLog.objects.create(
            user=user_b,
            email_type="welcome",
            ses_message_id="ses-shared-1",
        )
        self.assertEqual(
            EmailLog.objects.filter(ses_message_id="ses-shared-1").count(),
            2,
        )
        self.assertNotEqual(first.pk, second.pk)

    def test_migration_creates_index_concurrently_outside_a_transaction(self):
        self.assertFalse(SesMessageIdIndexMigration.atomic)
        self.assertEqual(len(SesMessageIdIndexMigration.operations), 1)
        operation = SesMessageIdIndexMigration.operations[0]
        self.assertIsInstance(operation, SeparateDatabaseAndState)
        self.assertTrue(
            any(
                isinstance(item, migrations.AddIndex)
                and item.index.name == INDEX_NAME
                for item in operation.state_operations
            )
        )
        consts = create_ses_message_id_index.__code__.co_consts
        self.assertTrue(
            any(isinstance(item, str) and "CONCURRENTLY" in item for item in consts)
        )
        self.assertTrue(
            any(isinstance(item, str) and "IF NOT EXISTS" in item for item in consts)
        )
