"""Tests for studio event CRUD views.

Verifies:
- Event list with search and status filter
- Event create form (GET and POST)
- Event edit form (GET and POST)
- Status transitions
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from events.models import Event

User = get_user_model()


class StudioEventListTest(TestCase):
    """Test event list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/events/')
        self.assertTemplateUsed(response, 'studio/events/list.html')

    def test_list_shows_events(self):
        Event.objects.create(
            title='Test Event', slug='test-event',
            start_datetime=timezone.now(),
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'Test Event')

    def test_list_filter_by_status(self):
        Event.objects.create(
            title='UpcomingEventXYZ', slug='upcoming',
            start_datetime=timezone.now(), status='upcoming',
        )
        Event.objects.create(
            title='DraftEventXYZ', slug='draft',
            start_datetime=timezone.now(), status='draft',
        )
        response = self.client.get('/studio/events/?status=upcoming')
        self.assertContains(response, 'UpcomingEventXYZ')
        self.assertNotContains(response, 'DraftEventXYZ')

    def test_list_search(self):
        Event.objects.create(
            title='Python Workshop', slug='python',
            start_datetime=timezone.now(),
        )
        Event.objects.create(
            title='Java Workshop', slug='java',
            start_datetime=timezone.now(),
        )
        response = self.client.get('/studio/events/?q=Python')
        self.assertContains(response, 'Python Workshop')
        self.assertNotContains(response, 'Java Workshop')


class StudioEventCreateTest(TestCase):
    """Test event creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)

    def test_create_event_post(self):
        response = self.client.post('/studio/events/new', {
            'title': 'New Event',
            'slug': 'new-event',
            'description': 'Test event',
            'event_type': 'live',
            'start_datetime': '2024-12-01T10:00',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(slug='new-event').exists())

    def test_create_event_with_capacity(self):
        self.client.post('/studio/events/new', {
            'title': 'Limited Event',
            'slug': 'limited',
            'event_type': 'live',
            'start_datetime': '2024-12-01T10:00',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
            'max_participants': '50',
        })
        event = Event.objects.get(slug='limited')
        self.assertEqual(event.max_participants, 50)

    def test_create_event_unlimited_capacity(self):
        self.client.post('/studio/events/new', {
            'title': 'Unlimited',
            'slug': 'unlimited',
            'event_type': 'live',
            'start_datetime': '2024-12-01T10:00',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='unlimited')
        self.assertIsNone(event.max_participants)


class StudioEventEditTest(TestCase):
    """Test event editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Edit Event', slug='edit-event',
            start_datetime=timezone.now(), status='draft',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_event_post(self):
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Updated Event',
            'slug': 'edit-event',
            'event_type': 'async',
            'start_datetime': '2024-12-15T14:00',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '10',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Updated Event')
        self.assertEqual(self.event.status, 'upcoming')
        self.assertEqual(self.event.event_type, 'async')

    def test_edit_event_status_transitions(self):
        """Test status can be changed from draft to upcoming."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_type': 'live',
            'start_datetime': '2024-12-01T10:00',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')

    def test_edit_nonexistent_event_returns_404(self):
        response = self.client.get('/studio/events/99999/edit')
        self.assertEqual(response.status_code, 404)
