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

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC, LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Recording
from events.models import Event, EventRegistration
from payments.models import Tier

User = get_user_model()


class TierSetupMixin:
    """Mixin that creates the four standard tiers."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )
        cls.main_tier, _ = Tier.objects.get_or_create(
            slug='main', defaults={'name': 'Main', 'level': 20},
        )
        cls.premium_tier, _ = Tier.objects.get_or_create(
            slug='premium', defaults={'name': 'Premium', 'level': 30},
        )


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
        self.assertEqual(event.event_type, 'live')
        self.assertEqual(event.status, 'draft')
        self.assertEqual(event.description, '')
        self.assertEqual(event.tags, [])
        self.assertEqual(event.required_level, 0)
        self.assertIsNone(event.max_participants)
        self.assertIsNone(event.recording)
        self.assertIsNotNone(event.created_at)
        self.assertIsNotNone(event.updated_at)

    def test_create_full_event(self):
        event = Event.objects.create(
            title='Full Event',
            slug='full-event',
            description='# Hello\n\nThis is a test.',
            event_type='async',
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
        self.assertEqual(event.event_type, 'async')
        self.assertEqual(event.timezone, 'Europe/Berlin')
        self.assertEqual(event.zoom_meeting_id, '123456789')
        self.assertEqual(event.location, 'Zoom')
        self.assertEqual(event.tags, ['python', 'django'])
        self.assertEqual(event.required_level, LEVEL_MAIN)
        self.assertEqual(event.max_participants, 50)
        self.assertEqual(event.status, 'upcoming')

    def test_slug_unique(self):
        Event.objects.create(
            title='First', slug='unique-slug',
            start_datetime=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            Event.objects.create(
                title='Second', slug='unique-slug',
                start_datetime=timezone.now(),
            )

    def test_str(self):
        event = Event(title='My Event')
        self.assertEqual(str(event), 'My Event')

    def test_get_absolute_url(self):
        event = Event(slug='my-event')
        self.assertEqual(event.get_absolute_url(), '/events/my-event')

    def test_ordering_by_start_datetime_desc(self):
        e1 = Event.objects.create(
            title='Old', slug='old',
            start_datetime=timezone.now() - timedelta(days=10),
        )
        e2 = Event.objects.create(
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

    def test_unique_together_constraint(self):
        EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        with self.assertRaises(IntegrityError):
            EventRegistration.objects.create(
                event=self.event, user=self.user,
            )

    def test_registration_count(self):
        self.assertEqual(self.event.registration_count, 0)
        EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        self.assertEqual(self.event.registration_count, 1)

    def test_str(self):
        reg = EventRegistration(event=self.event, user=self.user)
        self.assertEqual(str(reg), f'{self.user} - {self.event}')


# --- Events List Page Tests ---


class EventsListPageTest(TestCase):
    """Test GET /events shows Upcoming and Past sections."""

    def setUp(self):
        self.client = Client()
        self.upcoming_event = Event.objects.create(
            title='Upcoming Workshop',
            slug='upcoming-workshop',
            description='An upcoming event',
            event_type='live',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            location='Zoom',
        )
        self.past_event = Event.objects.create(
            title='Past Workshop',
            slug='past-workshop',
            description='A past event',
            event_type='live',
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

    def test_event_card_shows_type_badge(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Live')

    def test_event_card_shows_date(self):
        response = self.client.get('/events')
        self.assertContains(response, 'UTC')

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
        self.assertContains(response, 'Premium')
        self.assertContains(response, 'data-lucide="lock"')

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
    """Test past events show recording link if recording is set."""

    def test_completed_event_with_recording_shows_link(self):
        recording = Recording.objects.create(
            title='Event Recording',
            slug='event-recording',
            date=date(2025, 7, 1),
            published=True,
        )
        Event.objects.create(
            title='Recorded Event',
            slug='recorded-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording=recording,
        )
        response = self.client.get('/events')
        self.assertContains(response, 'Watch recording')
        self.assertContains(response, '/event-recordings/event-recording')

    def test_completed_event_without_recording_no_link(self):
        Event.objects.create(
            title='No Recording Event',
            slug='no-recording-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        response = self.client.get('/events')
        self.assertNotContains(response, 'Watch recording')


# --- Event Detail Page Tests ---


class EventDetailPageTest(TestCase):
    """Test GET /events/{slug} detail page."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Detail Event',
            slug='detail-event',
            description='A detailed description of the event.',
            event_type='live',
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

    def test_shows_event_type(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'Live')

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
        admin = User.objects.create_superuser(
            email='admin@test.com', password='pass',
        )
        self.client.login(email='admin@test.com', password='pass')
        response = self.client.get('/events/draft-event-staff')
        self.assertEqual(response.status_code, 200)

    def test_back_link_to_events(self):
        response = self.client.get('/events/detail-event')
        content = response.content.decode()
        self.assertIn('href="/events"', content)

    def test_anonymous_sees_sign_in_cta(self):
        response = self.client.get('/events/detail-event')
        self.assertContains(response, 'Sign in to register')


