"""Tests that the events sync pipeline never touches studio-origin events.

Issue #564. The dispatcher already scopes upsert and stale-cleanup
queries to ``source_repo=source.repo_name``. These tests pin the
behavior so a future code change cannot regress it for
``origin='studio'`` events.
"""

import uuid
from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class EventSyncStudioOriginIsolationTest(TestCase):
    """Sync dispatcher must leave studio-origin events untouched."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/content',
            prefix='event-origin-sync-',
        )

    def test_empty_sync_does_not_delete_or_unpublish_studio_event(self):
        """Stale cleanup must not touch studio-origin rows."""
        event = Event.objects.create(
            title='Studio Event',
            slug='studio-event-isolated',
            description='Live and well',
            start_datetime=timezone.now(),
            status='upcoming',
            published=True,
            origin='studio',
        )

        # Run sync against an empty repo — the dispatcher walks every
        # file (zero in this case) and then performs stale cleanup.
        sync_repo(self.source, self.repo)

        event.refresh_from_db()
        self.assertEqual(event.origin, 'studio')
        self.assertTrue(event.published)
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(event.description, 'Live and well')

    def test_studio_event_with_same_slug_as_yaml_is_left_alone(self):
        """A YAML file with a slug matching a studio event must not
        clobber the studio row. The dispatcher scopes upsert lookups by
        ``source_repo`` so the YAML creates a separate row (or fails on
        a unique-slug collision); either way the studio row is
        unchanged.
        """
        series = EventSeries.objects.create(
            name='Series',
            slug='series',
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        studio_event = Event.objects.create(
            title='Studio Title',
            slug='shared-slug',
            description='Studio body',
            start_datetime=timezone.now(),
            origin='studio',
            event_series=series,
            series_position=1,
        )

        self.repo.write_yaml(
            'events/shared-slug.yaml',
            {
                'content_id': str(uuid.uuid4()),
                'slug': 'shared-slug',
                'title': 'GitHub-Authored Event',
                'description': 'YAML body',
                'start_datetime': '2026-06-01T18:00:00Z',
                'status': 'completed',
                'recording_url': 'https://youtube.com/watch?v=abc',
            },
        )

        sync_repo(self.source, self.repo)

        studio_event.refresh_from_db()
        # The studio event MUST be untouched.
        self.assertEqual(studio_event.origin, 'studio')
        self.assertEqual(studio_event.title, 'Studio Title')
        self.assertEqual(studio_event.description, 'Studio body')
        self.assertEqual(studio_event.event_series_id, series.pk)
