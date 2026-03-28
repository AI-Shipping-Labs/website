"""Tests for event join redirect with click tracking - issue #186."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventJoinClick, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


class EventJoinRedirectTest(TierSetupMixin, TestCase):
    """Tests for GET /events/<slug>/join endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@example.com',
            password='testpass123',
        )
        cls.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            is_staff=True,
        )
        cls.upcoming_event = Event.objects.create(
            title='Upcoming Event',
            slug='upcoming-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            zoom_join_url='https://zoom.us/j/123456',
        )
        cls.no_url_event = Event.objects.create(
            title='No URL Event',
            slug='no-url-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            zoom_join_url='',
        )
        cls.completed_event = Event.objects.create(
            title='Past Event',
            slug='past-event',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
        )
        cls.completed_event_with_recording = Event.objects.create(
            title='Past Event With Recording',
            slug='past-event-recording',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=abc',
        )
        cls.draft_event = Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
            zoom_join_url='https://zoom.us/j/999',
        )
        # Register user for relevant events
        for event in [
            cls.upcoming_event,
            cls.no_url_event,
            cls.completed_event,
            cls.completed_event_with_recording,
        ]:
            EventRegistration.objects.create(event=event, user=cls.user)

    def test_join_redirect_records_click_and_redirects(self):
        """Registered user for upcoming event with join URL gets 302 to Zoom."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://zoom.us/j/123456')
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.upcoming_event, user=self.user,
            ).count(),
            1,
        )

    def test_join_redirect_requires_login(self):
        """Anonymous user gets redirected to login with next parameter."""
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn('next=/events/upcoming-event/join', response['Location'])

    def test_join_redirect_no_url_shows_unavailable(self):
        """Event without zoom_join_url shows unavailable page."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/no-url-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'The join link is not available yet')
        # No click should be recorded
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_join_redirect_past_event_shows_ended(self):
        """Completed event shows ended page."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/past-event/join')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/join_unavailable.html')
        self.assertContains(response, 'This event has ended')
        # No click should be recorded
        self.assertEqual(EventJoinClick.objects.count(), 0)

    def test_join_redirect_past_event_with_recording_shows_link(self):
        """Completed event with recording shows link to recording."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/past-event-recording/join')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Watch the recording')
        self.assertContains(response, '/event-recordings/past-event-recording')

    def test_join_redirect_draft_event_404(self):
        """Draft event returns 404 for non-staff user."""
        self.client.login(email='member@example.com', password='testpass123')
        response = self.client.get('/events/draft-event/join')
        self.assertEqual(response.status_code, 404)

    def test_join_redirect_unregistered_user_redirected_to_detail(self):
        """Authenticated but unregistered user is redirected to event detail."""
        User.objects.create_user(
            email='unregistered@example.com',
            password='testpass123',
        )
        self.client.login(email='unregistered@example.com', password='testpass123')
        response = self.client.get('/events/upcoming-event/join')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/events/upcoming-event')

    def test_multiple_clicks_tracked(self):
        """Each visit creates a new click record."""
        self.client.login(email='member@example.com', password='testpass123')
        self.client.get('/events/upcoming-event/join')
        self.client.get('/events/upcoming-event/join')
        self.client.get('/events/upcoming-event/join')
        self.assertEqual(
            EventJoinClick.objects.filter(
                event=self.upcoming_event, user=self.user,
            ).count(),
            3,
        )


class EventJoinClickCountPropertyTest(TestCase):
    """Test the join_click_count property on Event."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='counter@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Count Event',
            slug='count-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def test_join_click_count_returns_total(self):
        """join_click_count property returns total number of clicks."""
        self.assertEqual(self.event.join_click_count, 0)
        EventJoinClick.objects.create(event=self.event, user=self.user)
        EventJoinClick.objects.create(event=self.event, user=self.user)
        self.assertEqual(self.event.join_click_count, 2)


class StudioJoinClickCountTest(TierSetupMixin, TestCase):
    """Test that Studio event edit page shows join click count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff_user = User.objects.create_user(
            email='studio@example.com',
            password='testpass123',
            is_staff=True,
        )
        cls.user = User.objects.create_user(
            email='clicker@example.com',
            password='testpass123',
        )
        cls.event = Event.objects.create(
            title='Studio Event',
            slug='studio-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def test_join_click_count_in_studio(self):
        """Studio event edit page displays join click count."""
        # Create 5 clicks
        for _ in range(5):
            EventJoinClick.objects.create(event=self.event, user=self.user)

        self.client.login(email='studio@example.com', password='testpass123')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Join clicks')
        # Check the count is shown via the data-testid element
        content = response.content.decode()
        self.assertIn('data-testid="join-click-count"', content)
        self.assertIn('>5<', content)
