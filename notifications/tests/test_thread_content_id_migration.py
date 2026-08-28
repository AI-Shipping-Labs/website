"""Schema and legacy-row coverage for notification thread UUID metadata."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag

from notifications.models import Notification


@tag('core')
class NotificationThreadContentIdFieldTest(TestCase):
    def test_field_is_nullable_blank_and_indexed(self):
        field = Notification._meta.get_field('thread_content_id')

        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertTrue(field.db_index)
        self.assertIsNone(
            Notification.objects.create(title='Non-comment notice').thread_content_id,
        )


@tag('core')
class NotificationThreadContentIdMigrationTest(TransactionTestCase):
    migrate_from = [('notifications', '0011_alter_notification_notification_type')]
    migrate_to = [('notifications', '0012_notification_thread_content_id')]

    def test_existing_notification_stays_present_with_null_metadata(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()

        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldNotification = old_apps.get_model('notifications', 'Notification')
            legacy = OldNotification.objects.create(
                title='Legacy comment notice',
                body='Do not infer identity from this row',
                url='/books/legacy/chapters/1#qa-section-42',
                notification_type='content_comment',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewNotification = new_apps.get_model('notifications', 'Notification')
            migrated = NewNotification.objects.get(pk=legacy.pk)

            self.assertEqual(migrated.url, legacy.url)
            self.assertEqual(migrated.title, legacy.title)
            self.assertIsNone(migrated.thread_content_id)
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