class EventDetailRecordingLinkTest(TestCase):
    """Test completed event shows recording link on detail page."""

    def test_completed_with_recording_shows_link(self):
        recording = Recording.objects.create(
            title='Event Rec',
            slug='event-rec',
            date=date(2025, 7, 1),
            published=True,
        )
        event = Event.objects.create(
            title='Completed Event',
            slug='completed-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording=recording,
        )
        response = self.client.get('/events/completed-event')
        self.assertContains(response, 'Watch the recording')
        self.assertContains(response, '/event-recordings/event-rec')

    def test_completed_without_recording_no_link(self):
        Event.objects.create(
            title='No Rec Event',
            slug='no-rec-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        response = self.client.get('/events/no-rec-event')
        self.assertNotContains(response, 'Watch the recording')


# --- Access Control Tests ---


class EventDetailAccessControlTest(TierSetupMixin, TestCase):
    """Test event detail access control and registration gating."""

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

    def test_anonymous_sees_gated_event_details(self):
        """Detail page is always visible, but registration is gated."""
        response = self.client.get('/events/gated-event')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gated Event')
        self.assertContains(response, 'This event is gated.')

    def test_free_user_sees_upgrade_cta_for_gated(self):
        user = User.objects.create_user(email='free@test.com', password='pass')
        user.tier = self.free_tier
        user.save()
        self.client.login(email='free@test.com', password='pass')
        response = self.client.get('/events/gated-event')
        self.assertContains(response, 'Upgrade to Main to attend')

    def test_basic_user_sees_upgrade_cta_for_main_event(self):
        user = User.objects.create_user(email='basic@test.com', password='pass')
        user.tier = self.basic_tier
        user.save()
        self.client.login(email='basic@test.com', password='pass')
        response = self.client.get('/events/gated-event')
        self.assertContains(response, 'Upgrade to Main to attend')

    def test_main_user_sees_register_button(self):
        user = User.objects.create_user(email='main@test.com', password='pass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='pass')
        response = self.client.get('/events/gated-event')
        self.assertContains(response, 'Register')
        self.assertNotContains(response, 'Upgrade to Main')

    def test_premium_user_sees_register_button(self):
        user = User.objects.create_user(email='prem@test.com', password='pass')
        user.tier = self.premium_tier
        user.save()
        self.client.login(email='prem@test.com', password='pass')
        response = self.client.get('/events/gated-event')
        self.assertContains(response, 'Register')

    def test_gated_event_never_returns_404(self):
        response = self.client.get('/events/gated-event')
        self.assertEqual(response.status_code, 200)

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

        user = User.objects.create_user(email='viewer@test.com', password='pass')
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
        user = User.objects.create_user(email='soon@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='soon@test.com', password='pass')
        response = self.client.get('/events/soon-event')
        self.assertContains(response, 'https://zoom.us/j/123456')

    def test_zoom_link_not_shown_when_far_from_start(self):
        event = Event.objects.create(
            title='Far Event',
            slug='far-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/999999',
        )
        user = User.objects.create_user(email='far@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='far@test.com', password='pass')
        response = self.client.get('/events/far-event')
        self.assertNotContains(response, 'https://zoom.us/j/999999')

    def test_zoom_link_not_shown_when_not_registered(self):
        event = Event.objects.create(
            title='Not Reg Event',
            slug='not-reg-event',
            start_datetime=timezone.now() + timedelta(minutes=10),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/111111',
        )
        user = User.objects.create_user(email='notreg@test.com', password='pass')
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
        user = User.objects.create_user(email='regstat@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='regstat@test.com', password='pass')
        response = self.client.get('/events/reg-status-event')
        self.assertContains(response, "You're registered!")

    def test_unregistered_user_sees_register_button(self):
        event = Event.objects.create(
            title='Unreg Event',
            slug='unreg-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        user = User.objects.create_user(email='unreg@test.com', password='pass')
        self.client.login(email='unreg@test.com', password='pass')
        response = self.client.get('/events/unreg-event')
        self.assertContains(response, 'Register')
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
            email='apiuser@test.com', password='pass',
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
        gated_event = Event.objects.create(
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
        draft = Event.objects.create(
            title='Draft API',
            slug='draft-api',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
        )
        self.client.login(email='apiuser@test.com', password='pass')
        response = self.client.post('/api/events/draft-api/register')
        self.assertEqual(response.status_code, 404)

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
            email='unreg@test.com', password='pass',
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

    def test_admin_event_list(self):
        Event.objects.create(
            title='Admin Event',
            slug='admin-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        response = self.client.get('/admin/events/event/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Event')

    def test_admin_create_event(self):
        start = timezone.now() + timedelta(days=7)
        response = self.client.post('/admin/events/event/add/', {
            'title': 'New Event',
            'slug': 'new-event',
            'description': 'A new event',
            'event_type': 'live',
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
        })
        self.assertEqual(Event.objects.filter(slug='new-event').count(), 1)

    def test_admin_delete_event(self):
        event = Event.objects.create(
            title='Delete Me',
            slug='delete-me',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        response = self.client.post(
            f'/admin/events/event/{event.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(Event.objects.filter(slug='delete-me').count(), 0)

    def test_admin_slug_prepopulated(self):
        from events.admin.event import EventAdmin
        self.assertEqual(EventAdmin.prepopulated_fields, {'slug': ('title',)})

    def test_admin_search(self):
        Event.objects.create(
            title='Searchable Event',
            slug='searchable-event',
            start_datetime=timezone.now() + timedelta(days=7),
        )
        response = self.client.get('/admin/events/event/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Event')


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

    def test_transition_upcoming_to_live(self):
        event = Event.objects.create(
            title='Upcoming', slug='upcoming-transition',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        from events.admin.event import make_live
        make_live(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'live')

    def test_transition_live_to_completed(self):
        event = Event.objects.create(
            title='Live', slug='live-transition',
            start_datetime=timezone.now(),
            status='live',
        )
        from events.admin.event import make_completed
        make_completed(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')

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

    def test_cancel_from_any_state(self):
        for status in ['draft', 'upcoming', 'live', 'completed']:
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

    def test_draft_to_live_not_allowed(self):
        """Direct transition from draft to live should not happen."""
        event = Event.objects.create(
            title='Draft', slug='draft-to-live',
            start_datetime=timezone.now(),
            status='draft',
        )
        from events.admin.event import make_live
        make_live(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        # Should still be draft because make_live filters for upcoming status
        self.assertEqual(event.status, 'draft')

    def test_completed_to_live_not_allowed(self):
        """Completed events should not transition to live."""
        event = Event.objects.create(
            title='Completed', slug='completed-to-live',
            start_datetime=timezone.now(),
            status='completed',
        )
        from events.admin.event import make_live
        make_live(None, None, Event.objects.filter(pk=event.pk))
        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')


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
        user = User.objects.create_user(email='badge@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='badge@test.com', password='pass')
        response = self.client.get('/events')
        self.assertContains(response, 'Registered')

    def test_unregistered_event_no_badge(self):
        Event.objects.create(
            title='No Badge Event',
            slug='no-badge-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )
        user = User.objects.create_user(email='nobadge@test.com', password='pass')
        self.client.login(email='nobadge@test.com', password='pass')
        response = self.client.get('/events')
        content = response.content.decode()
        # The "Registered" badge text should not be in the page for unregistered events
        # (It may appear in JavaScript, so we check the card area specifically)
        self.assertNotIn('Registered\n', content)
