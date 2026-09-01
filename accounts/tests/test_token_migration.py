"""Behavioral coverage for account data migrations."""

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, identify_hasher
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings


class MergedSecondaryEmailMigrationTest(TransactionTestCase):
    """Migration 0017 owns its historical repair after runtime cleanup."""

    migrate_from = [("accounts", "0016_emailalias")]
    migrate_to = [("accounts", "0017_backfill_scrub_merged_secondary_emails")]

    def test_forward_is_self_contained_and_reverse_keeps_scrubbed_value(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()

        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldUser = old_apps.get_model("accounts", "User")
            OldEmailAlias = old_apps.get_model("accounts", "EmailAlias")

            canonical = OldUser.objects.create(
                email="migration-canonical@test.com",
                password="!",
            )
            legacy_secondary = OldUser.objects.create(
                email="migration-legacy@test.com",
                password="!",
                is_active=False,
            )
            active_collision = OldUser.objects.create(
                email="migration-active@test.com",
                password="!",
                is_active=True,
            )
            inactive_orphan = OldUser.objects.create(
                email="migration-orphan@test.com",
                password="!",
                is_active=False,
            )
            already_scrubbed = OldUser.objects.create(
                email="merged+999@merged.invalid",
                password="!",
                is_active=False,
            )
            OldEmailAlias.objects.create(
                user=canonical,
                email=legacy_secondary.email,
                source="merge",
            )
            OldEmailAlias.objects.create(
                user=canonical,
                email=active_collision.email,
                source="manual",
            )

            executor = MigrationExecutor(connection)
            with patch.dict(
                sys.modules,
                {"accounts.services.account_merge": None},
            ):
                executor.migrate(self.migrate_to)

            migrated_apps = executor.loader.project_state(self.migrate_to).apps
            MigratedUser = migrated_apps.get_model("accounts", "User")
            MigratedEmailAlias = migrated_apps.get_model("accounts", "EmailAlias")
            expected_scrubbed = f"merged+{legacy_secondary.pk}@merged.invalid"
            self.assertEqual(
                MigratedUser.objects.get(pk=legacy_secondary.pk).email,
                expected_scrubbed,
            )
            self.assertEqual(
                MigratedUser.objects.get(pk=active_collision.pk).email,
                "migration-active@test.com",
            )
            self.assertEqual(
                MigratedUser.objects.get(pk=inactive_orphan.pk).email,
                "migration-orphan@test.com",
            )
            self.assertEqual(
                MigratedUser.objects.get(pk=already_scrubbed.pk).email,
                "merged+999@merged.invalid",
            )
            self.assertEqual(
                MigratedEmailAlias.objects.get(
                    email="migration-legacy@test.com"
                ).user_id,
                canonical.pk,
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_from)
            reversed_apps = executor.loader.project_state(self.migrate_from).apps
            ReversedUser = reversed_apps.get_model("accounts", "User")
            self.assertEqual(
                ReversedUser.objects.get(pk=legacy_secondary.pk).email,
                expected_scrubbed,
            )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
)
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
            self.assertEqual(
                identify_hasher(migrated.key_hash).algorithm,
                "pbkdf2_sha256",
            )
            self.assertTrue(check_password(plaintext_key, migrated.key_hash))
            self.assertNotIn(plaintext_key, stdout.getvalue())
            self.assertNotIn(plaintext_key, stderr.getvalue())

            # Current ORM code must run against the current schema. The
            # historical migration state intentionally predates newer User
            # fields, so restore the graph before exercising authentication.
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)

            from accounts.models import Token

            authenticated = Token.authenticate(plaintext_key)
            self.assertIsNotNone(authenticated)
            self.assertEqual(authenticated.name, "legacy")
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)
