"""Tests for the workshop hand-off on the event detail page (issues #363, #426).

When an Event has a linked Workshop (``Workshop.event = OneToOneField(...)``
populates ``event.workshop``), the event detail page surfaces the
"Full workshop writeup" CTA pointing at ``/workshops/<slug>``. The recording
playback experience itself lives on the workshop landing/video pages.

Issue #426 retired the inline recording branch entirely. Issue #1037 allows
completed standalone events to show explicit recording/material links as
structured resources, while still forbidding inline embeds, timestamps,
transcripts, and workshop-only recording surfaces.
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
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-recording-block"')

    def test_linked_event_omits_video_player(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        # The video_player template tag emits a wrapper with this data-source
        # attribute; its absence proves the player did not render. (We do
        # NOT match on "youtube" since the writeup CTA may legitimately
        # mention it.)
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(response, 'class="video-timestamp')

    def test_linked_event_omits_core_tools_section(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        # Suppressing the Core Tools heading and tag chips.
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, '>Cursor<')
        self.assertNotContains(response, '>Claude Code<')

    def test_linked_event_omits_learning_objectives_section(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertNotContains(response, "What You'll Learn")
        self.assertNotContains(response, 'Build an MVP')
        self.assertNotContains(response, 'Ship to prod')

    def test_linked_event_omits_expected_outcome_section(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertNotContains(response, 'Expected Outcome')
        self.assertNotContains(
            response, 'You will have shipped an MVP.'
        )

    def test_linked_event_omits_materials_section(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        # The event-level resources section and the slide link must be absent.
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'Materials</h2>')
        self.assertNotContains(response, 'https://example.com/slides.pdf')

    def test_event_detail_omits_materials_even_when_linked_workshop_has_materials(self):
        """Issue #646: the event detail page never renders Materials.

        Even when the linked workshop has its own ``Workshop.materials``
        populated, the event detail page must continue to surface only
        the workshop writeup CTA — never an inline materials list.
        Issue #426 boundary holds.
        """
        self.workshop.materials = [
            {'title': 'WorkshopOnlyDoc',
             'url': 'https://example.com/workshop-only.pdf'},
        ]
        self.workshop.save()
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Materials</h2>')
        self.assertNotContains(
            response, 'https://example.com/workshop-only.pdf',
        )
        # And the writeup CTA still points at the workshop.
        self.assertContains(
            response, 'data-testid="event-workshop-writeup"',
        )

    def test_linked_event_shows_workshop_writeup_cta(self):
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertContains(
            response, 'data-testid="event-workshop-writeup"'
        )
        self.assertContains(
            response, 'data-testid="event-workshop-writeup-link"'
        )
        self.assertContains(
            response, '/workshops/2026-04-21-linked-workshop',
        )

    def test_linked_event_shows_announcement_description(self):
        # The event's announcement copy still renders independently of the
        # workshop's description.
        response = self.client.get(self.linked_event.get_absolute_url())
        self.assertContains(
            response, 'Announcement copy for the live session.'
        )


class EventDetailNoWorkshopRecordingRemovedTest(TestCase):
    """Completed events without a linked workshop show only resource links.

    Issue #1037 allows explicit recording/material fields to render as
    structured external links. Issue #426 still forbids inline players,
    transcripts, timestamps, and recording paywalls.
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
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-recording-block"')

    def test_orphan_event_omits_video_player(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(response, 'class="video-timestamp')

    def test_orphan_event_omits_core_tools(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, 'ChatGPT')

    def test_orphan_event_omits_learning_objectives(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertNotContains(response, "What You'll Learn")
        self.assertNotContains(response, 'Understand RAG')

    def test_orphan_event_renders_structured_resources_without_old_materials_ui(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-post-resources"')
        self.assertContains(response, 'data-testid="event-recording-resource"')
        self.assertContains(response, 'Watch recording')
        self.assertContains(
            response, 'https://www.youtube.com/watch?v=ORPHAN',
        )
        self.assertContains(response, 'data-testid="event-material-resource"')
        self.assertContains(response, 'Notes')
        self.assertContains(response, 'https://example.com/notes.pdf')
        self.assertNotContains(response, 'data-testid="recording-materials"')
        self.assertNotContains(response, 'Materials</h2>')

    def test_orphan_event_omits_transcript(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="recording-transcript"')
        self.assertNotContains(response, 'Some transcript copy.')

    def test_orphan_event_has_no_workshop_writeup_cta(self):
        # No linked workshop -> no handoff CTA either.
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertNotContains(
            response, 'data-testid="event-workshop-writeup"'
        )

    def test_orphan_event_still_shows_announcement_description(self):
        response = self.client.get(self.orphan_event.get_absolute_url())
        self.assertContains(
            response,
            'An older session that was never promoted to a workshop.',
        )
