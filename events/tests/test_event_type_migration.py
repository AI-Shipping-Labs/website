"""Tests for removing Event.event_type and status='live'."""

from contextlib import redirect_stdout
from datetime import datetime
from datetime import timezone as dt_timezone
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ('events', '0011_event_content_recap')
POST_MIGRATION = ('events', '0012_remove_event_type_and_live_status')


def _migrate_to(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    with redirect_stdout(StringIO()):
        executor.migrate(list(targets))
    return MigrationExecutor(connection).loader.project_state(list(targets)).apps


class EventTypeRemovalMigrationTest(TransactionTestCase):
    """Migration converts live rows and preserves event-related data."""

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_live_event_migrates_to_upcoming_without_data_loss(self):
        user = get_user_model().objects.create_user(
            email='migration@test.com',
            password='pass',
        )
        apps_pre = _migrate_to(PRE_MIGRATION)
        Event = apps_pre.get_model('events', 'Event')
        EventRegistration = apps_pre.get_model('events', 'EventRegistration')
        EventJoinClick = apps_pre.get_model('events', 'EventJoinClick')

        event = Event.objects.create(
            title='Live Migration Event',
            slug='live-migration-event',
            description='Keep me',
            event_type='live',
            status='live',
            start_datetime=datetime(2026, 5, 1, 18, 0, tzinfo=dt_timezone.utc),
            zoom_meeting_id='123456',
            zoom_join_url='https://zoom.us/j/123456',
            recording_url='https://example.com/recording',
            recap={'summary': 'done'},
            recap_html='<p>done</p>',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/live.yaml',
            source_commit='a' * 40,
        )
        EventRegistration.objects.create(event=event, user_id=user.pk)
        EventJoinClick.objects.create(event=event, user_id=user.pk)

        apps_post = _migrate_to(POST_MIGRATION)
        MigratedEvent = apps_post.get_model('events', 'Event')
        MigratedRegistration = apps_post.get_model('events', 'EventRegistration')
        MigratedJoinClick = apps_post.get_model('events', 'EventJoinClick')

        migrated = MigratedEvent.objects.get(pk=event.pk)
        with self.assertRaises(FieldDoesNotExist):
            MigratedEvent._meta.get_field('event_type')

        self.assertEqual(migrated.status, 'upcoming')
        self.assertEqual(migrated.zoom_meeting_id, '123456')
        self.assertEqual(migrated.zoom_join_url, 'https://zoom.us/j/123456')
        self.assertEqual(migrated.recording_url, 'https://example.com/recording')
        self.assertEqual(migrated.recap, {'summary': 'done'})
        self.assertEqual(migrated.recap_html, '<p>done</p>')
        self.assertEqual(migrated.source_repo, 'AI-Shipping-Labs/content')
        self.assertEqual(migrated.source_path, 'events/live.yaml')
        self.assertEqual(migrated.source_commit, 'a' * 40)
        self.assertTrue(
            MigratedRegistration.objects.filter(event_id=event.pk, user_id=user.pk).exists(),
        )
        self.assertTrue(
            MigratedJoinClick.objects.filter(event_id=event.pk, user_id=user.pk).exists(),
        )
