"""Studio authoring surface for event recap notes (issue #1458).

Covers:

- The ``Recap / event notes (Markdown)`` textarea renders on the edit form
  for a Studio-origin event AND for a synced GitHub-origin event (the field
  is deliberately NOT disabled on synced rows, same precedent as
  ``post_event_summary`` in #680).
- POSTing the edit form persists ``recap_notes`` in both branches.
- The content-repo precedence notice renders only when ``recap_file`` is set.
- The View / Preview recap link reflects publication state.
"""

from datetime import UTC, datetime

from django.test import TestCase

from events.models import Event
from tests.fixtures import StaffUserMixin

PAST_START = datetime(2026, 6, 8, 16, 0, tzinfo=UTC)
PAST_END = datetime(2026, 6, 8, 17, 0, tzinfo=UTC)


def _edit_post_data(event, **overrides):
    data = {
        'title': event.title,
        'slug': event.slug,
        'description': '',
        'status': event.status,
        'platform': 'zoom',
        'external_host': '',
        'event_date': '08/06/2026',
        'event_time': '16:00',
        'duration_hours': '1',
        'timezone': 'UTC',
        'location': '',
        'required_level': '0',
        'tags': '',
        'post_event_summary': '',
        'recap_notes': '',
    }
    data.update(overrides)
    return data


class StudioEventRecapNotesFieldTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _studio_event(self, **kwargs):
        defaults = {
            'title': 'Book Club Kickoff',
            'slug': 'book-club-kickoff',
            'start_datetime': PAST_START,
            'end_datetime': PAST_END,
            'status': 'completed',
            'timezone': 'UTC',
        }
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def _synced_event(self, **kwargs):
        return self._studio_event(
            title='Synced Launch',
            slug='synced-launch',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-launch.yaml',
            **kwargs,
        )

    def test_field_renders_on_studio_origin_edit_form(self):
        event = self._studio_event(recap_notes='## Week 1\n\nNotes.')
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-recap-notes"')
        self.assertContains(response, 'Recap / event notes (Markdown)')
        self.assertContains(response, '## Week 1')

    def test_field_renders_and_is_not_disabled_on_synced_edit_form(self):
        event = self._synced_event()
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        start = content.index('data-testid="event-recap-notes"')
        textarea = content[start - 400:start + 200]
        self.assertNotIn('disabled', textarea)

    def test_field_absent_on_the_create_form(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-recap-notes"')

    def test_post_persists_recap_notes_on_studio_origin_event(self):
        event = self._studio_event()
        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            _edit_post_data(event, recap_notes='## What we covered\n\nBatching.'),
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.recap_notes, '## What we covered\n\nBatching.')
        self.assertIn('<h2>What we covered</h2>', event.recap_notes_html)

    def test_post_persists_recap_notes_on_synced_event(self):
        event = self._synced_event()
        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            _edit_post_data(event, recap_notes='Organiser notes for this session'),
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(
            event.recap_notes, 'Organiser notes for this session',
        )

    def test_clearing_recap_notes_restores_the_synced_recap(self):
        event = self._studio_event(
            recap_file='launch/recap.md',
            recap_html='<h2>Synced recap</h2>',
            recap_notes='Organiser notes',
        )
        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            _edit_post_data(event, recap_notes=''),
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.recap_notes, '')
        self.assertEqual(event.recap_body_html, '<h2>Synced recap</h2>')

    def test_source_conflict_notice_only_when_recap_file_present(self):
        with_file = self._studio_event(
            slug='with-recap-file', recap_file='launch/recap.md',
            recap_html='<h2>Synced</h2>',
        )
        response = self.client.get(f'/studio/events/{with_file.pk}/edit')
        self.assertContains(
            response, 'data-testid="event-recap-source-conflict"',
        )
        self.assertContains(response, 'launch/recap.md')

        without_file = self._studio_event(slug='no-recap-file')
        response = self.client.get(f'/studio/events/{without_file.pk}/edit')
        self.assertNotContains(
            response, 'data-testid="event-recap-source-conflict"',
        )

    def test_recap_link_label_reflects_publication_state(self):
        published = self._studio_event(
            slug='published-recap', recap_notes='Notes.',
        )
        response = self.client.get(f'/studio/events/{published.pk}/edit')
        self.assertContains(response, 'View recap')
        self.assertContains(response, published.get_recap_url())

        unpublished = self._studio_event(
            slug='unpublished-recap',
            status='upcoming',
            start_datetime=datetime(2099, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2099, 6, 8, 17, 0, tzinfo=UTC),
            recap_notes='Draft notes.',
        )
        response = self.client.get(f'/studio/events/{unpublished.pk}/edit')
        self.assertContains(response, 'Preview recap (not public yet)')

        no_recap = self._studio_event(slug='no-recap-link')
        response = self.client.get(f'/studio/events/{no_recap.pk}/edit')
        self.assertNotContains(
            response, 'data-testid="event-recap-view-link"',
        )
