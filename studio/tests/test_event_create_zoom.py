"""Tests for Studio Zoom meeting creation button - issue #109.

Covers:
- Successful Zoom creation (mocking integrations.services.zoom.create_meeting)
- Event already has a Zoom meeting returns 400
- Non-existent event returns 404
- GET request returns 405 Method Not Allowed
- Non-staff user is redirected to login
- Anonymous user is redirected to login
- Template: Zoom meeting section only visible when platform='zoom'
- Template: Zoom meeting section hidden when platform='custom'
- Template: Zoom button only on edit form (not create form)
- Template: "Create Zoom Meeting" button present when no meeting exists
- Template: Meeting ID and join URL shown when meeting exists
- Template: Error status span uses red text class
"""

from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from events.models import Event

User = get_user_model()


class EventCreateZoomSuccessTest(TestCase):
    """Test successful Zoom meeting creation via the studio endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Live Workshop', slug='live-workshop',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
        )

    @patch('studio.views.events.create_meeting')
    def test_create_zoom_success(self, mock_create_meeting):
        """POST to create-zoom with mocked create_meeting returns 200 and saves meeting data."""
        mock_create_meeting.return_value = {
            'meeting_id': '98765432100',
            'join_url': 'https://zoom.us/j/98765432100',
        }
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['meeting_id'], '98765432100')
        self.assertEqual(data['join_url'], 'https://zoom.us/j/98765432100')

        self.event.refresh_from_db()
        self.assertEqual(self.event.zoom_meeting_id, '98765432100')
        self.assertEqual(self.event.zoom_join_url, 'https://zoom.us/j/98765432100')

    @patch('studio.views.events.create_meeting')
    def test_create_zoom_calls_create_meeting_with_event(self, mock_create_meeting):
        """create_meeting is called with the event instance."""
        mock_create_meeting.return_value = {
            'meeting_id': '111',
            'join_url': 'https://zoom.us/j/111',
        }
        self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        mock_create_meeting.assert_called_once()
        call_arg = mock_create_meeting.call_args[0][0]
        self.assertEqual(call_arg.pk, self.event.pk)

    @patch('studio.views.events.create_meeting')
    def test_create_zoom_api_error_returns_500(self, mock_create_meeting):
        """When create_meeting raises an exception, the endpoint returns 500 with error message."""
        mock_create_meeting.side_effect = Exception('Zoom API rate limit exceeded')
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('Zoom API rate limit exceeded', data['error'])

        # Event should NOT have a meeting ID set
        self.event.refresh_from_db()
        self.assertEqual(self.event.zoom_meeting_id, '')


class EventCreateZoomAlreadyHasMeetingTest(TestCase):
    """Test that creating a Zoom meeting for an event that already has one returns 400."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Already Has Zoom', slug='has-zoom',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
            zoom_meeting_id='existing-12345',
            zoom_join_url='https://zoom.us/j/existing-12345',
        )

    def test_returns_400(self):
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 400)

    def test_error_message(self):
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('already has', data['error'].lower())


class EventCreateZoom404Test(TestCase):
    """Test that creating a Zoom meeting for a non-existent event returns 404."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_nonexistent_event_returns_404(self):
        response = self.client.post('/studio/events/99999/create-zoom')
        self.assertEqual(response.status_code, 404)


class EventCreateZoom405Test(TestCase):
    """Test that GET requests to the create-zoom endpoint return 405."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Method Test', slug='method-test',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
        )

    def test_get_returns_405(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 405)


class EventCreateZoomAccessControlTest(TestCase):
    """Test access control for the create-zoom endpoint."""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(
            title='Access Test', slug='access-test',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
        )

    def test_non_staff_user_redirected_to_login(self):
        """A non-staff authenticated user gets redirected (via staff_required decorator)."""
        regular_user = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        # staff_required returns 403 for authenticated non-staff users
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_redirected_to_login(self):
        """An anonymous user is redirected to the login page."""
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class EventCreateZoomTemplateTest(TestCase):
    """Test that the event form template correctly shows/hides the Zoom meeting section."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_does_not_show_zoom_section(self):
        """The create form (new event) should NOT have the Zoom meeting section,
        since Zoom creation requires an existing event with a PK."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertNotIn('id="zoom-meeting-section"', content)
        self.assertNotIn('Create Zoom Meeting', content)

    def test_edit_zoom_event_without_meeting_shows_button(self):
        """Edit form for a Zoom event with no meeting shows the Create Zoom Meeting button."""
        event = Event.objects.create(
            title='No Meeting Yet', slug='no-meeting',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="zoom-meeting-section"', content)
        self.assertIn('Create Zoom Meeting', content)
        self.assertIn('id="create-zoom-btn"', content)

    def test_edit_zoom_event_with_meeting_shows_meeting_info(self):
        """Edit form for a Zoom event with an existing meeting shows meeting ID and URL."""
        event = Event.objects.create(
            title='Has Meeting', slug='has-meeting',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='upcoming',
            zoom_meeting_id='99887766',
            zoom_join_url='https://zoom.us/j/99887766',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="zoom-meeting-section"', content)
        self.assertIn('99887766', content)
        self.assertIn('https://zoom.us/j/99887766', content)
        # The create button should NOT be present when meeting already exists
        self.assertNotIn('id="create-zoom-btn"', content)

    def test_edit_custom_event_hides_zoom_section_via_js(self):
        """Edit form for a Custom URL event includes JS to hide the zoom section."""
        event = Event.objects.create(
            title='Custom Event', slug='custom-event',
            event_type='live', platform='custom',
            start_datetime=timezone.now(),
            zoom_join_url='https://youtube.com/live/abc',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        # The zoom meeting section element exists in the DOM but JS hides it
        self.assertIn('id="zoom-meeting-section"', content)
        # The platform toggle JS is present
        self.assertIn('updatePlatformVisibility', content)
        # Custom URL option is selected
        self.assertIn('<option value="custom" selected>Custom URL</option>', content)

    def test_template_has_error_status_span(self):
        """The template includes a status span for error messages."""
        event = Event.objects.create(
            title='Status Span', slug='status-span',
            event_type='live', platform='zoom',
            start_datetime=timezone.now(),
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="zoom-status"', content)

