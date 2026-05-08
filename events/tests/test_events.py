"""Tests for Events and Calendar - issue #83.

Covers:
- Event model fields, defaults, and constraints
- EventRegistration model with unique_together constraint
- Description markdown rendering on save
- Spots remaining and is_full properties
- Zoom link visibility (15 min before start)
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

from content.access import LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from content.models import Instructor
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
        self.assertIsNone(event.max_participants)
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
            max_participants=50,
            status='upcoming',
        )
        self.assertEqual(event.timezone, 'Europe/Berlin')
        self.assertEqual(event.zoom_meeting_id, '123456789')
        self.assertEqual(event.location, 'Zoom')
        self.assertEqual(event.tags, ['python', 'django'])
        self.assertEqual(event.required_level, LEVEL_MAIN)
        self.assertEqual(event.max_participants, 50)
        self.assertEqual(event.status, 'upcoming')

    def test_get_absolute_url(self):
        event = Event(slug='my-event')
        self.assertEqual(event.get_absolute_url(), '/events/my-event')

    def test_ordering_by_start_datetime_desc(self):
        Event.objects.create(
            title='Old', slug='old',
            start_datetime=timezone.now() - timedelta(days=10),
        )
        Event.objects.create(
            title='New', slug='new',
            start_datetime=timezone.now(),
        )
        events = list(Event.objects.all())
        self.assertEqual(events[0].slug, 'new')
        self.assertEqual(events[1].slug, 'old')

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


class EventSpotsTest(TestCase):
    """Test spots_remaining and is_full properties."""

    def test_spots_remaining_with_max_participants(self):
        event = Event.objects.create(
            title='Capped', slug='capped',
            start_datetime=timezone.now(),
            max_participants=10,
            status='upcoming',
        )
        self.assertEqual(event.spots_remaining, 10)
        self.assertFalse(event.is_full)

    def test_spots_remaining_none_when_unlimited(self):
        event = Event.objects.create(
            title='Unlimited', slug='unlimited',
            start_datetime=timezone.now(),
        )
        self.assertIsNone(event.spots_remaining)
        self.assertFalse(event.is_full)

    def test_is_full_when_at_capacity(self):
        event = Event.objects.create(
            title='Full', slug='full-event',
            start_datetime=timezone.now(),
            max_participants=1,
            status='upcoming',
        )
        user = User.objects.create_user(email='user@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.assertTrue(event.is_full)
        self.assertEqual(event.spots_remaining, 0)


class EventZoomLinkTest(TestCase):
    """Test can_show_zoom_link method."""

    def test_zoom_link_visible_within_15_minutes(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() + timedelta(minutes=10),
        )
        self.assertTrue(event.can_show_zoom_link())

    def test_zoom_link_not_visible_more_than_15_min_before(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() + timedelta(minutes=30),
        )
        self.assertFalse(event.can_show_zoom_link())

    def test_zoom_link_visible_after_start(self):
        event = Event(
            zoom_join_url='https://zoom.us/j/123',
            start_datetime=timezone.now() - timedelta(minutes=5),
        )
        self.assertTrue(event.can_show_zoom_link())

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

    def test_events_list_status_200(self):
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)

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
        self.assertContains(response, self.upcoming_event.formatted_date())
        self.assertNotContains(response, self.upcoming_event.formatted_time())

    def test_event_card_shows_location(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Zoom')


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


class EventsListSpotsRemainingTest(TestCase):
    """Test spots remaining on events list."""

    def test_shows_spots_remaining(self):
        Event.objects.create(
            title='Limited Event',
            slug='limited-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            max_participants=20,
        )
        response = self.client.get('/events')
        self.assertContains(response, '20 spots remaining')

    def test_shows_full_when_at_capacity(self):
        event = Event.objects.create(
            title='Full Event',
            slug='full-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            max_participants=1,
        )
        user = User.objects.create_user(email='u@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        response = self.client.get('/events')
        self.assertContains(response, 'Event is full')

    def test_no_spots_display_for_unlimited(self):
        Event.objects.create(
            title='Unlimited Event',
            slug='unlimited-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'spots remaining')
        self.assertNotContains(response, 'Event is full')


class EventsListRecordingLinkTest(TestCase):
    """Test past events show the recording indicator on the default /events view."""

    def test_completed_event_with_recording_shows_indicator(self):
        Event.objects.create(
            title='Recorded Event',
            slug='recorded-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        response = self.client.get('/events')
        # The card itself already links to /events/<slug>. The past section
        # shows a small "Recording available" indicator for such events.
        self.assertContains(response, 'Recording available')
        self.assertContains(response, '/events/recorded-event')
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

    def test_detail_status_200(self):
        response = self.client.get('/events/detail-event')
        self.assertEqual(response.status_code, 200)

    def test_detail_template(self):
        response = self.client.get('/events/detail-event')
        self.assertTemplateUsed(response, 'events/event_detail.html')

    def test_shows_title(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'Detail Event')

    def test_shows_description(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'A detailed description of the event.')

    def test_shows_location(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'Zoom')

    def test_shows_timezone(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'Europe/Berlin')

    def test_omits_event_type(self):
        response = self.client.get('/events/detail-event')
        self.assertNotContains(response, 'Live Event')
        self.assertNotContains(response, 'Async Event')

    def test_shows_tags(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'python')
        self.assertContains(response, 'agents')

    def test_title_tag_format(self):
        response = self.client.get('/events/detail-event')
        content = response.content.decode()
        self.assertIn('<title>Detail Event | AI Shipping Labs</title>', content)

    def test_404_for_nonexistent_slug(self):
        response = self.client.get('/events/nonexistent')
        self.assertEqual(response.status_code, 404)

    def test_draft_event_404_for_anonymous(self):
        Event.objects.create(
            title='Draft Event', slug='draft-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        response = self.client.get('/events/draft-event')
        self.assertEqual(response.status_code, 404)

    def test_draft_event_visible_to_staff(self):
        Event.objects.create(
            title='Draft Event', slug='draft-event-staff',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        User.objects.create_superuser(
            email='admin@test.com', password='pass',
        )
        self.client.login(email='admin@test.com', password='pass')
        response = self.client.get('/events/draft-event-staff')
        self.assertEqual(response.status_code, 200)

    def test_back_link_to_events(self):
        response = self.client.get('/events/detail-event')
        content = response.content.decode()
        self.assertIn('href="/events"', content)

    def test_anonymous_sees_email_only_registration_form(self):
        """Issue #513: anonymous visitors on a free upcoming event see an
        inline email-only registration form, not the legacy "Sign in /
        Create free account" button pair. The "Already have an account?
        Sign in" link below the form preserves the sign-in path for
        returning users.
        """
        response = self.client.get('/events/detail-event')
        # The new email-only form replaces the old sign-in CTA on free
        # events (required_level == 0).
        self.assertContains(response, 'event-anonymous-email-form')
        self.assertContains(response, 'id="event-anon-email"')
        self.assertContains(response, 'Register for this event')
        # The "Already have an account?" sign-in link still preserves
        # the next URL for returning users.
        self.assertContains(
            response, '/accounts/login/?next=/events/detail-event',
        )
        # The legacy "Create free account" CTA is gone for free events.
        self.assertNotContains(response, 'Create free account')


class EventDetailRecordingRemovedTest(TestCase):
    """Issue #426: event detail no longer renders inline recording UI.

    Completed events with recording fields populated must not embed a
    video player, materials list, transcript, or chapters on the event
    detail page. Recording playback lives on the linked Workshop's
    landing/video pages.
    """

    def test_completed_with_recording_omits_inline_block(self):
        Event.objects.create(
            title='Completed Event',
            slug='completed-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            timestamps=[{'time_seconds': 0, 'label': 'Welcome'}],
            materials=[
                {'title': 'Slides', 'url': 'https://example.com/slides.pdf'}
            ],
            transcript_text='Event transcript text.',
        )
        response = self.client.get('/events/completed-event')
        # No inline recording block markers anywhere on the page.
        self.assertNotContains(response, 'data-testid="event-recording-block"')
        self.assertNotContains(response, 'data-testid="video-chapters"')
        self.assertNotContains(response, 'class="video-timestamp')
        self.assertNotContains(response, 'data-source="youtube"')
        self.assertNotContains(
            response, 'data-testid="recording-materials"',
        )
        self.assertNotContains(response, 'https://example.com/slides.pdf')
        self.assertNotContains(
            response, 'data-testid="recording-transcript"',
        )
        self.assertNotContains(response, 'Event transcript text.')
        # No link out to the retired standalone recording surface either.
        self.assertNotContains(response, '/event-recordings/')

    def test_completed_without_recording_no_block(self):
        Event.objects.create(
            title='No Rec Event',
            slug='no-rec-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        response = self.client.get('/events/no-rec-event')
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
        response = self.client.get('/events/open-event')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Event')

    def test_anonymous_sees_gated_event_with_upgrade_cta(self):
        """Detail page is always visible, but registration is gated."""
        response = self.client.get('/events/gated-event')
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
        response = self.client.get('/events/gated-event')
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
        response = self.client.get('/events/gated-event')
        self.assertContains(response, 'Upgrade to Main to attend')
        # Issue #481: Main event copy still uses "or above" because Main
        # is not the highest tier.
        self.assertContains(
            response, 'This event requires a Main membership or above.',
        )

    def test_premium_event_drops_or_above_suffix(self):
        """Issue #481 AC: Premium-only CTAs do not say "or above".

        Premium is the highest public tier so "Premium membership or
        above" is misleading — there is no higher tier to upgrade to.
        """
        Event.objects.create(
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
        response = self.client.get('/events/premium-event')
        self.assertContains(response, 'Upgrade to Premium to attend')
        self.assertContains(
            response, 'This event requires a Premium membership.',
        )
        self.assertNotContains(response, 'Premium membership or above')
        # And the lock badge in the header is "Premium" (no "+").
        self.assertNotContains(response, 'Premium+')

    def test_full_event_shows_full_message(self):
        event = Event.objects.create(
            title='Full Event',
            slug='full-event-detail',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            max_participants=1,
        )
        other_user = User.objects.create_user(email='other@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=other_user)

        User.objects.create_user(
            email='viewer@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='viewer@test.com', password='pass')
        response = self.client.get('/events/full-event-detail')
        self.assertContains(response, 'Event is full')


class EventDetailZoomLinkTest(TierSetupMixin, TestCase):
    """Test Zoom join link display on event detail."""

    def test_zoom_link_shown_when_registered_and_within_15_min(self):
        event = Event.objects.create(
            title='Soon Event',
            slug='soon-event',
            start_datetime=timezone.now() + timedelta(minutes=10),
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
        response = self.client.get('/events/soon-event')
        self.assertContains(response, '/events/soon-event/join')

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
        response = self.client.get('/events/far-event')
        self.assertNotContains(response, '/events/far-event/join')

    def test_zoom_link_not_shown_when_not_registered(self):
        Event.objects.create(
            title='Not Reg Event',
            slug='not-reg-event',
            start_datetime=timezone.now() + timedelta(minutes=10),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/111111',
        )
        User.objects.create_user(
            email='notreg@test.com',
            password='pass',
            email_verified=True,
        )
        self.client.login(email='notreg@test.com', password='pass')
        response = self.client.get('/events/not-reg-event')
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
        response = self.client.get('/events/reg-status-event')
        self.assertContains(response, "You're registered!")

    def test_unregistered_user_sees_register_button(self):
        Event.objects.create(
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
        response = self.client.get('/events/unreg-event')
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

    def test_register_full_event(self):
        self.event.max_participants = 1
        self.event.save()
        other_user = User.objects.create_user(email='other@test.com', password='pass')
        EventRegistration.objects.create(event=self.event, user=other_user)
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/api-event/register')
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertEqual(data['error'], 'Event is full')

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
    """Issue #484: event detail page renders cover image with fallback."""

    def test_cover_image_renders_when_set(self):
        Event.objects.create(
            title='With Cover',
            slug='with-cover',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            cover_image_url='https://cdn.example.com/cover.jpg',
        )
        response = self.client.get('/events/with-cover')
        self.assertContains(response, 'data-testid="event-cover-image"')
        self.assertContains(response, 'https://cdn.example.com/cover.jpg')
        self.assertNotContains(response, 'data-testid="event-cover-fallback"')

    def test_decorative_fallback_renders_when_no_cover(self):
        Event.objects.create(
            title='No Cover',
            slug='no-cover',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/events/no-cover')
        self.assertContains(response, 'data-testid="event-cover-fallback"')
        self.assertNotContains(response, 'data-testid="event-cover-image"')


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
        response = self.client.get('/events/speaker-event')
        self.assertContains(response, 'data-testid="event-instructors"')
        self.assertContains(response, 'Ada Lovelace')

    def test_no_instructor_block_when_unlinked(self):
        Event.objects.create(
            title='No Speaker',
            slug='no-speaker',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/events/no-speaker')
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
        response = self.client.get('/events/confirmation-event')
        self.assertContains(
            response, 'data-testid="event-registered-confirmation"',
        )
        self.assertContains(response, "You're registered!")

    def test_next_steps_mention_email_and_calendar(self):
        response = self.client.get('/events/confirmation-event')
        self.assertContains(response, 'data-testid="event-next-steps"')
        self.assertContains(response, 'confirmation to your email')
        self.assertContains(response, 'spam folder')
        self.assertContains(response, '15 minutes before')

    def test_add_to_calendar_button_links_to_ics(self):
        response = self.client.get('/events/confirmation-event')
        self.assertContains(response, 'data-testid="event-add-to-calendar"')
        self.assertContains(response, '/events/confirmation-event/calendar.ics')
        self.assertContains(response, 'Add to calendar')

    def test_cancel_registration_still_present(self):
        response = self.client.get('/events/confirmation-event')
        self.assertContains(response, 'id="unregister-btn"')
        self.assertContains(response, 'Cancel registration')

    def test_event_ics_url_in_context(self):
        response = self.client.get('/events/confirmation-event')
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

    def test_anonymous_user_can_download_ics(self):
        # Public download — registered users typically use this, but the
        # detail page is also public so the .ics is too.
        response = self.client.get('/events/ics-download-event/calendar.ics')
        self.assertEqual(response.status_code, 200)

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
        response = self.client.get('/events/anon-event')
        # The form is the new entry point for anonymous registration.
        self.assertContains(response, 'event-anonymous-email-form')
        # Disclose that submitting the form creates an account.
        self.assertContains(response, 'free account')
        # Disclose unsubscribe.
        self.assertContains(response, 'unsubscribe')

    def test_signin_link_preserves_event_path(self):
        response = self.client.get('/events/anon-event')
        # Returning users keep the sign-in path, with `next` preserved.
        self.assertContains(
            response, '/accounts/login/?next=/events/anon-event',
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

    def _post(self, slug, body):
        return self.client.post(
            f'/api/events/{slug}/register',
            data=json.dumps(body),
            content_type='application/json',
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
    def test_anonymous_full_event_returns_410_no_user_created(
        self, mock_reg_email, mock_verify,
    ):
        self.event.max_participants = 1
        self.event.save()
        # Saturate the event with another user.
        other = User.objects.create_user(email='other@test.com')
        EventRegistration.objects.create(event=self.event, user=other)

        resp = self._post('open-call', {'email': 'late@test.com'})
        self.assertEqual(resp.status_code, 410)

        # Don't leak orphan unverified accounts when registration would
        # have failed anyway.
        self.assertFalse(
            User.objects.filter(email__iexact='late@test.com').exists()
        )
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
        resp = self.client.get('/events/open-call-detail')
        self.assertContains(resp, 'event-anonymous-email-form')
        self.assertContains(resp, 'id="event-anon-email"')
        self.assertContains(resp, 'Register for this event')

    def test_form_hidden_for_gated_event(self):
        Event.objects.create(
            title='Gated',
            slug='gated-detail',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            required_level=LEVEL_MAIN,
        )
        resp = self.client.get('/events/gated-detail')
        self.assertNotContains(resp, 'event-anonymous-email-form')
        # Falls back to the existing tier-upgrade CTA.
        self.assertContains(resp, 'event-anonymous-cta')
        self.assertContains(resp, 'Sign in to register')

    def test_confirmation_block_renders_for_registered_query_param(self):
        resp = self.client.get(
            '/events/open-call-detail?registered=anon%40test.com&account_created=1',
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
        resp = self.client.get('/events/open-call-detail?registered=1')
        # ``?registered=1`` is junk (not an email); template should fall
        # back to the regular form, not the confirmation block.
        self.assertNotContains(
            resp, 'event-anonymous-registered-confirmation',
        )
        self.assertContains(resp, 'event-anonymous-email-form')
