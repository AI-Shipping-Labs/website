"""Tests for studio event views.

Verifies:
- Event list with search and status filter (no create button)
- Event create URL returns 404
- Event edit form (GET and POST) with pre-populated date/time/duration
- Synced events: description read-only, operational fields editable, GitHub link shown
- Status transitions
- Date/time picker UX: separate Date, Time, Duration fields
- end_datetime computed from start_datetime + duration
- Duration defaults to 1 hour when left blank
- No datetime-local inputs on the form
"""

from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from events.models import Event
from tests.fixtures import StaffUserMixin


class StudioEventListTest(StaffUserMixin, TestCase):
    """Test event list view."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

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

    def test_list_shows_kind_and_platform(self):
        Event.objects.create(
            title='Workshop Event',
            slug='workshop-event',
            start_datetime=timezone.now(),
            kind='workshop',
            platform='custom',
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'Kind / Platform')
        self.assertContains(response, 'Workshop')
        self.assertContains(response, 'Custom URL')

    def test_list_has_no_create_button(self):
        response = self.client.get('/studio/events/')
        self.assertNotContains(response, 'New Event')

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


class StudioEventCreateURLTest(StaffUserMixin, TestCase):
    """Test that the event create URL returns 404."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_url_get_returns_404(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 404)

    def test_create_url_post_returns_404(self):
        response = self.client.post('/studio/events/new', {
            'title': 'New Event',
            'slug': 'new-event',
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 404)


class StudioEventEditTest(StaffUserMixin, TestCase):
    """Test event editing with pre-populated date/time/duration fields."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
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
        self.assertIn('01/06/2026', content)

    def test_edit_form_prepopulates_time(self):
        """Edit form pre-populates Time field from stored start_datetime."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('value="10:00"', content)

    def test_edit_form_prepopulates_duration(self):
        """Edit form pre-populates Duration from end - start (1.5 hours)."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
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
            'event_date': '15/12/2024',
            'event_time': '14:00',
            'duration_hours': '2',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '10',
            'tags': 'event, , live ,, workshop ',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Updated Event')
        self.assertEqual(self.event.status, 'upcoming')
        self.assertEqual(self.event.tags, ['event', 'live', 'workshop'])

    def test_edit_event_saves_correct_datetimes(self):
        """Editing with time=09:00 and duration=3 saves correctly."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
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


class StudioEventSyncedTest(StaffUserMixin, TestCase):
    """Test that synced events show GitHub link and have read-only content fields."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.event = Event.objects.create(
            title='Synced Event', slug='synced-event',
            description='Original description',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 0),
            status='draft',
            max_participants=100,
            source_repo='AI-Shipping-Labs/content',
            source_path='my-event.md',
        )

    def test_synced_event_shows_github_banner(self):
        """Synced events display the synced-from-GitHub banner."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="synced-banner"')

    def test_synced_event_shows_edit_on_github_link(self):
        """Synced events show an 'Edit on GitHub' link."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'Edit on GitHub')

    def test_synced_event_description_is_disabled(self):
        """Description field is disabled for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        # The description textarea should have the disabled attribute
        # Find the description textarea and check it has disabled
        self.assertIn('name="description"', content)
        # Check that there is a disabled textarea for description
        import re
        desc_match = re.search(
            r'<textarea[^>]*name="description"[^>]*>', content
        )
        self.assertIsNotNone(desc_match)
        self.assertIn('disabled', desc_match.group(0))

    def test_synced_event_title_is_disabled(self):
        """Title field is disabled for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        title_match = re.search(
            r'<input[^>]*name="title"[^>]*>', content
        )
        self.assertIsNotNone(title_match)
        self.assertIn('disabled', title_match.group(0))

    def test_synced_event_status_is_editable(self):
        """Status field remains editable for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        status_match = re.search(
            r'<select[^>]*name="status"[^>]*>', content
        )
        self.assertIsNotNone(status_match)
        self.assertNotIn('disabled', status_match.group(0))

    def test_synced_event_max_participants_is_editable(self):
        """Max participants field remains editable for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        mp_match = re.search(
            r'<input[^>]*name="max_participants"[^>]*>', content
        )
        self.assertIsNotNone(mp_match)
        self.assertNotIn('disabled', mp_match.group(0))

    def test_synced_event_post_updates_operational_fields(self):
        """POST to synced event updates status and max_participants but not description."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'status': 'upcoming',
            'max_participants': '50',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')
        self.assertEqual(self.event.max_participants, 50)
        # Description should not change
        self.assertEqual(self.event.description, 'Original description')

    def test_synced_event_post_does_not_change_title(self):
        """POST to synced event does not change the title."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Hacked Title',
            'status': 'draft',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Synced Event')

    def test_synced_event_shows_view_event_title(self):
        """Synced event page shows 'View Event' instead of 'Edit Event'."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'View Event')

    def test_non_synced_event_has_no_synced_banner(self):
        """Non-synced events do not show the synced banner."""
        event = Event.objects.create(
            title='Local Event', slug='local-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            status='draft',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertNotContains(response, 'data-testid="synced-banner"')


class StudioEventCreateZoomTest(StaffUserMixin, TestCase):
    """Test Studio endpoint for creating Zoom meetings for events."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.event = Event.objects.create(
            title='Live Event', slug='live-event',
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
        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '15/03/2026'
        data['event_time'] = '14:30'
        data['duration_hours'] = '2'

        start_dt, end_dt = _parse_event_datetime(data)
        self.assertEqual(start_dt, datetime(2026, 3, 15, 14, 30))
        self.assertEqual(end_dt, datetime(2026, 3, 15, 16, 30))

    def test_parse_empty_duration_defaults_to_1_hour(self):
        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '20/06/2026'
        data['event_time'] = '09:00'
        data['duration_hours'] = ''

        start_dt, end_dt = _parse_event_datetime(data)
        self.assertEqual(start_dt, datetime(2026, 6, 20, 9, 0))
        self.assertEqual(end_dt, datetime(2026, 6, 20, 10, 0))

    def test_parse_fractional_duration(self):
        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

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
