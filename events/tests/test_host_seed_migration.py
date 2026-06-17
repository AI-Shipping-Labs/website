"""Tests for the event host seed migration (#994)."""

from contextlib import redirect_stdout
from datetime import datetime
from datetime import timezone as dt_timezone
from io import StringIO

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ('events', '0035_remove_event_max_participants')
POST_MIGRATION = ('events', '0036_host_eventhost_seed')
PRE_TITLE_MIGRATION = ('events', '0037_alter_event_host_email')
POST_TITLE_MIGRATION = ('events', '0038_host_title')


def _migrate_to(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    with redirect_stdout(StringIO()):
        executor.migrate(list(targets))
    return MigrationExecutor(connection).loader.project_state(list(targets)).apps


class HostSeedMigrationTest(TransactionTestCase):
    """The host seed creates people only, not event assignments."""

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_seed_hosts_does_not_auto_assign_existing_events(self):
        apps_pre = _migrate_to(PRE_MIGRATION)
        Event = apps_pre.get_model('events', 'Event')
        Event.objects.create(
            title='Existing Community Event',
            slug='existing-community-event',
            start_datetime=datetime(2026, 6, 20, 17, 0, tzinfo=dt_timezone.utc),
            status='upcoming',
        )
        Event.objects.create(
            title='Personal Brand for Developers: A 30-Day LinkedIn Challenge',
            slug='personal-brand-linkedin-challenge',
            start_datetime=datetime(2026, 6, 27, 17, 0, tzinfo=dt_timezone.utc),
            status='upcoming',
        )

        apps_post = _migrate_to(POST_MIGRATION)
        Host = apps_post.get_model('events', 'Host')
        EventHost = apps_post.get_model('events', 'EventHost')

        self.assertTrue(Host.objects.filter(slug='alexey-grigorev').exists())
        self.assertTrue(Host.objects.filter(slug='valeriia-kuka').exists())
        self.assertFalse(EventHost.objects.exists())

    def test_title_backfill_updates_only_seeded_host_titles(self):
        apps_pre = _migrate_to(PRE_TITLE_MIGRATION)
        Host = apps_pre.get_model('events', 'Host')
        Host.objects.update_or_create(
            slug='alexey-grigorev',
            defaults={
                'name': 'Alexey Grigorev',
                'email': 'alexey-custom@example.com',
                'bio': 'Custom Alexey bio',
                'bio_html': '<p>Custom Alexey bio</p>',
                'photo_url': 'https://cdn.example.com/alexey.jpg',
                'is_active': False,
            },
        )
        Host.objects.update_or_create(
            slug='valeriia-kuka',
            defaults={
                'name': 'Valeriia Kuka',
                'email': 'valeriia-custom@example.com',
                'bio': 'Custom Valeriia bio',
                'bio_html': '<p>Custom Valeriia bio</p>',
                'photo_url': 'https://cdn.example.com/valeriia.jpg',
                'is_active': False,
            },
        )

        apps_post = _migrate_to(POST_TITLE_MIGRATION)
        Host = apps_post.get_model('events', 'Host')

        alexey = Host.objects.get(slug='alexey-grigorev')
        self.assertEqual(alexey.title, 'Chief Agent Officer at AI Shipping Labs')
        self.assertEqual(alexey.email, 'alexey-custom@example.com')
        self.assertEqual(alexey.bio, 'Custom Alexey bio')
        self.assertEqual(alexey.photo_url, 'https://cdn.example.com/alexey.jpg')
        self.assertFalse(alexey.is_active)

        valeriia = Host.objects.get(slug='valeriia-kuka')
        self.assertEqual(valeriia.title, 'Content Strategist')
        self.assertEqual(valeriia.email, 'valeriia-custom@example.com')
        self.assertEqual(valeriia.bio, 'Custom Valeriia bio')
        self.assertEqual(valeriia.photo_url, 'https://cdn.example.com/valeriia.jpg')
        self.assertFalse(valeriia.is_active)
