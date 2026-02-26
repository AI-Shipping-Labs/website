"""Tests for studio event CRUD views.

Verifies:
- Event list with search and status filter
- Event create form (GET and POST) with separate date/time/duration fields
- Event edit form (GET and POST) with pre-populated date/time/duration
- Status transitions
- Date/time picker UX: separate Date, Time, Duration fields
- end_datetime computed from start_datetime + duration
- Duration defaults to 1 hour when left blank
- No datetime-local inputs on the form
"""

from datetime import datetime

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
    """Test event creation with separate date/time/duration fields."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)

    def test_create_form_has_separate_date_time_duration_fields(self):
        """The create form must have separate Date, Time, Duration fields."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('name="event_date"', content)
        self.assertIn('name="event_time"', content)
        self.assertIn('name="duration_hours"', content)

    def test_create_form_has_no_datetime_local_input(self):
        """The old datetime-local inputs must be removed."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertNotIn('type="datetime-local"', content)
        self.assertNotIn('name="start_datetime"', content)
        self.assertNotIn('name="end_datetime"', content)

    def test_create_form_has_flatpickr_assets(self):
        """The form must include flatpickr CSS and JS from CDN."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('flatpickr', content)

    def test_create_event_post(self):
        """Create an event using the new date/time/duration fields."""
        response = self.client.post('/studio/events/new', {
            'title': 'New Event',
            'slug': 'new-event',
            'description': 'Test event',
            'event_type': 'live',
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(slug='new-event').exists())

    def test_create_event_saves_correct_start_datetime(self):
        """Submitting date=15/03/2026, time=14:30 saves start_datetime correctly."""
        self.client.post('/studio/events/new', {
            'title': 'Datetime Test',
            'slug': 'datetime-test',
            'event_type': 'live',
            'event_date': '15/03/2026',
            'event_time': '14:30',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='datetime-test')
        self.assertEqual(event.start_datetime.year, 2026)
        self.assertEqual(event.start_datetime.month, 3)
        self.assertEqual(event.start_datetime.day, 15)
        self.assertEqual(event.start_datetime.hour, 14)
        self.assertEqual(event.start_datetime.minute, 30)

    def test_create_event_computes_end_datetime(self):
        """end_datetime is computed as start_datetime + duration."""
        self.client.post('/studio/events/new', {
            'title': 'End DT Test',
            'slug': 'end-dt-test',
            'event_type': 'live',
            'event_date': '15/03/2026',
            'event_time': '14:30',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='end-dt-test')
        self.assertEqual(event.end_datetime.hour, 16)
        self.assertEqual(event.end_datetime.minute, 30)
        self.assertEqual(event.end_datetime.day, 15)

    def test_create_event_duration_default_1_hour(self):
        """Leaving duration blank defaults to 1 hour."""
        self.client.post('/studio/events/new', {
            'title': 'Default Duration',
            'slug': 'default-duration',
            'event_type': 'live',
            'event_date': '20/06/2026',
            'event_time': '09:00',
            'duration_hours': '',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='default-duration')
        self.assertEqual(event.start_datetime.hour, 9)
        self.assertEqual(event.start_datetime.minute, 0)
        self.assertEqual(event.end_datetime.hour, 10)
        self.assertEqual(event.end_datetime.minute, 0)

    def test_create_event_half_hour_duration(self):
        """Duration of 0.5 hours computes end_datetime correctly."""
        self.client.post('/studio/events/new', {
            'title': 'Half Hour',
            'slug': 'half-hour',
            'event_type': 'live',
            'event_date': '10/01/2026',
            'event_time': '15:00',
            'duration_hours': '0.5',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='half-hour')
        self.assertEqual(event.end_datetime.hour, 15)
        self.assertEqual(event.end_datetime.minute, 30)

    def test_create_event_with_capacity(self):
        self.client.post('/studio/events/new', {
            'title': 'Limited Event',
            'slug': 'limited',
            'event_type': 'live',
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '1',
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
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='unlimited')
        self.assertIsNone(event.max_participants)


class StudioEventEditTest(TestCase):
    """Test event editing with pre-populated date/time/duration fields."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Edit Event', slug='edit-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 30),
            status='draft',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_form_has_no_datetime_local_input(self):
        """The old datetime-local inputs must be removed from edit form."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertNotIn('type="datetime-local"', content)
        self.assertNotIn('name="start_datetime"', content)
        self.assertNotIn('name="end_datetime"', content)

    def test_edit_form_prepopulates_date(self):
        """Edit form pre-populates Date field from stored start_datetime."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        # event_date should be 01/06/2026
        self.assertIn('01/06/2026', content)

    def test_edit_form_prepopulates_time(self):
        """Edit form pre-populates Time field from stored start_datetime."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        # event_time should be 10:00
        self.assertIn('value="10:00"', content)

    def test_edit_form_prepopulates_duration(self):
        """Edit form pre-populates Duration from end - start (1.5 hours)."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        # duration_hours should be 1.5
        self.assertIn('value="1.5"', content)

    def test_edit_form_prepopulates_duration_default_1_when_no_end(self):
        """Duration defaults to 1 when end_datetime is null."""
        self.event.end_datetime = None
        self.event.save()
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('value="1"', content)

    def test_edit_form_shows_datetime_summary(self):
        """Edit form shows a resolved datetime summary line."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('Resolved:', content)

    def test_edit_event_post(self):
        """Edit an event using the new date/time/duration fields."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Updated Event',
            'slug': 'edit-event',
            'event_type': 'async',
            'event_date': '15/12/2024',
            'event_time': '14:00',
            'duration_hours': '2',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '10',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Updated Event')
        self.assertEqual(self.event.status, 'upcoming')
        self.assertEqual(self.event.event_type, 'async')

    def test_edit_event_saves_correct_datetimes(self):
        """Editing with time=09:00 and duration=3 saves correctly."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_type': 'live',
            'event_date': '01/06/2026',
            'event_time': '09:00',
            'duration_hours': '3',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_datetime.year, 2026)
        self.assertEqual(self.event.start_datetime.month, 6)
        self.assertEqual(self.event.start_datetime.day, 1)
        self.assertEqual(self.event.start_datetime.hour, 9)
        self.assertEqual(self.event.start_datetime.minute, 0)
        self.assertEqual(self.event.end_datetime.hour, 12)
        self.assertEqual(self.event.end_datetime.minute, 0)

    def test_edit_event_status_transitions(self):
        """Test status can be changed from draft to upcoming."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_type': 'live',
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')

    def test_edit_nonexistent_event_returns_404(self):
        response = self.client.get('/studio/events/99999/edit')
        self.assertEqual(response.status_code, 404)


class StudioEventCreateZoomTest(TestCase):
    """Test Studio endpoint for creating Zoom meetings for events."""

    def setUp(self):
        from unittest.mock import MagicMock, patch  # noqa: F811
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Live Event', slug='live-event',
            event_type='live',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='draft',
        )

    def test_create_zoom_success(self):
        from unittest.mock import MagicMock, patch
        from django.test import override_settings

        with override_settings(
            ZOOM_CLIENT_ID='test-client-id',
            ZOOM_CLIENT_SECRET='test-client-secret',
            ZOOM_ACCOUNT_ID='test-account-id',
        ):
            with patch('integrations.services.zoom.requests.post') as mock_post:
                from integrations.services import zoom
                zoom.clear_token_cache()

                token_resp = MagicMock()
                token_resp.status_code = 200
                token_resp.json.return_value = {
                    'access_token': 'tok', 'expires_in': 3600,
                }
                meeting_resp = MagicMock()
                meeting_resp.status_code = 201
                meeting_resp.json.return_value = {
                    'id': 12345678900,
                    'join_url': 'https://zoom.us/j/12345678900',
                }
                mock_post.side_effect = [token_resp, meeting_resp]

                response = self.client.post(
                    f'/studio/events/{self.event.pk}/create-zoom',
                )
                self.assertEqual(response.status_code, 200)
                self.event.refresh_from_db()
                self.assertEqual(self.event.zoom_meeting_id, '12345678900')
                self.assertEqual(
                    self.event.zoom_join_url, 'https://zoom.us/j/12345678900',
                )

    def test_create_zoom_already_has_meeting(self):
        self.event.zoom_meeting_id = 'existing-id'
        self.event.save(update_fields=['zoom_meeting_id'])
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_zoom_nonexistent_event(self):
        response = self.client.post('/studio/events/99999/create-zoom')
        self.assertEqual(response.status_code, 404)

    def test_create_zoom_requires_post(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 405)


class StudioEventDateTimeParsingTest(TestCase):
    """Test the _parse_event_datetime helper function directly."""

    def test_parse_valid_date_time_duration(self):
        from studio.views.events import _parse_event_datetime
        from django.http import QueryDict

        data = QueryDict(mutable=True)
        data['event_date'] = '15/03/2026'
        data['event_time'] = '14:30'
        data['duration_hours'] = '2'

        start_dt, end_dt = _parse_event_datetime(data)
        self.assertEqual(start_dt, datetime(2026, 3, 15, 14, 30))
        self.assertEqual(end_dt, datetime(2026, 3, 15, 16, 30))

    def test_parse_empty_duration_defaults_to_1_hour(self):
        from studio.views.events import _parse_event_datetime
        from django.http import QueryDict

        data = QueryDict(mutable=True)
        data['event_date'] = '20/06/2026'
        data['event_time'] = '09:00'
        data['duration_hours'] = ''

        start_dt, end_dt = _parse_event_datetime(data)
        self.assertEqual(start_dt, datetime(2026, 6, 20, 9, 0))
        self.assertEqual(end_dt, datetime(2026, 6, 20, 10, 0))

    def test_parse_fractional_duration(self):
        from studio.views.events import _parse_event_datetime
        from django.http import QueryDict

        data = QueryDict(mutable=True)
        data['event_date'] = '01/01/2026'
        data['event_time'] = '10:00'
        data['duration_hours'] = '1.5'

        start_dt, end_dt = _parse_event_datetime(data)
        self.assertEqual(start_dt, datetime(2026, 1, 1, 10, 0))
        self.assertEqual(end_dt, datetime(2026, 1, 1, 11, 30))


class StudioEventFormContextTest(TestCase):
    """Test the _event_form_context helper function."""

    def test_context_for_new_event(self):
        from studio.views.events import _event_form_context

        context = _event_form_context(None)
        self.assertEqual(context['event_date'], '')
        self.assertEqual(context['event_time'], '')
        self.assertEqual(context['duration_hours'], '1')

    def test_context_for_existing_event_with_end(self):
        from studio.views.events import _event_form_context

        event = Event.objects.create(
            title='Test', slug='test-ctx',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 30),
        )
        context = _event_form_context(event)
        self.assertEqual(context['event_date'], '01/06/2026')
        self.assertEqual(context['event_time'], '10:00')
        self.assertEqual(context['duration_hours'], '1.5')

    def test_context_for_existing_event_without_end(self):
        from studio.views.events import _event_form_context

        event = Event.objects.create(
            title='Test', slug='test-ctx-no-end',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=None,
        )
        context = _event_form_context(event)
        self.assertEqual(context['event_date'], '01/06/2026')
        self.assertEqual(context['event_time'], '10:00')
        self.assertEqual(context['duration_hours'], '1')

    def test_context_for_whole_number_duration(self):
        from studio.views.events import _event_form_context

        event = Event.objects.create(
            title='Test', slug='test-ctx-whole',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 12, 0),
        )
        context = _event_form_context(event)
        self.assertEqual(context['duration_hours'], '2')
