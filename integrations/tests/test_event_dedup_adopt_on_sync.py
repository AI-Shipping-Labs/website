"""Adopt-on-sync dedup for STANDARD events (issue #998).

Before minting a brand-new ``origin='github'`` event for an incoming
content-repo YAML, the events dispatcher tries to ADOPT a likely
pre-existing, Studio-created duplicate (same normalized title + same UTC
calendar day, ``content_id``-less, non-series) instead of creating a
second row. These tests pin every branch of that heuristic:

- exactly one match -> adopt (no duplicate; registrations/zoom preserved)
- re-sync -> update the adopted row, never a third row
- more than one match -> skip-and-warn, fall through to a new event
- zero match -> new event exactly as before
- studio event with a content_id -> excluded from the candidate set
- series-occurrence -> excluded from the candidate set
- same-slug but different title -> left alone (#564 regression)
"""

import datetime as dt
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from events.models import Event, EventRegistration, EventSeries
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

User = get_user_model()

ADOPT_TITLE = 'Solving a Real AI Engineer Take-Home Assignment Live'
STUDIO_START = dt.datetime(2026, 6, 1, 18, 0, tzinfo=dt.timezone.utc)


class EventAdoptOnSyncTest(TestCase):
    """``_dispatch_events`` adopts a matching studio event, not a duplicate."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/content',
            prefix='event-adopt-sync-',
        )

    def _make_studio_event(self, **overrides):
        defaults = {
            'title': ADOPT_TITLE,
            'slug': 'studio-take-home-live',
            'description': 'Studio body',
            'start_datetime': STUDIO_START,
            'status': 'upcoming',
            'published': True,
            'origin': 'studio',
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def _write_event_yaml(self, *, slug='github-take-home-live',
                          title=ADOPT_TITLE, content_id=None,
                          start='2026-06-01T00:00:00Z', description='YAML body',
                          rel_path='events/take-home-live.yaml'):
        self.repo.write_yaml(rel_path, {
            'content_id': content_id or str(uuid.uuid4()),
            'slug': slug,
            'title': title,
            'description': description,
            'start_datetime': start,
            'status': 'completed',
            'recording_url': 'https://youtube.com/watch?v=abc',
        })

    def test_adopts_matching_studio_event_instead_of_duplicating(self):
        """One matching content_id-less studio event is adopted, not cloned."""
        user = User.objects.create_user(
            email='reg@example.com', password='x',
        )
        studio = self._make_studio_event(
            zoom_meeting_id='999',
            zoom_join_url='https://zoom.us/j/999',
            platform='zoom',
        )
        registration = EventRegistration.objects.create(
            event=studio, user=user,
        )

        content_id = str(uuid.uuid4())
        self._write_event_yaml(content_id=content_id)
        sync_repo(self.source, self.repo)

        # Exactly one event with this title survives — no duplicate.
        self.assertEqual(
            Event.objects.filter(title=ADOPT_TITLE).count(), 1,
        )

        studio.refresh_from_db()
        # The surviving row is the adopted studio row, now github-owned.
        self.assertEqual(studio.origin, 'github')
        self.assertEqual(studio.source_repo, self.source.repo_name)
        self.assertTrue(studio.source_path)
        self.assertTrue(studio.source_commit)
        self.assertEqual(str(studio.content_id), content_id)
        # Content edits from the YAML are applied.
        self.assertEqual(studio.description, 'YAML body')

        # Operational data is preserved: studio slug, start time, zoom link,
        # and the registration FK all survive the adopt.
        self.assertEqual(studio.slug, 'studio-take-home-live')
        self.assertEqual(studio.start_datetime, STUDIO_START)
        self.assertEqual(studio.zoom_join_url, 'https://zoom.us/j/999')
        self.assertEqual(studio.zoom_meeting_id, '999')
        self.assertTrue(
            EventRegistration.objects.filter(pk=registration.pk).exists(),
        )
        self.assertEqual(studio.registrations.count(), 1)

    def test_resync_updates_adopted_event_without_a_third_row(self):
        """A second sync updates the adopted row by slug+source_repo."""
        self._make_studio_event()
        content_id = str(uuid.uuid4())
        self._write_event_yaml(content_id=content_id, description='First body')
        sync_repo(self.source, self.repo)

        self.assertEqual(Event.objects.filter(title=ADOPT_TITLE).count(), 1)

        # Re-sync with an edited description.
        self._write_event_yaml(content_id=content_id, description='Edited body')
        sync_repo(self.source, self.repo)

        self.assertEqual(Event.objects.filter(title=ADOPT_TITLE).count(), 1)
        adopted = Event.objects.get(title=ADOPT_TITLE)
        self.assertEqual(adopted.description, 'Edited body')
        self.assertEqual(adopted.origin, 'github')

    def test_ambiguous_match_is_skipped_and_logged(self):
        """Two matching studio events -> no adopt, new row, error recorded."""
        s1 = self._make_studio_event(slug='studio-a')
        s2 = self._make_studio_event(slug='studio-b')

        self._write_event_yaml(content_id=str(uuid.uuid4()))
        with self.assertLogs(
            'integrations.services.github', level='WARNING',
        ) as logs:
            sync_log = sync_repo(self.source, self.repo)

        # Neither studio event was mutated.
        for studio in (s1, s2):
            studio.refresh_from_db()
            self.assertEqual(studio.origin, 'studio')
            self.assertIsNone(studio.content_id)

        # A new github-origin row WAS created (fall-through to normal path).
        self.assertEqual(Event.objects.filter(title=ADOPT_TITLE).count(), 3)
        self.assertTrue(
            Event.objects.filter(
                title=ADOPT_TITLE, origin='github',
            ).exists(),
        )

        # The ambiguity is reported with the candidate ids.
        errors = sync_log.errors or []
        ambiguity_errors = [
            e for e in errors if 'ambiguous' in e.get('error', '').lower()
        ]
        self.assertEqual(len(ambiguity_errors), 1)
        error_text = ambiguity_errors[0]['error']
        self.assertIn(str(s1.pk), error_text)
        self.assertIn(str(s2.pk), error_text)
        self.assertTrue(
            any('ambiguous' in line.lower() for line in logs.output),
        )

    def test_zero_match_creates_new_event(self):
        """No matching studio event -> a fresh github-origin row, counted."""
        # An unrelated studio event that should NOT match (different title).
        Event.objects.create(
            title='Totally Different Session',
            slug='different',
            start_datetime=STUDIO_START,
            origin='studio',
        )

        self._write_event_yaml(content_id=str(uuid.uuid4()))
        sync_log = sync_repo(self.source, self.repo)

        new_events = Event.objects.filter(title=ADOPT_TITLE)
        self.assertEqual(new_events.count(), 1)
        self.assertEqual(new_events.first().origin, 'github')
        self.assertGreaterEqual(sync_log.items_created, 1)

    def test_studio_event_with_content_id_is_excluded(self):
        """A studio event that already carries a content_id is not re-adopted."""
        already_adopted = self._make_studio_event(
            content_id=uuid.uuid4(), slug='already-has-cid',
        )

        # Incoming YAML shares title + day but a DIFFERENT content_id.
        self._write_event_yaml(content_id=str(uuid.uuid4()))
        sync_repo(self.source, self.repo)

        already_adopted.refresh_from_db()
        # The content_id-carrying studio event is untouched by the adopt path.
        self.assertEqual(already_adopted.origin, 'studio')
        self.assertEqual(already_adopted.slug, 'already-has-cid')
        # A separate github row was minted instead.
        self.assertTrue(
            Event.objects.filter(
                title=ADOPT_TITLE, origin='github',
            ).exists(),
        )

    def test_series_occurrence_is_excluded(self):
        """A studio event in an event_series is not falsely adopted."""
        series = EventSeries.objects.create(
            name='Weekly Live',
            slug='weekly-live',
            start_time=dt.time(18, 0),
        )
        occurrence = self._make_studio_event(
            slug='series-occurrence',
            event_series=series,
            series_position=1,
        )

        self._write_event_yaml(content_id=str(uuid.uuid4()))
        sync_repo(self.source, self.repo)

        occurrence.refresh_from_db()
        # Series occurrence is excluded from the candidate set -> untouched.
        self.assertEqual(occurrence.origin, 'studio')
        self.assertIsNone(occurrence.content_id)
        self.assertEqual(occurrence.event_series_id, series.pk)
        # The YAML synced to its own github-origin row instead.
        self.assertTrue(
            Event.objects.filter(
                title=ADOPT_TITLE, origin='github',
            ).exists(),
        )

    def test_same_slug_different_title_is_left_alone(self):
        """#564 regression: a same-slug studio event with a different title
        is NOT adopted; the slug-collision skip still applies."""
        series = EventSeries.objects.create(
            name='Series', slug='series',
            start_time=dt.time(18, 0),
        )
        studio_event = Event.objects.create(
            title='Studio Title',
            slug='shared-slug',
            description='Studio body',
            start_datetime=STUDIO_START,
            origin='studio',
            event_series=series,
            series_position=1,
        )

        self.repo.write_yaml('events/shared-slug.yaml', {
            'content_id': str(uuid.uuid4()),
            'slug': 'shared-slug',
            'title': 'GitHub-Authored Event',
            'description': 'YAML body',
            'start_datetime': '2026-06-01T18:00:00Z',
            'status': 'completed',
            'recording_url': 'https://youtube.com/watch?v=abc',
        })

        sync_repo(self.source, self.repo)

        studio_event.refresh_from_db()
        # The studio event MUST be untouched (distinct titles -> no adopt;
        # same slug from a foreign source -> collision skip).
        self.assertEqual(studio_event.origin, 'studio')
        self.assertEqual(studio_event.title, 'Studio Title')
        self.assertEqual(studio_event.description, 'Studio body')
        self.assertEqual(studio_event.event_series_id, series.pk)
