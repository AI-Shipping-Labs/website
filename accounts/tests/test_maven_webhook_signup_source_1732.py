"""``signup_source=maven_webhook`` and its lifecycle neutrality (issue #1732)."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag

from accounts.lifecycle import (
    ACCOUNT_CREATING_SIGNUP_SOURCES,
    ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN,
    derive_account_lifecycle,
)
from accounts.models.user import (
    SIGNUP_SOURCE_IMPORTED,
    SIGNUP_SOURCE_MAVEN_WEBHOOK,
)

User = get_user_model()


class MavenWebhookSignupSourceTest(TestCase):
    def test_the_choice_carries_the_operator_facing_label(self):
        user = User.objects.create_user(
            email="labelled-1732@example.com",
            password="pw",
            signup_source=SIGNUP_SOURCE_MAVEN_WEBHOOK,
        )
        self.assertEqual(
            user.get_signup_source_display(), "Maven enrollment webhook",
        )

    def test_it_is_not_an_account_creating_source(self):
        # A webhook-created account was not self-created by the person, so the
        # derived lifecycle bucket must not shift for these members.
        self.assertNotIn(
            SIGNUP_SOURCE_MAVEN_WEBHOOK, ACCOUNT_CREATING_SIGNUP_SOURCES,
        )

    def test_lifecycle_matches_the_imported_bucket_it_replaced(self):
        webhook_user = User.objects.create_user(
            email="webhook-1732@example.com",
            password="pw",
            signup_source=SIGNUP_SOURCE_MAVEN_WEBHOOK,
        )
        imported_user = User.objects.create_user(
            email="imported-1732@example.com",
            password="pw",
            signup_source=SIGNUP_SOURCE_IMPORTED,
        )
        self.assertEqual(
            derive_account_lifecycle(webhook_user),
            ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN,
        )
        self.assertEqual(
            derive_account_lifecycle(webhook_user),
            derive_account_lifecycle(imported_user),
        )


@tag("slow_platform")
class MavenWebhookSignupSourceBackfillMigrationTest(TransactionTestCase):
    # ``integrations`` is named explicitly so the historical project state
    # carries ``MavenEnrollmentEvent``; ``accounts.0030`` alone does not pull
    # that app into the graph.
    migrate_from = [
        ("accounts", "0030_alter_user_signup_source"),
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more"),
    ]
    migrate_to = [
        ("accounts", "0031_backfill_maven_webhook_signup_source"),
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more"),
    ]

    def test_only_imported_users_linked_to_a_created_occurrence_are_rewritten(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldUser = old_apps.get_model("accounts", "User")
            OldOccurrence = old_apps.get_model(
                "integrations", "MavenEnrollmentEvent",
            )

            created = OldUser.objects.create(
                email="created-1732@example.com", signup_source="imported",
            )
            resolved = OldUser.objects.create(
                email="resolved-1732@example.com", signup_source="imported",
            )
            signed_up = OldUser.objects.create(
                email="signed-up-1732@example.com", signup_source="signup",
            )
            oauth = OldUser.objects.create(
                email="oauth-1732@example.com", signup_source="oauth",
            )
            unrelated = OldUser.objects.create(
                email="unrelated-1732@example.com", signup_source="imported",
            )
            OldOccurrence.objects.create(
                dedupe_key="created-1732", identity_hash="created-1732",
                user=created, account_created=True,
            )
            OldOccurrence.objects.create(
                dedupe_key="resolved-1732", identity_hash="resolved-1732",
                user=resolved, account_created=False,
            )
            OldOccurrence.objects.create(
                dedupe_key="signup-1732", identity_hash="signup-1732",
                user=signed_up, account_created=True,
            )
            OldOccurrence.objects.create(
                dedupe_key="oauth-1732", identity_hash="oauth-1732",
                user=oauth, account_created=True,
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewUser = new_apps.get_model("accounts", "User")

            def source(user):
                return NewUser.objects.get(pk=user.pk).signup_source

            self.assertEqual(source(created), "maven_webhook")
            self.assertEqual(source(resolved), "imported")
            self.assertEqual(source(signed_up), "signup")
            self.assertEqual(source(oauth), "oauth")
            self.assertEqual(source(unrelated), "imported")
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
