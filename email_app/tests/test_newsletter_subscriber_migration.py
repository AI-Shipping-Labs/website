"""Migration tests for retiring NewsletterSubscriber."""

from contextlib import redirect_stdout
from io import StringIO

from django.contrib.auth.hashers import is_password_usable
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ("email_app", "0007_emailcampaign_slack_filter")
POST_MIGRATION = ("email_app", "0008_migrate_newsletter_subscribers_to_users")
ACCOUNTS_LEAF = ("accounts", "0008_user_preferred_timezone")


def _migrate_to(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    with redirect_stdout(StringIO()):
        executor.migrate(list(targets))
    return MigrationExecutor(connection).loader.project_state(list(targets)).apps


class NewsletterSubscriberRemovalMigrationTest(TransactionTestCase):
    """Legacy active/inactive subscribers are preserved in User state."""

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with redirect_stdout(StringIO()):
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_legacy_subscribers_map_to_user_newsletter_state(self):
        apps_pre = _migrate_to(PRE_MIGRATION, ACCOUNTS_LEAF)
        User = apps_pre.get_model("accounts", "User")
        NewsletterSubscriber = apps_pre.get_model(
            "email_app",
            "NewsletterSubscriber",
        )

        active_existing = User.objects.create(
            email="active-existing@test.com",
            password="!",
            unsubscribed=True,
            email_preferences={"events": True},
            email_verified=False,
        )
        inactive_existing = User.objects.create(
            email="inactive-existing@test.com",
            password="!",
            unsubscribed=False,
            email_preferences={"newsletter": True},
            email_verified=True,
        )
        mixed_case_existing = User.objects.create(
            email="MixedCase@Test.com",
            password="!",
            unsubscribed=True,
            email_preferences={},
        )

        NewsletterSubscriber.objects.create(
            email="active-existing@test.com",
            is_active=True,
        )
        NewsletterSubscriber.objects.create(
            email="inactive-existing@test.com",
            is_active=False,
        )
        NewsletterSubscriber.objects.create(
            email="mixedcase@test.com",
            is_active=True,
        )
        NewsletterSubscriber.objects.create(
            email="new-active@test.com",
            is_active=True,
        )
        NewsletterSubscriber.objects.create(
            email="new-inactive@test.com",
            is_active=False,
        )

        apps_post = _migrate_to(POST_MIGRATION, ACCOUNTS_LEAF)
        MigratedUser = apps_post.get_model("accounts", "User")

        active_existing = MigratedUser.objects.get(pk=active_existing.pk)
        self.assertFalse(active_existing.unsubscribed)
        self.assertTrue(active_existing.email_preferences["newsletter"])
        self.assertTrue(active_existing.email_preferences["events"])
        self.assertTrue(active_existing.email_verified)

        inactive_existing = MigratedUser.objects.get(pk=inactive_existing.pk)
        self.assertTrue(inactive_existing.unsubscribed)
        self.assertFalse(inactive_existing.email_preferences["newsletter"])

        mixed_case_existing = MigratedUser.objects.get(pk=mixed_case_existing.pk)
        self.assertFalse(mixed_case_existing.unsubscribed)
        self.assertTrue(mixed_case_existing.email_preferences["newsletter"])
        self.assertFalse(
            MigratedUser.objects.filter(email="mixedcase@test.com").exists(),
        )

        new_active = MigratedUser.objects.get(email="new-active@test.com")
        self.assertTrue(new_active.email_verified)
        self.assertFalse(is_password_usable(new_active.password))
        self.assertFalse(new_active.unsubscribed)
        self.assertTrue(new_active.email_preferences["newsletter"])

        new_inactive = MigratedUser.objects.get(email="new-inactive@test.com")
        self.assertTrue(new_inactive.email_verified)
        self.assertFalse(is_password_usable(new_inactive.password))
        self.assertTrue(new_inactive.unsubscribed)
        self.assertFalse(new_inactive.email_preferences["newsletter"])

        with self.assertRaises(LookupError):
            apps_post.get_model("email_app", "NewsletterSubscriber")

    def test_newsletter_subscriber_table_is_removed(self):
        _migrate_to(POST_MIGRATION, ACCOUNTS_LEAF)
        self.assertNotIn(
            "email_app_newslettersubscriber",
            connection.introspection.table_names(),
        )
