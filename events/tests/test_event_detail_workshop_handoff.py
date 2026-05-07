"""Tests for the workshop hand-off on the event detail page (issues #363, #426).

When an Event has a linked Workshop (``Workshop.event = OneToOneField(...)``
populates ``event.workshop``), the event detail page surfaces the
"Full workshop writeup" CTA pointing at ``/workshops/<slug>``. The recording
playback experience itself lives on the workshop landing/video pages.

Issue #426 retired the inline recording branch entirely, so completed events
without a linked workshop are announcement pages too — they no longer render
inline embeds, timestamps, materials, transcript, or recording paywalls.
"""

import datetime
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_OPEN
from content.models import Workshop
from events.models import Event


class EventDetailWorkshopHandoffTest(TestCase):
    """Linked Workshop event surfaces the writeup CTA, no inline recording UI."""

    @classmethod
    def setUpTestData(cls):
        # The Event row carries the recording fields (matching what the sync
        # pipeline produces in `_link_or_create_workshop_event`), but because
        # a Workshop points at it via OneToOneField, the event detail page
        # links out to the workshop instead of rendering recording UI.
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

    def setUp(self):
        self.client = Client()

    # --- Linked Workshop event: workshop CTA surfaces, no inline UI --------

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


class EventDetailNoWorkshopRecordingRemovedTest(TestCase):
    """Completed events without a linked workshop are announcement-only.

    Issue #426 removed the inline recording fallback. A completed event with
    recording fields populated and no linked Workshop must NOT render any
    inline player, materials, transcript, timestamps, or recording paywall.
    """

    @classmethod
    def setUpTestData(cls):
        cls.orphan_event = Event.objects.create(
            title='Orphan Past Event',
            slug='orphan-past-event',
            description='An older session that was never promoted to a workshop.',
            start_datetime=timezone.now() - timedelta(days=30),
            status='completed',
            kind='standard',
            recording_url='https://www.youtube.com/watch?v=ORPHAN',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            core_tools=['ChatGPT'],
            learning_objectives=['Understand RAG'],
            outcome='You will know how RAG works.',
            materials=[
                {'title': 'Notes', 'url': 'https://example.com/notes.pdf'}
            ],
            transcript_text='Some transcript copy.',
            required_level=LEVEL_OPEN,
            published=True,
        )

    def test_orphan_event_omits_recording_block_testid(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-recording-block"')

    def test_orphan_event_omits_video_player(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(response, 'class="video-timestamp')

    def test_orphan_event_omits_core_tools(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, 'ChatGPT')

    def test_orphan_event_omits_learning_objectives(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(response, "What You'll Learn")
        self.assertNotContains(response, 'Understand RAG')

    def test_orphan_event_omits_materials(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(response, 'Materials</h2>')
        self.assertNotContains(response, 'https://example.com/notes.pdf')

    def test_orphan_event_omits_transcript(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(response, 'data-testid="recording-transcript"')
        self.assertNotContains(response, 'Some transcript copy.')

    def test_orphan_event_has_no_workshop_writeup_cta(self):
        # No linked workshop -> no handoff CTA either.
        response = self.client.get('/events/orphan-past-event')
        self.assertNotContains(
            response, 'data-testid="event-workshop-writeup"'
        )

    def test_orphan_event_still_shows_announcement_description(self):
        response = self.client.get('/events/orphan-past-event')
        self.assertContains(
            response,
            'An older session that was never promoted to a workshop.',
        )
