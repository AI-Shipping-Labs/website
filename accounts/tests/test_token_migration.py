"""Migration coverage for hashing legacy operator API tokens."""

import io
from contextlib import redirect_stderr, redirect_stdout

from django.contrib.auth.hashers import check_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OperatorTokenHashMigrationTest(TransactionTestCase):
    """Existing plaintext token rows are hashed without changing clients."""

    migrate_from = [("accounts", "0019_user_dashboard_dismissals")]
    migrate_to = [("accounts", "0020_hash_operator_tokens")]

    def test_legacy_plaintext_token_is_hashed_and_still_authenticates(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()

        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldUser = old_apps.get_model("accounts", "User")
            OldToken = old_apps.get_model("accounts", "Token")

            user = OldUser.objects.create(
                email="legacy-token@test.com",
                password="!",
                is_staff=True,
            )
            plaintext_key = "legacy-plaintext-token-for-migration"
            OldToken.objects.create(
                key=plaintext_key,
                user=user,
                name="legacy",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                executor = MigrationExecutor(connection)
                executor.migrate(self.migrate_to)

            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewToken = new_apps.get_model("accounts", "Token")
            migrated = NewToken.objects.get(name="legacy")

            self.assertNotEqual(migrated.pk, plaintext_key)
            self.assertEqual(migrated.lookup_prefix, plaintext_key[:24])
            self.assertNotEqual(migrated.key_hash, plaintext_key)
            self.assertTrue(check_password(plaintext_key, migrated.key_hash))
            self.assertNotIn(plaintext_key, stdout.getvalue())
            self.assertNotIn(plaintext_key, stderr.getvalue())

            from accounts.models import Token

            authenticated = Token.authenticate(plaintext_key)
            self.assertIsNotNone(authenticated)
            self.assertEqual(authenticated.name, "legacy")
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)
