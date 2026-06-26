"""Tests for Events and Calendar - issue #83.

Covers:
- Event model fields, defaults, and constraints
- EventRegistration model with unique_together constraint
- Description markdown rendering on save
- Zoom link visibility (5 min before start)
- Events list page: Upcoming and Past sections
- Event detail page: always visible, badges, date/time, location
- Registration gating: authorized user can register, tier check, full event
- Unauthorized user sees CTA "Upgrade to {tier_name} to attend"
- Completed event shows link to recording if recording_id is set
- POST /api/events/{slug}/register and DELETE /api/events/{slug}/unregister
- Admin CRUD with status transitions
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from content.models import Instructor, Workshop
from events.models import Event, EventInstructor, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


# --- Event Model Tests ---


class EventModelFieldsTest(TestCase):
    """Test Event model fields, defaults, and constraints."""

    def test_create_minimal_event(self):
        event = Event.objects.create(
            title='Test Event',
            slug='test-event',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.title, 'Test Event')
        self.assertEqual(event.slug, 'test-event')
        self.assertEqual(event.status, 'draft')
        self.assertEqual(event.description, '')
        self.assertEqual(event.tags, [])
        self.assertEqual(event.required_level, 0)
        self.assertFalse(event.has_recording)
        self.assertIsNotNone(event.created_at)
        self.assertIsNotNone(event.updated_at)

    def test_create_full_event(self):
        event = Event.objects.create(
            title='Full Event',
            slug='full-event',
            description='# Hello\n\nThis is a test.',
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=2),
            timezone='Europe/Berlin',
            zoom_meeting_id='123456789',
            zoom_join_url='https://zoom.us/j/123456789',
            location='Zoom',
            tags=['python', 'django'],
            required_level=LEVEL_MAIN,
            status='upcoming',
        )
        self.assertEqual(event.timezone, 'Europe/Berlin')
        self.assertEqual(event.zoom_meeting_id, '123456789')
        self.assertEqual(event.location, 'Zoom')
        self.assertEqual(event.tags, ['python', 'django'])
        self.assertEqual(event.required_level, LEVEL_MAIN)
        self.assertEqual(event.status, 'upcoming')

    def test_get_absolute_url(self):
        """Issue #673: canonical URL is ``/events/<id>/<slug>``."""
        event = Event.objects.create(
            title='My Event',
            slug='my-event',
            start_datetime=timezone.now(),
        )
        self.assertEqual(
            event.get_absolute_url(),
            f'/events/{event.id}/my-event',
        )

    def test_get_absolute_url_unsaved_returns_empty_string(self):
        """Issue #673: an unsaved row has no id, so reverse() would
        raise ``NoReverseMatch``. The helper returns ``''`` instead so
        admin previews and ``__str__`` don't blow up.
        """
        event = Event(slug='my-event')
        self.assertEqual(event.get_absolute_url(), '')

    def test_get_recording_url_matches_get_absolute_url(self):
        """Issue #673: the two helpers must agree so callers that
        still go through ``get_recording_url`` end up at the same
        canonical URL.
        """
        event = Event.objects.create(
            title='Rec', slug='rec',
            start_datetime=timezone.now(),
        )
        self.assertEqual(
            event.get_recording_url(),
            event.get_absolute_url(),
        )

    def test_default_timezone(self):
        event = Event.objects.create(
            title='TZ Test', slug='tz-test',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.timezone, 'Europe/Berlin')


class EventMarkdownRenderingTest(TestCase):
    """Test description markdown rendering on save."""

    def test_description_html_generated_on_save(self):
        event = Event.objects.create(
            title='MD Test', slug='md-test',
            description='# Hello World',
            start_datetime=timezone.now(),
        )
        self.assertIn('<h1>Hello World</h1>', event.description_html)

    def test_empty_description_html_when_no_description(self):
        event = Event.objects.create(
            title='No Desc', slug='no-desc',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.description_html, '')


class EventZoomLinkTest(TestCase):
    """Test can_show_zoom_link method."""

    def test_zoom_link_visible_within_5_minutes(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='upcoming',
        )
        self.assertTrue(event.can_show_zoom_link())

    def test_zoom_link_not_visible_more_than_5_min_before(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() + timedelta(minutes=6),
            status='upcoming',
        )
        self.assertFalse(event.can_show_zoom_link())

    def test_zoom_link_visible_after_start(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() - timedelta(minutes=5),
            status='upcoming',
        )
        self.assertTrue(event.can_show_zoom_link())

    def test_zoom_link_not_visible_after_live_window(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() - timedelta(hours=2),
            status='upcoming',
        )
        self.assertFalse(event.can_show_zoom_link())

    def test_zoom_link_not_visible_for_cancelled_event(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='cancelled',
        )
        self.assertFalse(event.can_show_zoom_link())

    def test_zoom_link_not_visible_without_url(self):
        event = Event(
            zoom_join_url='',
            start_datetime=timezone.now() + timedelta(minutes=5),
        )
        self.assertFalse(event.can_show_zoom_link())


# --- EventRegistration Model Tests ---


