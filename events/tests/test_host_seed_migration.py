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
