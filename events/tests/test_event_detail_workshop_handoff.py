"""Tests for the workshop hand-off on the event detail page (issue #363).

When an Event has a linked Workshop (`event.workshop` is set via the
``Workshop.event = OneToOneField(...)`` reverse accessor), the event detail
page must suppress the inline recording block. The recording UI — video
player, timestamps, core tools, learning objectives, expected outcome,
materials, and the recording paywall — lives on the workshop landing/video
pages now. The existing "Full workshop writeup" CTA carries the page.

Legacy past events with no linked workshop are unchanged: the inline
recording block still renders so content can be migrated to workshops one
at a time without a code freeze.
"""

import datetime
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_OPEN
from content.models import Workshop
from events.models import Event


class EventDetailWorkshopHandoffTest(TestCase):
    """Workshop-linked event suppresses inline recording UI; legacy unchanged."""

    @classmethod
    def setUpTestData(cls):
        # --- Workshop-linked completed event ---------------------------------
        # The Event row carries the recording fields (matching what the sync
        # pipeline produces in `_link_or_create_workshop_event`), but because
        # a Workshop points at it via OneToOneField, the event detail page
        # must NOT render the inline recording UI.
        cls.linked_event = Event.objects.create(
            title='Linked Workshop Event',
            slug='linked-workshop-event',
            description='Announcement copy for the live session.',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            kind='workshop',
            recording_url='https://www.youtube.com/watch?v=LINKED',
            timestamps=[{'time_seconds': 0, 'label': 'Welcome'}],
            core_tools=['Cursor', 'Claude Code'],
            learning_objectives=['Build an MVP', 'Ship to prod'],
            outcome='You will have shipped an MVP.',
            materials=[
                {'title': 'Slides', 'url': 'https://example.com/slides.pdf'}
            ],
            required_level=LEVEL_OPEN,
            published=True,
        )
        cls.workshop = Workshop.objects.create(
            slug='linked-workshop',
            title='Linked Workshop',
            date=datetime.date(2026, 4, 21),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            event=cls.linked_event,
        )

        # --- Legacy completed event with NO linked workshop ------------------
        # No Workshop row points at this Event, so `event.workshop` raises
        # `DoesNotExist`; the template's `not event.workshop` check resolves
        # truthy via Django's lazy reverse-accessor handling and the inline
        # recording block renders as it did before #363.
        cls.legacy_event = Event.objects.create(
            title='Legacy Past Event',
            slug='legacy-past-event',
            description='An older recording that was never promoted.',
            start_datetime=timezone.now() - timedelta(days=30),
            status='completed',
            kind='standard',
            recording_url='https://www.youtube.com/watch?v=LEGACY',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            core_tools=['ChatGPT'],
            learning_objectives=['Understand RAG'],
            outcome='You will know how RAG works.',
            materials=[
                {'title': 'Notes', 'url': 'https://example.com/notes.pdf'}
            ],
            required_level=LEVEL_OPEN,
            published=True,
        )

    def setUp(self):
        self.client = Client()

    # --- Workshop-linked event: recording UI is suppressed -----------------

    def test_linked_event_omits_recording_block_testid(self):
        response = self.client.get('/events/linked-workshop-event')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-recording-block"')

    def test_linked_event_omits_video_player(self):
        response = self.client.get('/events/linked-workshop-event')
        # The video_player template tag emits a wrapper with this data-source
        # attribute; its absence proves the player did not render. (We do
        # NOT match on "youtube" since the writeup CTA may legitimately
        # mention it.)
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(response, 'class="video-timestamp')

    def test_linked_event_omits_core_tools_section(self):
        response = self.client.get('/events/linked-workshop-event')
        # Suppressing the Core Tools heading and tag chips.
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, '>Cursor<')
        self.assertNotContains(response, '>Claude Code<')

    def test_linked_event_omits_learning_objectives_section(self):
        response = self.client.get('/events/linked-workshop-event')
        self.assertNotContains(response, "What You'll Learn")
        self.assertNotContains(response, 'Build an MVP')
        self.assertNotContains(response, 'Ship to prod')

    def test_linked_event_omits_expected_outcome_section(self):
        response = self.client.get('/events/linked-workshop-event')
        self.assertNotContains(response, 'Expected Outcome')
        self.assertNotContains(
            response, 'You will have shipped an MVP.'
        )

    def test_linked_event_omits_materials_section(self):
        response = self.client.get('/events/linked-workshop-event')
        # The Materials heading and the slide link must both be absent.
        self.assertNotContains(response, 'Materials</h2>')
        self.assertNotContains(response, 'https://example.com/slides.pdf')

    def test_linked_event_shows_workshop_writeup_cta(self):
        response = self.client.get('/events/linked-workshop-event')
        self.assertContains(
            response, 'data-testid="event-workshop-writeup"'
        )
        self.assertContains(
            response, 'data-testid="event-workshop-writeup-link"'
        )
        self.assertContains(response, '/workshops/linked-workshop')

    def test_linked_event_shows_announcement_description(self):
        # The event's announcement copy still renders independently of the
        # workshop's description.
        response = self.client.get('/events/linked-workshop-event')
        self.assertContains(
            response, 'Announcement copy for the live session.'
        )

    # --- Legacy past event: inline recording block still renders -----------

    def test_legacy_event_renders_recording_block(self):
        response = self.client.get('/events/legacy-past-event')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="event-recording-block"')

    def test_legacy_event_renders_core_tools(self):
        response = self.client.get('/events/legacy-past-event')
        self.assertContains(response, 'Core Tools')
        self.assertContains(response, 'ChatGPT')

    def test_legacy_event_renders_learning_objectives(self):
        response = self.client.get('/events/legacy-past-event')
        self.assertContains(response, "What You'll Learn")
        self.assertContains(response, 'Understand RAG')

    def test_legacy_event_renders_materials(self):
        response = self.client.get('/events/legacy-past-event')
        self.assertContains(response, 'Materials</h2>')
        self.assertContains(response, 'https://example.com/notes.pdf')

    def test_legacy_event_has_no_workshop_writeup_cta(self):
        response = self.client.get('/events/legacy-past-event')
        self.assertNotContains(
            response, 'data-testid="event-workshop-writeup"'
        )
