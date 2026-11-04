"""Explicit event references work without a public recording URL (#2040)."""

from datetime import datetime
from datetime import timezone as dt_timezone

from content.models import Workshop
from events.models import Event
from integrations.tests.sync_fixtures import sync_repo
from integrations.tests.test_workshop_sync import _WorkshopSyncFixtureBase


class WorkshopS3EventSyncTest(_WorkshopSyncFixtureBase):
    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(
            slug='existing-cloud-session', title='Original session title',
            start_datetime=datetime(2026, 4, 21, 12, tzinfo=dt_timezone.utc),
            status='completed', published=True, required_level=20,
            recording_s3_url='https://example.invalid/private/session.mp4',
            transcript_text='Existing transcript',
            transcript_s3_url='https://example.invalid/private/session.vtt',
            zoom_meeting_id='synthetic-meeting',
            timestamps=[{'time_seconds': 12, 'label': 'Existing chapter'}],
            materials=[{'title': 'Existing notes', 'url': 'https://example.org/notes'}],
        )

    def _sync_reference(self, reference, pages_level=10):
        self._write_workshop_yaml(
            extra_yaml=reference, pages_required_level=pages_level,
        )
        self._write_page('2026/2026-04-21-demo', '01-overview.md', title='Overview')
        return sync_repo(self.source, self.repo)

    def test_id_reference_preserves_event_assets_and_gate_on_repeated_sync(self):
        original_event = Event.objects.values().get(pk=self.event.pk)
        for _ in range(2):
            result = self._sync_reference(f'event_id: {self.event.pk}\n')
            self.assertEqual(result.errors, [])
            workshop = Workshop.objects.get(slug='demo')
            self.assertEqual(workshop.event_id, self.event.pk)
            self.assertEqual(workshop.pages_required_level, 10)
            self.assertEqual(workshop.recording_required_level, 20)
            self.assertEqual(Event.objects.count(), 1)
            self.assertEqual(Event.objects.values().get(pk=self.event.pk), original_event)

    def test_slug_reference_resolves_without_a_recording_block(self):
        result = self._sync_reference('event_slug: existing-cloud-session\n')
        self.assertEqual(result.errors, [])
        self.assertEqual(Workshop.objects.get(slug='demo').event_id, self.event.pk)

    def test_pages_gate_can_be_stricter_than_the_linked_event(self):
        result = self._sync_reference(f'event_id: {self.event.pk}\n', pages_level=30)
        self.assertEqual(result.errors, [])
        self.assertEqual(Workshop.objects.get(slug='demo').recording_required_level, 30)
        self.event.refresh_from_db()
        self.assertEqual(self.event.required_level, 20)

    def test_invalid_reference_fails_without_creating_an_event_or_workshop(self):
        result = self._sync_reference('event_id: 99999\n')
        self.assertTrue(result.errors)
        self.assertIn('event_id 99999', str(result.errors))
        self.assertFalse(Workshop.objects.filter(slug='demo').exists())
        self.assertEqual(Event.objects.count(), 1)

    def test_id_reference_takes_precedence_over_conflicting_slug(self):
        result = self._sync_reference(
            f'event_id: {self.event.pk}\nevent_slug: nonexistent-session\n',
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(Workshop.objects.get(slug='demo').event_id, self.event.pk)

    def test_malformed_reference_cannot_resolve_an_existing_event(self):
        for reference in ('event_id: true\n', 'event_id: 1.0\n', "event_slug: '   '\n"):
            with self.subTest(reference=reference):
                result = self._sync_reference(reference)
                self.assertIn('Invalid event_', str(result.errors))
                self.assertFalse(Workshop.objects.filter(slug='demo').exists())
                self.assertEqual(Event.objects.count(), 1)