class EventRegistrationModelTest(TestCase):
    """Test EventRegistration model constraints."""

    def setUp(self):
        self.event = Event.objects.create(
            title='Reg Test', slug='reg-test',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        self.user = User.objects.create_user(
            email='reg@test.com', password='pass',
        )

    def test_create_registration(self):
        reg = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        self.assertIsNotNone(reg.registered_at)
        self.assertEqual(reg.event, self.event)
        self.assertEqual(reg.user, self.user)

    def test_registration_count(self):
        self.assertEqual(self.event.registration_count, 0)
        EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        self.assertEqual(self.event.registration_count, 1)

# --- Events List Page Tests ---


class EventsListPageTest(TestCase):
    """Test GET /events shows Upcoming and Past sections."""

    def setUp(self):
        self.client = Client()
        self.upcoming_event = Event.objects.create(
            title='Upcoming Workshop',
            slug='upcoming-workshop',
            description='An upcoming event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            location='Zoom',
        )
        self.past_event = Event.objects.create(
            title='Past Workshop',
            slug='past-workshop',
            description='A past event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        self.draft_event = Event.objects.create(
            title='Draft Workshop',
            slug='draft-workshop',
            start_datetime=timezone.now() + timedelta(days=14),
            status='draft',
        )

    def test_events_list_template(self):
        response = self.client.get('/events')
        self.assertTemplateUsed(response, 'events/events_list.html')

    def test_upcoming_section_shows_upcoming_events(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Upcoming Workshop')

    def test_past_section_shows_completed_events(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Past Workshop')

    def test_draft_events_not_shown(self):
        response = self.client.get('/events')
        self.assertNotContains(response, 'Draft Workshop')

    def test_upcoming_section_header(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Upcoming')

    def test_past_section_header(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Past')

    def test_event_card_omits_type_badge(self):
        response = self.client.get('/events')
        self.assertNotContains(response, 'Live')
        self.assertNotContains(response, 'Async')

    def test_event_card_shows_date(self):
        response = self.client.get('/events')
        self.assertContains(response, self.upcoming_event.weekday_date())
        self.assertNotContains(response, self.upcoming_event.formatted_time())

    def test_event_card_shows_location(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Zoom')


class EventsListMarkdownRenderingTest(TestCase):
    """Regression tests for issue #707.

    The events list cards used to render `event.description` (raw markdown)
    instead of `event.description_html` (rendered + striptags-cleaned), so
    markdown link syntax like `[anchor](https://...)` appeared as literal
    text on the upcoming/past cards. Cards apply `striptags|truncatechars`
    because the whole card is a single clickable <a> and HTML5 forbids
    nested anchors.
    """

    def _decode_card_html(self, response):
        return response.content.decode()

    def test_upcoming_card_renders_markdown_link_as_plain_text(self):
        Event.objects.create(
            title='Upcoming Markdown Event',
            slug='upcoming-md-link-event',
            description='In the [previous workshop](https://example.com/foo) we covered backend setup',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/events')
        body = self._decode_card_html(response)
        # Anchor text survives as plain text
        self.assertIn('previous workshop', body)
        # Raw markdown syntax must not leak through
        self.assertNotIn('[previous workshop]', body)
        self.assertNotIn('](https://example.com/foo)', body)
        # striptags should have removed the rendered <a> tag too
        self.assertNotIn('<a href="https://example.com/foo"', body)

    def test_past_card_renders_markdown_link_as_plain_text(self):
        # /events?filter=past only surfaces completed events that are
        # published AND have a non-empty recording_url — see
        # events.views.pages.events_list for the query.
        Event.objects.create(
            title='Past Markdown Event',
            slug='past-md-link-event',
            description='In the [previous workshop](https://example.com/foo) we covered backend setup',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            published=True,
            recording_url='https://example.com/recording',
        )
        response = self.client.get('/events?filter=past')
        body = self._decode_card_html(response)
        self.assertIn('previous workshop', body)
        self.assertNotIn('[previous workshop]', body)
        self.assertNotIn('](https://example.com/foo)', body)
        self.assertNotIn('<a href="https://example.com/foo"', body)

    def test_upcoming_card_renders_bold_and_italic_as_plain_text(self):
        Event.objects.create(
            title='Upcoming Emphasis Event',
            slug='upcoming-emph-event',
            description='This is **bold** and _italic_ text',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/events')
        body = self._decode_card_html(response)
        # Plain words survive
        self.assertIn('bold', body)
        self.assertIn('italic', body)
        # Raw markdown emphasis markers must not leak through
        self.assertNotIn('**bold**', body)
        self.assertNotIn('_italic_', body)


class EventsListTierBadgeTest(TierSetupMixin, TestCase):
    """Test tier badge on events list."""

    def test_gated_event_shows_tier_badge(self):
        Event.objects.create(
            title='Premium Event',
            slug='premium-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_PREMIUM,
        )
        response = self.client.get('/events')
        # Lock icon appears next to the tier name in the badge
        self.assertContains(response, 'data-lucide="lock"')
        # The tier name "Premium" appears in the gating badge context
        content = response.content.decode()
        # Both the lock icon and tier name are in the same badge span
        lock_pos = content.index('data-lucide="lock"')
        premium_pos = content.index('Premium', lock_pos)
        # Premium appears shortly after the lock icon (within the same span)
        self.assertLess(premium_pos - lock_pos, 100)

    def test_open_event_no_tier_badge(self):
        Event.objects.create(
            title='Open Event',
            slug='open-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        response = self.client.get('/events')
        content = response.content.decode()
        # The open event should not have a lock icon (only tier-gated ones do)
        # We check that the card for this event doesn't contain a lock
        self.assertNotIn('data-lucide="lock"', content)


class EventsListNoCapacityTest(TestCase):
    """Issue #984: the events list never shows capacity copy.

    Capacity was removed entirely; an event with many registrations must
    still render as a normal card with no "spots remaining" / "Event is
    full" text.
    """

    def test_no_capacity_copy_on_list(self):
        event = Event.objects.create(
            title='Popular Event',
            slug='popular-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        for i in range(3):
            user = User.objects.create_user(
                email=f'u{i}@test.com', password='pass',
            )
            EventRegistration.objects.create(event=event, user=user)
        response = self.client.get('/events')
        self.assertContains(response, 'Popular Event')
        self.assertNotContains(response, 'spots remaining')
        self.assertNotContains(response, 'Event is full')


class EventsListRecordingLinkTest(TestCase):
    """Test past events show the recording indicator on the default /events view."""

    def test_completed_event_with_recording_shows_indicator(self):
        event = Event.objects.create(
            title='Recorded Event',
            slug='recorded-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        response = self.client.get('/events')
        # The card itself already links to /events/<id>/<slug>. The past
        # section shows a small "Recording available" indicator for
        # such events.
        self.assertContains(response, 'Recording available')
        self.assertContains(response, event.get_absolute_url())
        # Must not link out to the old standalone recording surface.
        self.assertNotContains(response, '/event-recordings/')

    def test_completed_event_without_recording_no_indicator(self):
        Event.objects.create(
            title='No Recording Event',
            slug='no-recording-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'Recording available')


# --- Event Detail Page Tests ---


class EventDetailPageTest(TestCase):
    """Test GET /events/{slug} detail page."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Detail Event',
            slug='detail-event',
            description='A detailed description of the event.',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=2),
            timezone='Europe/Berlin',
            location='Zoom',
            tags=['python', 'agents'],
            status='upcoming',
        )

    def test_detail_template(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertTemplateUsed(response, 'events/event_detail.html')
        self.assertTemplateUsed(response, 'events/_event_hero_media.html')
        self.assertTemplateUsed(response, 'events/_event_header.html')
        self.assertTemplateUsed(response, 'events/_event_registration_card.html')
        self.assertTemplateUsed(response, 'events/_event_description.html')

    def test_detail_uses_static_page_script(self):
        response = self.client.get(self.event.get_absolute_url())
        html = response.content.decode()

        self.assertIn('data-event-detail', html)
        self.assertIn('data-event-slug="detail-event"', html)
        self.assertIn('/static/js/events/event_detail.js', html)
        self.assertNotIn('onclick="registerForEvent', html)
        self.assertNotIn('onclick="unregisterFromEvent', html)
        self.assertNotIn('function registerForEvent', html)

    def test_shows_title(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'Detail Event')

    def test_shows_description(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'A detailed description of the event.')

    def test_shows_location(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'Zoom')

    def test_shows_timezone(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'Europe/Berlin')

    def test_omits_event_type(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(response, 'Live Event')
        self.assertNotContains(response, 'Async Event')

    def test_shows_tags(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'python')
        self.assertContains(response, 'agents')

    def test_title_tag_format(self):
        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()
        self.assertIn('<title>Detail Event | AI Shipping Labs</title>', content)

    def test_404_for_nonexistent_id(self):
        """Issue #673: an unknown id returns 404."""
        response = self.client.get('/events/99999/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_draft_event_404_for_anonymous(self):
        draft = Event.objects.create(
            title='Draft Event', slug='draft-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        response = self.client.get(draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_draft_event_visible_to_staff(self):
        draft = Event.objects.create(
            title='Draft Event', slug='draft-event-staff',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        User.objects.create_superuser(
            email='admin@test.com', password='pass',
        )
        self.client.login(email='admin@test.com', password='pass')
        response = self.client.get(draft.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_detail.html')
        self.assertContains(response, 'Draft Event')

    def test_back_link_to_events(self):
        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()
        self.assertIn('href="/events"', content)

    def test_anonymous_sees_email_only_registration_form(self):
        """Issue #513: anonymous visitors on a free upcoming event see an
        inline email-only registration form, not the legacy "Sign in /
        Create free account" button pair. The "Already have an account?
        Sign in" link below the form preserves the sign-in path for
        returning users.
        """
        response = self.client.get(self.event.get_absolute_url())
        # The new email-only form replaces the old sign-in CTA on free
        # events (required_level == 0).
        self.assertContains(response, 'event-anonymous-email-form')
        self.assertContains(response, 'id="event-anon-email"')
        self.assertContains(response, 'Register for this event')
        # The "Already have an account?" sign-in link still preserves
        # the next URL for returning users. Issue #673: ``next`` carries
        # the canonical id+slug URL now.
        self.assertContains(
            response,
            f'/accounts/login/?next={self.event.get_absolute_url()}',
        )
        # The legacy "Create free account" CTA is gone for free events.
        self.assertNotContains(response, 'Create free account')


class EventDetailRecordingRemovedTest(TestCase):
    """Issues #426/#1037: event detail renders links, not playback UI.

    Standalone completed events can render structured external recording
    and material resources, but they must not embed a video player,
    transcript, chapters, or other workshop recording surfaces.
    """

    def _create_completed_event(self, **overrides):
        defaults = {
            'title': 'Completed Event',
            'slug': 'completed-event',
            'description': 'A short completed-event description.',
            'start_datetime': timezone.now() - timedelta(days=7),
            'end_datetime': timezone.now() - timedelta(days=7, hours=-1),
            'status': 'completed',
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def test_completed_standalone_recording_and_materials_render_as_resources(self):
        event = Event.objects.create(
            title='Completed Event',
            slug='completed-event',
            description='A short completed-event description.',
            start_datetime=timezone.now() - timedelta(days=7),
            end_datetime=timezone.now() - timedelta(days=7, hours=-1),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            timestamps=[{'time_seconds': 0, 'label': 'Welcome'}],
            materials=[
                {
                    'title': 'Slides',
                    'url': 'https://example.com/slides.pdf',
                    'type': 'PDF',
                },
            ],
            transcript_text='Event transcript text.',
            core_tools=['NotebookLM'],
            learning_objectives=['Build a prototype'],
            outcome='Expected outcome text.',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertTemplateUsed(response, 'events/_event_post_resources.html')
        self.assertContains(response, 'data-testid="event-post-resources"')
        self.assertContains(response, 'data-testid="event-recording-resource"')
        self.assertContains(response, 'Watch recording')
        self.assertContains(response, 'href="https://youtube.com/watch?v=test"')
        self.assertContains(response, 'youtube.com')
        self.assertContains(response, 'data-testid="event-materials-resources"')
        self.assertContains(response, 'data-testid="event-material-resource"')
        self.assertContains(response, 'Slides')
        self.assertContains(response, 'PDF')
        self.assertContains(response, 'href="https://example.com/slides.pdf"')
        # No inline recording block markers anywhere on the page.
        self.assertNotContains(response, 'data-testid="event-recording-block"')
        self.assertNotContains(response, 'data-testid="video-chapters"')
        self.assertNotContains(response, 'class="video-timestamp')
        self.assertNotContains(response, '<iframe')
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(
            response, 'data-testid="recording-materials"',
        )
        self.assertNotContains(
            response, 'data-testid="recording-transcript"',
        )
        self.assertNotContains(response, 'Event transcript text.')
        self.assertNotContains(response, 'NotebookLM')
        self.assertNotContains(response, 'Build a prototype')
        self.assertNotContains(response, 'Expected outcome text.')
        # No link out to the retired standalone recording surface either.
        self.assertNotContains(response, '/event-recordings/')

    def test_completed_standalone_only_recording_has_no_empty_materials(self):
        event = self._create_completed_event(
            slug='recording-only-event',
            recording_url='https://video.example.com/replay',
            materials=[],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-post-resources"')
        self.assertContains(response, 'data-testid="event-recording-resource"')
        self.assertContains(response, 'Watch recording')
        self.assertNotContains(
            response, 'data-testid="event-materials-resources"',
        )
        self.assertNotContains(response, 'Materials</h2>')

    def test_completed_standalone_only_materials_has_no_empty_recording(self):
        event = self._create_completed_event(
            slug='materials-only-event',
            recording_url='',
            materials=[
                {
                    'title': 'Session notes',
                    'url': 'https://docs.example.com/session-notes',
                    'type': 'notes',
                },
            ],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-post-resources"')
        self.assertContains(response, 'data-testid="event-materials-resources"')
        self.assertContains(response, 'Session notes')
        self.assertContains(response, 'notes')
        self.assertNotContains(response, 'Watch recording')
        self.assertNotContains(
            response, 'data-testid="event-recording-resource"',
        )

    def test_malformed_materials_are_skipped_without_empty_section(self):
        event = self._create_completed_event(
            slug='malformed-materials-event',
            recording_url='',
            materials=[
                {'title': 'Missing URL'},
                {'url': 'https://docs.example.com/missing-title'},
                {'title': 'Invalid URL', 'url': 'not-a-url'},
                'not-a-dict',
            ],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'Missing URL')
        self.assertNotContains(response, 'Invalid URL')
        self.assertNotContains(response, 'missing-title')

    def test_valid_materials_render_and_malformed_entries_are_skipped(self):
        event = self._create_completed_event(
            slug='mixed-materials-event',
            recording_url='',
            materials=[
                {
                    'title': 'Session notes',
                    'url': 'https://docs.example.com/session-notes',
                },
                {'title': 'Missing URL'},
            ],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'Session notes')
        self.assertContains(response, 'https://docs.example.com/session-notes')
        self.assertNotContains(response, 'Missing URL')

    def test_description_links_are_not_scraped_into_resources(self):
        event = self._create_completed_event(
            slug='description-link-event',
            description=(
                'Watch [the raw link](https://youtube.com/watch?v=raw) '
                'and read [notes](https://docs.example.com/raw-notes).'
            ),
            recording_url='',
            materials=[],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'https://youtube.com/watch?v=raw')
        self.assertContains(response, 'https://docs.example.com/raw-notes')
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'Watch recording')

    def test_linked_workshop_suppresses_event_level_resources(self):
        event = self._create_completed_event(
            slug='linked-workshop-event',
            kind='workshop',
            recording_url='https://youtube.com/watch?v=linked',
            materials=[
                {
                    'title': 'Event-level notes',
                    'url': 'https://docs.example.com/event-level',
                },
            ],
        )
        Workshop.objects.create(
            slug='linked-workshop',
            title='Linked Workshop',
            date=timezone.localdate(),
            status='published',
            event=event,
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-workshop-writeup"')
        self.assertContains(response, 'View workshop writeup')
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'https://youtube.com/watch?v=linked')
        self.assertNotContains(response, 'Event-level notes')

    def test_upcoming_event_suppresses_prepopulated_resources(self):
        event = Event.objects.create(
            title='Upcoming Event',
            slug='upcoming-event',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
            status='upcoming',
            recording_url='https://youtube.com/watch?v=early',
            materials=[
                {
                    'title': 'Early notes',
                    'url': 'https://docs.example.com/early',
                },
            ],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'Watch recording')
        self.assertNotContains(response, 'Early notes')

    def test_insufficient_tier_suppresses_event_level_resources(self):
        event = self._create_completed_event(
            slug='gated-completed-event',
            required_level=LEVEL_MAIN,
            recording_url='https://youtube.com/watch?v=gated',
            materials=[
                {
                    'title': 'Main notes',
                    'url': 'https://docs.example.com/main-notes',
                },
            ],
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertNotContains(response, 'https://youtube.com/watch?v=gated')
        self.assertNotContains(response, 'Main notes')

    def test_feedback_has_intentional_top_gap_after_short_description(self):
        event = self._create_completed_event(
            slug='short-description-feedback-event',
            recording_url='',
            materials=[],
            description='Short.',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'Short.')
        self.assertNotContains(response, 'data-testid="event-post-resources"')
        self.assertContains(
            response,
            'class="mt-12 mb-12 rounded-lg border border-border bg-card p-6"',
        )
        self.assertContains(response, 'data-testid="event-feedback-section"')

    def test_completed_without_recording_no_block(self):
        event = Event.objects.create(
            title='No Rec Event',
            slug='no-rec-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-recording-block"')


# --- Access Control Tests ---


class EventDetailAccessControlTest(TierSetupMixin, TestCase):
    """Smoke + access-control matrix for event detail pages.

    Full per-tier matrix (free/basic/main/premium) is exercised
    end-to-end by
    `playwright_tests/test_access_control.py::TestScenario12FreeMemberGatedEvent`.
    Only the gated-vs-not-gated smokes and the access-flag context
    invariant remain at the Django layer (#261).
    """

    def setUp(self):
        self.client = Client()
        self.open_event = Event.objects.create(
            title='Open Event',
            slug='open-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        self.gated_event = Event.objects.create(
            title='Gated Event',
            slug='gated-event',
            description='This event is gated.',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )

    def test_anonymous_sees_open_event(self):
        response = self.client.get(self.open_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Event')

    def test_anonymous_sees_gated_event_with_upgrade_cta(self):
        """Detail page is always visible, but registration is gated."""
        response = self.client.get(self.gated_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gated Event')
        self.assertContains(response, 'This event is gated.')
        # Anonymous users do not see the register button; the gating CTA
        # is an upgrade prompt that appears for any insufficient-tier user.

    def test_main_user_sees_register_button(self):
        user = User.objects.create_user(
            email='main@test.com',
            password='pass',
            email_verified=True,
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='pass')
        response = self.client.get(self.gated_event.get_absolute_url())
        self.assertTrue(response.context['has_access'])
        self.assertContains(response, 'id="register-btn"')
        self.assertNotContains(response, 'Upgrade to Main')

    def test_basic_user_sees_upgrade_cta_for_main_event(self):
        user = User.objects.create_user(
            email='basic@test.com',
            password='pass',
            email_verified=True,
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='pass')
        response = self.client.get(self.gated_event.get_absolute_url())
        self.assertContains(response, 'Upgrade to Main to attend')
        # Issue #481: Main event copy still uses "or above" because Main
        # is not the highest tier. Issue #671: copy starts with
        # "Registering for this event" to stay consistent with the
        # anonymous-on-paid CTA.
        self.assertContains(
            response,
            'Registering for this event requires a Main membership or above.',
        )

    def test_premium_event_drops_or_above_suffix(self):
        """Issue #481 AC: Premium-only CTAs do not say "or above".

        Premium is the highest public tier so "Premium membership or
        above" is misleading — there is no higher tier to upgrade to.
        """
        premium_event = Event.objects.create(
            title='Premium Event',
            slug='premium-event',
            description='Premium-only event.',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_PREMIUM,
        )
        user = User.objects.create_user(
            email='basic-premium@test.com',
            password='pass',
            email_verified=True,
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic-premium@test.com', password='pass')
        response = self.client.get(premium_event.get_absolute_url())
        self.assertContains(response, 'Upgrade to Premium to attend')
        self.assertContains(
            response,
            'Registering for this event requires a Premium membership.',
        )
        self.assertNotContains(response, 'Premium membership or above')
        # And the lock badge in the header is "Premium" (no "+").
        self.assertNotContains(response, 'Premium+')

    def test_event_with_registrations_never_shows_full(self):
        """Issue #984: capacity removed, so a heavily-registered event detail
        page never shows "Event is full" and still offers registration."""
        event = Event.objects.create(
            title='Popular Event',
            slug='popular-event-detail',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        other_user = User.objects.create_user(email='other@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=other_user)

        User.objects.create_user(
            email='viewer@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='viewer@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'Event is full')
        self.assertContains(response, 'Register for this event')


class EventDetailZoomLinkTest(TierSetupMixin, TestCase):
    """Test Zoom join link display on event detail."""

    def test_zoom_link_shown_when_registered_and_within_5_min(self):
        event = Event.objects.create(
            title='Soon Event',
            slug='soon-event',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/123456',
        )
        user = User.objects.create_user(
            email='soon@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='soon@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        # Issue #1082: the on-page Join button now uses the id-canonical
        # ``/events/<id>/<slug>/join`` URL via ``Event.get_join_url``.
        self.assertContains(response, event.get_join_url())
        self.assertContains(response, 'data-testid="event-join-now"')
        self.assertNotContains(response, 'https://zoom.us/j/123456')

    def test_zoom_link_not_shown_6_minutes_before_start(self):
        event = Event.objects.create(
            title='Six Minute Event',
            slug='six-minute-event',
            start_datetime=timezone.now() + timedelta(minutes=6),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/654321',
        )
        user = User.objects.create_user(
            email='six@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='six@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, "You're registered!")
        self.assertContains(response, '5 minutes before')
        self.assertNotContains(response, 'data-testid="event-join-now"')
        self.assertNotContains(response, event.get_join_url())
        self.assertNotContains(response, 'https://zoom.us/j/654321')

    def test_zoom_link_not_shown_when_far_from_start(self):
        event = Event.objects.create(
            title='Far Event',
            slug='far-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/999999',
        )
        user = User.objects.create_user(
            email='far@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='far@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, event.get_join_url())

    def test_zoom_link_not_shown_when_not_registered(self):
        event = Event.objects.create(
            title='Not Reg Event',
            slug='not-reg-event',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/111111',
        )
        User.objects.create_user(
            email='notreg@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='notreg@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'https://zoom.us/j/111111')


class EventDetailRegisteredStatusTest(TierSetupMixin, TestCase):
    """Test registered status display on event detail."""

    def test_registered_user_sees_registered_badge(self):
        event = Event.objects.create(
            title='Reg Status Event',
            slug='reg-status-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        user = User.objects.create_user(
            email='regstat@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='regstat@test.com', password='pass')
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, "You're registered!")

    def test_unregistered_user_sees_register_button(self):
        unreg_event = Event.objects.create(
            title='Unreg Event',
            slug='unreg-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        User.objects.create_user(
            email='unreg@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='unreg@test.com', password='pass')
        response = self.client.get(unreg_event.get_absolute_url())
        self.assertFalse(response.context['is_registered'])
        self.assertContains(response, 'id="register-btn"')
        self.assertNotContains(response, "You're registered!")


# --- Registration API Tests ---


class RegisterForEventAPITest(TierSetupMixin, TestCase):
    """Test POST /api/events/{slug}/register."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='API Event',
            slug='api-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        self.user = User.objects.create_user(
            email='apiuser@test.com', password='pass', email_verified=True,
        )

    def test_register_success(self):
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/api-event/register')
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'registered')
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).exists()
        )

    def test_register_unauthenticated(self):
        response = self.client.post('/api/events/api-event/register')
        self.assertEqual(response.status_code, 401)

    def test_register_insufficient_tier(self):
        Event.objects.create(
            title='Gated API',
            slug='gated-api',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_PREMIUM,
        )
        self.user.tier = self.free_tier
        self.user.save()
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/gated-api/register')
        self.assertEqual(response.status_code, 403)

    def test_register_already_registered(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/api-event/register')
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertEqual(data['error'], 'Already registered')

    def test_register_succeeds_regardless_of_existing_registrations(self):
        """Issue #984: capacity removed — registration is never blocked by
        how many users are already registered (no 410 "Event is full")."""
        for i in range(3):
            other_user = User.objects.create_user(
                email=f'other{i}@test.com', password='pass',
            )
            EventRegistration.objects.create(event=self.event, user=other_user)
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/api-event/register')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).exists()
        )

    def test_register_nonexistent_event(self):
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/nonexistent/register')
        self.assertEqual(response.status_code, 404)

    def test_register_draft_event(self):
        Event.objects.create(
            title='Draft API',
            slug='draft-api',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/draft-api/register')
        self.assertEqual(response.status_code, 404)

    def test_register_non_upcoming_events_rejected(self):
        self.client.login(email='apiuser@test.com', password='pass')

        for status in ['completed', 'cancelled']:
            event = Event.objects.create(
                title=f'{status.title()} API',
                slug=f'{status}-api',
                start_datetime=timezone.now() - timedelta(days=1),
                status=status,
                required_level=LEVEL_OPEN,
            )
            response = self.client.post(f'/api/events/{event.slug}/register')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(
                response.json()['error'],
                'Event is not open for registration',
            )
            self.assertFalse(
                EventRegistration.objects.filter(
                    event=event, user=self.user,
                ).exists()
            )

    def test_register_only_post(self):
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.get('/api/events/api-event/register')
        self.assertEqual(response.status_code, 405)


class UnregisterFromEventAPITest(TestCase):
    """Test DELETE /api/events/{slug}/unregister."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Unreg Event',
            slug='unreg-api-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        self.user = User.objects.create_user(
            email='unreg@test.com', password='pass', email_verified=True,
        )

    def test_unregister_success(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        self.client.login(email='unreg@test.com', password='pass')
        response = self.client.delete('/api/events/unreg-api-event/unregister')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'unregistered')
        self.assertFalse(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).exists()
        )

    def test_unregister_unauthenticated(self):
        response = self.client.delete('/api/events/unreg-api-event/unregister')
        self.assertEqual(response.status_code, 401)

    def test_unregister_not_registered(self):
        self.client.login(email='unreg@test.com', password='pass')
        response = self.client.delete('/api/events/unreg-api-event/unregister')
        self.assertEqual(response.status_code, 404)

    def test_unregister_non_upcoming_events_rejected(self):
        self.client.login(email='unreg@test.com', password='pass')

        for status in ['completed', 'cancelled']:
            event = Event.objects.create(
                title=f'{status.title()} Unreg API',
                slug=f'{status}-unreg-api',
                start_datetime=timezone.now() - timedelta(days=1),
                status=status,
            )
            EventRegistration.objects.create(event=event, user=self.user)

            response = self.client.delete(f'/api/events/{event.slug}/unregister')

            self.assertEqual(response.status_code, 409)
            self.assertEqual(
                response.json()['error'],
                'Event is not open for registration',
            )
            self.assertTrue(
                EventRegistration.objects.filter(
                    event=event, user=self.user,
                ).exists()
            )

    def test_unregister_only_delete(self):
        self.client.login(email='unreg@test.com', password='pass')
        response = self.client.post('/api/events/unreg-api-event/unregister')
        self.assertEqual(response.status_code, 405)


# --- Admin Tests ---


class EventAdminTest(TestCase):
    """Test admin CRUD for events."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_create_event(self):
        start = timezone.now() + timedelta(days=7)
        self.client.post('/admin/events/event/add/', {
            'title': 'New Event',
            'slug': 'new-event',
            'description': 'A new event',
            'platform': 'zoom',
            'start_datetime_0': start.strftime('%Y-%m-%d'),
            'start_datetime_1': start.strftime('%H:%M:%S'),
            'timezone': 'Europe/Berlin',
            'zoom_meeting_id': '',
            'zoom_join_url': '',
            'location': 'Zoom',
            'tags': '[]',
            'required_level': 0,
            'status': 'draft',
            # Inline formset management data for EventRegistration
            'registrations-TOTAL_FORMS': '0',
            'registrations-INITIAL_FORMS': '0',
            'registrations-MIN_NUM_FORMS': '0',
            'registrations-MAX_NUM_FORMS': '1000',
            # EventInstructor through-model inline (issue #308)
            'eventinstructor_set-TOTAL_FORMS': '0',
            'eventinstructor_set-INITIAL_FORMS': '0',
            'eventinstructor_set-MIN_NUM_FORMS': '0',
            'eventinstructor_set-MAX_NUM_FORMS': '1000',
            # EventFeedback inline (issue #679)
            'feedback-TOTAL_FORMS': '0',
            'feedback-INITIAL_FORMS': '0',
            'feedback-MIN_NUM_FORMS': '0',
            'feedback-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(Event.objects.filter(slug='new-event').count(), 1)

    def test_admin_search(self):
        Event.objects.create(
            title='Searchable Event',
            slug='searchable-event',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        Event.objects.create(
            title='Hidden Event',
            slug='hidden-event',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        response = self.client.get('/admin/events/event/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Event')
        self.assertNotContains(response, 'Hidden Event')


class EventAdminStatusTransitionTest(TestCase):
    """Test admin status transition actions."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_transition_draft_to_upcoming(self):
        event = Event.objects.create(
            title='Draft', slug='draft-transition',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        from events.admin.event import make_upcoming
        make_upcoming(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'upcoming')

    def test_transition_upcoming_to_completed(self):
        event = Event.objects.create(
            title='Upcoming', slug='upcoming-completed',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        from events.admin.event import make_completed
        make_completed(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')

    def test_draft_does_not_transition_to_completed(self):
        event = Event.objects.create(
            title='Draft', slug='draft-completed',
            start_datetime=timezone.now(),
            status='draft',
        )
        from events.admin.event import make_completed
        make_completed(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'draft')

    def test_cancel_from_any_state(self):
        for status in ['draft', 'upcoming', 'completed']:
            event = Event.objects.create(
                title=f'Cancel {status}',
                slug=f'cancel-{status}',
                start_datetime=timezone.now(),
                status=status,
            )
            from events.admin.event import make_cancelled
            make_cancelled(None, None, Event.objects.filter(pk=event.pk))
            event.refresh_from_db()
            self.assertEqual(event.status, 'cancelled')


# --- Events List Registered Badge Test ---


class EventsListRegisteredBadgeTest(TestCase):
    """Test that registered events show a badge on the list page."""

    def test_registered_event_shows_badge(self):
        event = Event.objects.create(
            title='Badge Event',
            slug='badge-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        user = User.objects.create_user(
            email='badge@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='badge@test.com', password='pass')
        response = self.client.get('/events')
        # The registered badge renders with a check icon and "Registered" text
        # inside a green badge span
        self.assertContains(response, 'Registered')
        # Verify the event ID is in the registered set in context
        self.assertIn(event.id, response.context['registered_event_ids'])

    def test_unregistered_event_no_badge(self):
        Event.objects.create(
            title='No Badge Event',
            slug='no-badge-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        User.objects.create_user(
            email='nobadge@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='nobadge@test.com', password='pass')
        response = self.client.get('/events')
        # No registered event IDs in context
        self.assertEqual(len(response.context['registered_event_ids']), 0)
        # The "Registered" badge text should not appear
        self.assertNotContains(response, 'Registered')


# --- Issue #484: improved event detail / registration confirmation ---


class EventDetailCoverImageTest(TestCase):
    """Issue #484 + #651: event detail page renders cover image when
    set, and renders no hero block at all when cover_image_url is
    empty."""

    def test_event_detail_with_cover_renders_image(self):
        event = Event.objects.create(
            title='With Cover',
            slug='with-cover',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            cover_image_url='https://cdn.example.com/cover.jpg',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-cover-image"')
        self.assertContains(response, 'https://cdn.example.com/cover.jpg')
        self.assertNotContains(response, 'data-testid="event-cover-fallback"')

    def test_event_detail_without_cover_renders_no_hero(self):
        """Issue #651: empty cover_image_url renders neither an image
        nor a decorative fallback — the page starts at the back-link
        and title directly."""
        event = Event.objects.create(
            title='No Cover',
            slug='no-cover',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-cover-image"')
        self.assertNotContains(response, 'data-testid="event-cover-fallback"')


class EventDetailInstructorTest(TestCase):
    """Issue #484: event detail page shows speaker / instructor info."""

    def test_instructor_rendered_when_linked(self):
        instructor = Instructor.objects.create(
            instructor_id='ada-lovelace',
            name='Ada Lovelace',
            status='published',
        )
        event = Event.objects.create(
            title='Speaker Event',
            slug='speaker-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        EventInstructor.objects.create(
            event=event, instructor=instructor, position=0,
        )
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-instructors"')
        self.assertContains(response, 'Ada Lovelace')

    def test_no_instructor_block_when_unlinked(self):
        event = Event.objects.create(
            title='No Speaker',
            slug='no-speaker',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, 'data-testid="event-instructors"')


class EventDetailRegisteredConfirmationTest(TierSetupMixin, TestCase):
    """Issue #484: post-registration confirmation surfaces email + ICS + next steps."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title='Confirmation Event',
            slug='confirmation-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        cls.user = User.objects.create_user(
            email='conf@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=cls.event, user=cls.user)

    def setUp(self):
        self.client.login(email='conf@test.com', password='pass')

    def test_registered_confirmation_block_rendered(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(
            response, 'data-testid="event-registered-confirmation"',
        )
        self.assertContains(response, "You're registered!")

    def test_next_steps_mention_email_and_calendar(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-next-steps"')
        self.assertContains(response, 'confirmation to your email')
        self.assertContains(response, 'spam folder')
        self.assertContains(response, '5 minutes before')
        self.assertNotContains(response, '15 minutes before')

    def test_add_to_calendar_button_links_to_ics(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'data-testid="event-add-to-calendar"')
        self.assertContains(response, '/events/confirmation-event/calendar.ics')
        self.assertContains(response, 'Add to calendar')

    def test_cancel_registration_still_present(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertContains(response, 'id="unregister-btn"')
        self.assertContains(response, 'Cancel registration')

    def test_event_ics_url_in_context(self):
        response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(
            response.context['event_ics_url'],
            '/events/confirmation-event/calendar.ics',
        )


class EventCalendarIcsViewTest(TestCase):
    """Issue #484: GET /events/<slug>/calendar.ics returns the .ics file."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='ICS Download Event',
            slug='ics-download-event',
            description='An event to download.',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=2),
            status='upcoming',
        )

    def test_returns_ics_content_type(self):
        response = self.client.get('/events/ics-download-event/calendar.ics')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/calendar', response['Content-Type'])

    def test_returns_attachment_disposition_with_slug_filename(self):
        response = self.client.get('/events/ics-download-event/calendar.ics')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('ics-download-event.ics', response['Content-Disposition'])

    def test_response_body_is_valid_vcalendar(self):
        response = self.client.get('/events/ics-download-event/calendar.ics')
        body = response.content.decode('utf-8')
        self.assertIn('BEGIN:VCALENDAR', body)
        self.assertIn('END:VCALENDAR', body)
        self.assertIn('SUMMARY:ICS Download Event', body)

    def test_response_uses_attendee_join_url(self):
        from icalendar import Calendar

        self.event.zoom_join_url = 'https://zoom.us/j/raw-download'
        self.event.save(update_fields=['zoom_join_url'])

        response = self.client.get('/events/ics-download-event/calendar.ics')
        cal = Calendar.from_ical(response.content)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        join_url = f'https://aishippinglabs.com{self.event.get_join_url()}'

        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertIn(f'Join: {join_url}', str(vevent.get('description')))
        self.assertNotIn('zoom.us', response.content.decode('utf-8'))

    def test_draft_event_returns_404_for_anonymous(self):
        Event.objects.create(
            title='Draft ICS',
            slug='draft-ics',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        response = self.client.get('/events/draft-ics/calendar.ics')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_event_returns_404(self):
        # The .ics download still keys by slug — unknown slug 404s.
        response = self.client.get('/events/no-such-event/calendar.ics')
        self.assertEqual(response.status_code, 404)


class EventDetailAnonymousCopyTest(TestCase):
    """Issue #513: anonymous email-only form copy on free events.

    Replaces the legacy issue #484 copy assertions: anonymous visitors
    on a free upcoming event now see an inline email-only registration
    form. The form copy must explain that an account will be created
    and that the user can unsubscribe at any time.
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Anon Event',
            slug='anon-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )

    def test_form_copy_discloses_account_creation_and_unsubscribe(self):
        response = self.client.get(self.event.get_absolute_url())
        # The form is the new entry point for anonymous registration.
        self.assertContains(response, 'event-anonymous-email-form')
        # Disclose that submitting the form creates an account.
        self.assertContains(response, 'free account')
        # Disclose unsubscribe.
        self.assertContains(response, 'unsubscribe')

    def test_signin_link_preserves_event_path(self):
        response = self.client.get(self.event.get_absolute_url())
        # Returning users keep the sign-in path, with `next` preserved.
        # Issue #673: the next URL is the canonical id+slug shape.
        self.assertContains(
            response,
            f'/accounts/login/?next={self.event.get_absolute_url()}',
        )


# Issue #513 ----------------------------------------------------------------
# Anonymous email-only event registration on free events.


class AnonymousEventRegistrationAPITest(TierSetupMixin, TestCase):
    """Issue #513: ``register_for_event`` accepts anonymous POST with an
    email body on free events. The view auto-creates a free unverified
    User, registers them, and sends both the registration confirmation
    (with ``.ics``) and the standard verification email so the user can
    claim the account.
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Open Community Call',
            slug='open-call',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        cls.gated_event = Event.objects.create(
            title='Main Workshop',
            slug='main-workshop',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )

    def setUp(self):
        # Issue #672: the anon-register view now uses the cache for
        # per-IP / per-email rate limiting. Tests share an in-memory
        # cache backend, so a counter left behind by an earlier case
        # can trip rate limits in this one. Clear once per test.
        from django.core.cache import cache
        cache.clear()

    def _post(self, slug, body, **extra):
        return self.client.post(
            f'/api/events/{slug}/register',
            data=json.dumps(body),
            content_type='application/json',
            **extra,
        )

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_creates_user_and_registers(self, mock_reg_email, mock_verify):
        before = timezone.now()
        resp = self._post('open-call', {'email': 'anon@test.com'})
        after = timezone.now()
        self.assertEqual(resp.status_code, 201)

        body = resp.json()
        self.assertEqual(body['status'], 'registered')
        self.assertEqual(body['event_slug'], 'open-call')
        self.assertTrue(body['account_created'])

        user = User.objects.get(email='anon@test.com')
        self.assertFalse(user.email_verified)
        # Free tier (default) and a populated verification_expires_at so
        # the daily purge job can clean abandoned anonymous-registration
        # rows just like newsletter / signup ones.
        self.assertIsNotNone(user.verification_expires_at)
        lower = before + timedelta(days=7) - timedelta(seconds=5)
        upper = after + timedelta(days=7) + timedelta(seconds=5)
        self.assertGreaterEqual(user.verification_expires_at, lower)
        self.assertLessEqual(user.verification_expires_at, upper)

        # Registration row exists.
        registration = EventRegistration.objects.get(
            event=self.event, user=user,
        )
        self.assertIsNotNone(registration.registered_at)

        # Both the registration confirmation and the verification email
        # are sent. They serve different jobs.
        mock_reg_email.assert_called_once()
        mock_verify.assert_called_once()
        self.assertEqual(mock_verify.call_args[0][0].email, 'anon@test.com')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_with_existing_user_registers_without_resetting(
        self, mock_reg_email, mock_verify,
    ):
        existing = User.objects.create_user(
            email='member@test.com',
            password='realpassword12',
            email_verified=True,
        )
        original_expires = existing.verification_expires_at
        original_password = existing.password
        original_tier = existing.tier_id

        resp = self._post('open-call', {'email': 'member@test.com'})
        self.assertEqual(resp.status_code, 201)

        body = resp.json()
        self.assertFalse(body['account_created'])

        existing.refresh_from_db()
        # Verified state, password, tier all preserved.
        self.assertTrue(existing.email_verified)
        self.assertEqual(existing.password, original_password)
        self.assertEqual(existing.tier_id, original_tier)
        self.assertEqual(existing.verification_expires_at, original_expires)

        # Registration row exists for THIS user — no duplicate created.
        self.assertEqual(
            User.objects.filter(email__iexact='member@test.com').count(), 1,
        )
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.event, user=existing,
            ).exists()
        )

        # Verification email is NOT sent — the user is already verified.
        mock_verify.assert_not_called()
        # Registration confirmation IS sent.
        mock_reg_email.assert_called_once()

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_on_gated_event_returns_403_and_creates_no_user(
        self, mock_reg_email, mock_verify,
    ):
        resp = self._post('main-workshop', {'email': 'gate@test.com'})
        self.assertEqual(resp.status_code, 403)

        # Crucially, no User row is created — anonymous email submission
        # must NOT bypass tier gates.
        self.assertFalse(
            User.objects.filter(email__iexact='gate@test.com').exists()
        )
        self.assertFalse(
            EventRegistration.objects.filter(event=self.gated_event).exists()
        )
        mock_reg_email.assert_not_called()
        mock_verify.assert_not_called()

    def test_anonymous_invalid_email_returns_400(self):
        resp = self._post('open-call', {'email': 'not-an-email'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            User.objects.filter(email__iexact='not-an-email').exists()
        )

    def test_anonymous_without_email_keeps_legacy_401(self):
        """A no-email anonymous POST keeps the historical 401 contract so
        clients that expected a login gate are not silently broken.
        """
        resp = self.client.post('/api/events/open-call/register')
        self.assertEqual(resp.status_code, 401)

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_registration_never_blocked_by_existing_registrations(
        self, mock_reg_email, mock_verify,
    ):
        """Issue #984: capacity removed — an anonymous registrant is never
        turned away (no 410) no matter how many users already registered."""
        for i in range(3):
            other = User.objects.create_user(email=f'other{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=other)

        resp = self._post('open-call', {'email': 'late@test.com'})
        self.assertEqual(resp.status_code, 201)

        # The new unverified account is created and registered.
        self.assertTrue(
            User.objects.filter(email__iexact='late@test.com').exists()
        )
        mock_verify.assert_called_once()

    # --- Issue #672: gap 1 (claim link for existing unverified user) ---

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_existing_unverified_user_receives_claim_link(
        self, mock_reg_email, mock_verify,
    ):
        """Existing-unverified user must ALSO get the claim-account
        magic link, not just the calendar invite. Without this they
        have an account they cannot sign into.
        """
        existing = User.objects.create_user(
            email='unverified@test.com',
            email_verified=False,
        )

        resp = self._post('open-call', {'email': 'unverified@test.com'})
        self.assertEqual(resp.status_code, 201)

        body = resp.json()
        self.assertFalse(body['account_created'])

        mock_reg_email.assert_called_once()
        # The claim link goes out alongside the calendar invite.
        mock_verify.assert_called_once()
        self.assertEqual(mock_verify.call_args[0][0].pk, existing.pk)

    # --- Issue #672: gap 2 (preferred_timezone capture) ---

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_register_stores_browser_timezone(
        self, mock_reg_email, mock_verify,
    ):
        resp = self._post(
            'open-call',
            {'email': 'tz1@test.com', 'timezone': 'Europe/Berlin'},
        )
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email='tz1@test.com')
        self.assertEqual(user.preferred_timezone, 'Europe/Berlin')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_register_invalid_timezone_falls_back_to_empty(
        self, mock_reg_email, mock_verify,
    ):
        """Invalid TZ must NOT reject the request — fall back to '' so
        the email helper uses UTC. The user still gets registered.
        """
        resp = self._post(
            'open-call',
            {'email': 'tz2@test.com', 'timezone': 'Not/A/Zone'},
        )
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email='tz2@test.com')
        self.assertEqual(user.preferred_timezone, '')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_register_missing_timezone_key_succeeds(
        self, mock_reg_email, mock_verify,
    ):
        """Legacy clients that don't send the ``timezone`` key still
        register cleanly. ``preferred_timezone`` defaults to ''.
        """
        resp = self._post('open-call', {'email': 'tz3@test.com'})
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email='tz3@test.com')
        self.assertEqual(user.preferred_timezone, '')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_existing_user_with_empty_timezone_gets_backfilled(
        self, mock_reg_email, mock_verify,
    ):
        existing = User.objects.create_user(
            email='tz4@test.com', email_verified=True,
        )
        self.assertEqual(existing.preferred_timezone, '')

        resp = self._post(
            'open-call',
            {'email': 'tz4@test.com', 'timezone': 'America/New_York'},
        )
        self.assertEqual(resp.status_code, 201)
        existing.refresh_from_db()
        self.assertEqual(existing.preferred_timezone, 'America/New_York')

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_existing_user_with_timezone_is_preserved(
        self, mock_reg_email, mock_verify,
    ):
        """A non-empty existing ``preferred_timezone`` must NOT be
        overwritten by a new anonymous submit — the existing setting
        is canonical (it likely came from the account settings page).
        """
        existing = User.objects.create_user(
            email='tz5@test.com',
            email_verified=True,
            preferred_timezone='Asia/Tokyo',
        )

        resp = self._post(
            'open-call',
            {'email': 'tz5@test.com', 'timezone': 'America/New_York'},
        )
        self.assertEqual(resp.status_code, 201)
        existing.refresh_from_db()
        self.assertEqual(existing.preferred_timezone, 'Asia/Tokyo')

    # --- Issue #672: gap 3 (rate limiting) ---

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_ip_rate_limit_blocks_sixth_request(
        self, mock_reg_email, mock_verify,
    ):
        ip = '203.0.113.7'
        # Five distinct emails from the same IP must succeed.
        for i in range(5):
            resp = self._post(
                'open-call',
                {'email': f'ratelimit{i}@test.com'},
                REMOTE_ADDR=ip,
            )
            self.assertEqual(resp.status_code, 201, msg=f'request {i}')

        # The sixth request from the same IP is rate-limited regardless
        # of the email used.
        resp = self._post(
            'open-call',
            {'email': 'ratelimit-extra@test.com'},
            REMOTE_ADDR=ip,
        )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(
            resp.json()['error'],
            'Too many registration attempts. Please try again in a few '
            'minutes.',
        )
        # No user created for the blocked submission.
        self.assertFalse(
            User.objects.filter(
                email__iexact='ratelimit-extra@test.com',
            ).exists()
        )

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_anonymous_email_rate_limit_blocks_fourth_request(
        self, mock_reg_email, mock_verify,
    ):
        # Use a unique IP per attempt so the IP gate doesn't fire.
        for i in range(3):
            resp = self._post(
                'open-call',
                {'email': 'repeat@test.com'},
                REMOTE_ADDR=f'198.51.100.{i + 10}',
            )
            # First request creates the user + registration; subsequent
            # ones return 201 with already_registered=True.
            self.assertEqual(resp.status_code, 201, msg=f'request {i}')

        resp = self._post(
            'open-call',
            {'email': 'repeat@test.com'},
            REMOTE_ADDR='198.51.100.99',
        )
        self.assertEqual(resp.status_code, 429)

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_authenticated_register_is_not_rate_limited(
        self, mock_reg_email, mock_verify,
    ):
        """Rate limit is only applied on the anonymous email-submit
        branch. Authenticated POSTs hit a different code path and must
        be unaffected.
        """
        user = User.objects.create_user(
            email='auth-rl@test.com',
            password='realpassword12',
            email_verified=True,
        )
        self.client.force_login(user)

        # First authenticated POST registers the user.
        resp = self.client.post('/api/events/open-call/register')
        self.assertEqual(resp.status_code, 201)

        # Spam another five POSTs from the same IP. They keep hitting
        # the auth-side 409 (already registered), NOT the 429.
        for _ in range(5):
            resp = self.client.post('/api/events/open-call/register')
            self.assertEqual(resp.status_code, 409)
            self.assertEqual(resp.json()['error'], 'Already registered')

    def test_anonymous_gated_event_does_not_consume_rate_limit_slot(self):
        """Gated-event 403 must short-circuit BEFORE the rate-limit
        check so bots probing gated events cannot exhaust a legitimate
        visitor's quota on the same IP.
        """
        ip = '192.0.2.55'
        # Hit the gated endpoint six times — all should 403, NONE
        # should consume an IP-bucket slot.
        for _ in range(6):
            resp = self._post(
                'main-workshop',
                {'email': 'probe@test.com'},
                REMOTE_ADDR=ip,
            )
            self.assertEqual(resp.status_code, 403)

        # A free-event submit from the same IP still succeeds — the
        # IP gate has not been touched by the probes above.
        resp = self._post(
            'open-call',
            {'email': 'real-visitor@test.com'},
            REMOTE_ADDR=ip,
        )
        self.assertEqual(resp.status_code, 201)

    # --- Issue #672: gap 4 (idempotent duplicate submit) ---

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_duplicate_anonymous_submit_returns_201_already_registered(
        self, mock_reg_email, mock_verify,
    ):
        """Duplicate anonymous submit by the same email returns 201
        with ``already_registered: true`` — not 409. No second
        registration row is created and no second email is sent.
        """
        # First submit creates the user + registration.
        resp = self._post(
            'open-call',
            {'email': 'dup@test.com'},
            REMOTE_ADDR='198.51.100.1',
        )
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(email='dup@test.com')

        # Reset mocks so we can assert the duplicate path sends nothing.
        mock_reg_email.reset_mock()
        mock_verify.reset_mock()

        # Second submit — same email, different IP so the IP gate is
        # not the thing tripping us.
        resp = self._post(
            'open-call',
            {'email': 'dup@test.com'},
            REMOTE_ADDR='198.51.100.2',
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['status'], 'registered')
        self.assertFalse(body['account_created'])
        self.assertTrue(body['already_registered'])
        self.assertEqual(body['event_slug'], 'open-call')

        # Exactly one registration row.
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=user,
            ).count(),
            1,
        )

        # No second registration email and no second verification email.
        mock_reg_email.assert_not_called()
        mock_verify.assert_not_called()


class EventDetailAnonymousFlowTest(TestCase):
    """Issue #513: event detail page surfaces the email-only form for
    free events and the post-registration confirmation block when the
    page is loaded with ``?registered=<email>``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Open Community Call',
            slug='open-call-detail',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )

    def test_form_visible_to_anonymous_on_free_event(self):
        resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(resp, 'event-anonymous-email-form')
        self.assertContains(resp, 'id="event-anon-email"')
        self.assertContains(resp, 'Register for this event')

    def test_form_hidden_for_gated_event(self):
        gated_event = Event.objects.create(
            title='Gated',
            slug='gated-detail',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )
        resp = self.client.get(gated_event.get_absolute_url())
        self.assertNotContains(resp, 'event-anonymous-email-form')
        # Falls back to the tier-aware anonymous CTA (issue #671).
        self.assertContains(resp, 'event-anonymous-cta')
        # The misleading "free account is required" copy is gone.
        self.assertNotContains(resp, 'free account is required')
        # New tier-aware copy names the required tier and links to /pricing.
        self.assertContains(resp, 'This event is for Main members')
        self.assertContains(
            resp, 'Registering for this event requires a Main membership or above',
        )
        self.assertContains(resp, 'View membership options')

    def test_confirmation_block_renders_for_registered_query_param(self):
        resp = self.client.get(
            self.event.get_absolute_url()
            + '?registered=anon%40test.com&account_created=1',
        )
        self.assertContains(resp, 'event-anonymous-registered-confirmation')
        # Email used is surfaced in the confirmation block.
        self.assertContains(response=resp, text='anon@test.com')
        # Calendar download is offered — independent of email delivery.
        self.assertContains(resp, 'event-anonymous-add-to-calendar')
        self.assertContains(resp, '/events/open-call-detail/calendar.ics')
        # "Sign in to manage your registration" link is visible.
        self.assertContains(resp, 'event-anonymous-manage-link')
        self.assertContains(resp, 'Sign in to manage your registration')
        # Account-created copy is shown when account_created=1.
        self.assertContains(resp, 'verification link')

    def test_confirmation_block_skipped_for_junk_query_param(self):
        resp = self.client.get(
            self.event.get_absolute_url() + '?registered=1',
        )
        # ``?registered=1`` is junk (not an email); template should fall
        # back to the regular form, not the confirmation block.
        self.assertNotContains(
            resp, 'event-anonymous-registered-confirmation',
        )
        self.assertContains(resp, 'event-anonymous-email-form')


class EventAnonymousPaidCopyTest(TestCase):
    """Issue #671: the anonymous CTA on a tier-gated upcoming event must
    name the required tier and point at /pricing. The misleading "free
    account is required" copy must be gone.
    """

    @classmethod
    def setUpTestData(cls):
        cls.basic_event = Event.objects.create(
            title='Basic-tier event',
            slug='basic-paid-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_BASIC,
        )
        cls.main_event = Event.objects.create(
            title='Main-tier event',
            slug='main-paid-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )
        cls.premium_event = Event.objects.create(
            title='Premium-tier event',
            slug='premium-paid-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_PREMIUM,
        )

    def test_basic_event_anonymous_cta_names_tier(self):
        resp = self.client.get(self.basic_event.get_absolute_url())
        self.assertContains(resp, 'data-testid="event-anonymous-cta"')
        self.assertContains(resp, 'This event is for Basic members')
        self.assertContains(
            resp,
            'Registering for this event requires a Basic membership or above',
        )
        # The misleading copy is gone.
        self.assertNotContains(resp, 'free account is required')
        self.assertNotContains(resp, 'A free account is required to register')

    def test_main_event_anonymous_cta_names_tier(self):
        resp = self.client.get(self.main_event.get_absolute_url())
        self.assertContains(resp, 'This event is for Main members')
        self.assertContains(
            resp,
            'Registering for this event requires a Main membership or above',
        )
        self.assertNotContains(resp, 'free account is required')

    def test_premium_event_anonymous_cta_drops_or_above(self):
        resp = self.client.get(self.premium_event.get_absolute_url())
        self.assertContains(resp, 'This event is for Premium members')
        self.assertContains(
            resp,
            'Registering for this event requires a Premium membership.',
        )
        # Premium is the highest tier — "or above" must NOT appear.
        self.assertNotContains(resp, 'Premium membership or above')

    def test_anonymous_paid_cta_has_pricing_link(self):
        resp = self.client.get(self.main_event.get_absolute_url())
        html = resp.content.decode()
        # The primary CTA is "View membership options" linking to /pricing.
        self.assertIn(
            'data-testid="event-anonymous-pricing-cta"', html,
        )
        self.assertIn('href="/pricing"', html)
        self.assertIn('View membership options', html)

    def test_anonymous_paid_cta_has_signin_link_preserving_next(self):
        resp = self.client.get(self.main_event.get_absolute_url())
        html = resp.content.decode()
        # Secondary CTA: "Sign in" preserving ?next= to the event URL.
        # Issue #673: ``next`` carries the canonical id+slug URL.
        self.assertIn(
            'data-testid="event-anonymous-signin-cta"', html,
        )
        self.assertIn(
            f'/accounts/login/?next={self.main_event.get_absolute_url()}',
            html,
        )

    def test_anonymous_paid_cta_does_not_offer_signup(self):
        """Issue #671: the new copy does not push anonymous visitors to
        create a free account on a paid event — that was the bug."""
        resp = self.client.get(self.main_event.get_absolute_url())
        html = resp.content.decode()
        # The legacy "Create free account" CTA pointing at /accounts/signup
        # must be gone for tier-gated events. (The /accounts/login link
        # for the "Sign in" CTA is allowed.)
        self.assertNotIn('/accounts/signup/', html)
        self.assertNotIn('Create free account', html)

    def test_template_source_has_no_free_account_string(self):
        """Acceptance criterion: the literal "free account is required"
        substring must not appear anywhere in the registration card."""
        from pathlib import Path

        template_path = (
            Path(__file__).resolve().parent.parent.parent
            / 'templates' / 'events' / '_event_registration_card.html'
        )
        body = template_path.read_text()
        self.assertNotIn('free account is required', body)

    def test_free_event_anonymous_keeps_email_form(self):
        """Regression check: anonymous visitor on a free event must still
        see the inline email-only signup form (unchanged from issue #513).
        """
        free_event = Event.objects.create(
            title='Free event',
            slug='free-event-regression',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_OPEN,
        )
        resp = self.client.get(free_event.get_absolute_url())
        self.assertContains(resp, 'event-anonymous-email-form')
        # The new paid-event CTA must not leak into free events.
        self.assertNotContains(resp, 'event-anonymous-cta')
        self.assertNotContains(resp, 'This event is for')


class EventUnderTierCopyConsistencyTest(TierSetupMixin, TestCase):
    """Issue #671: the authenticated-under-tier copy must phrase the
    requirement identically to the anonymous-on-paid copy so users see
    one consistent message regardless of auth state.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main_event = Event.objects.create(
            title='Main event',
            slug='under-tier-main',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )
        cls.premium_event = Event.objects.create(
            title='Premium event',
            slug='under-tier-premium',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_PREMIUM,
        )

    def _login_at(self, tier):
        user = User.objects.create_user(
            email=f'u-{tier.slug}@test.com',
            password='pass',
            email_verified=True,
        )
        user.tier = tier
        user.save()
        self.client.login(email=user.email, password='pass')
        return user

    def test_free_user_on_main_event_sees_registering_copy(self):
        self._login_at(self.free_tier)
        response = self.client.get(self.main_event.get_absolute_url())
        self.assertContains(response, 'Upgrade to Main to attend')
        self.assertContains(
            response,
            'Registering for this event requires a Main membership or above.',
        )
        self.assertNotContains(response, 'free account')
        self.assertContains(response, 'href="/pricing"')

    def test_basic_user_on_premium_event_drops_or_above(self):
        self._login_at(self.basic_tier)
        response = self.client.get(self.premium_event.get_absolute_url())
        self.assertContains(response, 'Upgrade to Premium to attend')
        self.assertContains(
            response,
            'Registering for this event requires a Premium membership.',
        )
        self.assertNotContains(response, 'Premium membership or above')
