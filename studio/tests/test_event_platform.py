"""Tests for Studio event platform selector (Zoom / Custom URL) - issue #108.

Covers:
- Event model has platform field with choices 'zoom' and 'custom', default 'zoom'
- Create form has Platform dropdown with Zoom and Custom URL options
- Create with Custom URL stores the URL in zoom_join_url and sets platform='custom'
- Create with Zoom sets platform='zoom'
- Edit form pre-selects the correct platform and shows correct section
- Custom URL input visible when 'custom' selected, hidden when 'zoom' selected
- Zoom meeting section visible when 'zoom' selected, hidden when 'custom' selected
- Custom URL displayed on public event detail page via existing join link logic
"""

from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from integrations.services.zoom import ZoomAPIError

User = get_user_model()


class EventPlatformModelTest(TestCase):
    """Test the platform field on the Event model."""

    def test_platform_set_to_custom(self):
        event = Event.objects.create(
            title='Custom Event', slug='custom-event',
            start_datetime=timezone.now(),
            platform='custom',
            zoom_join_url='https://youtube.com/live/abc123',
        )
        self.assertEqual(event.platform, 'custom')
        self.assertEqual(event.zoom_join_url, 'https://youtube.com/live/abc123')

    def test_platform_set_to_zoom(self):
        event = Event.objects.create(
            title='Zoom Event', slug='zoom-event',
            start_datetime=timezone.now(),
            platform='zoom',
        )
        self.assertEqual(event.platform, 'zoom')


class StudioEventCreatePlatformURLTest(TestCase):
    """Issue #574 added back the ``/studio/events/new`` create flow.

    These assertions confirm the route is wired and produces a
    ``origin='studio'`` row (the broader behavior is exercised in
    ``StudioEventCreateTest`` in ``test_events.py``).
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_url_returns_200(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)

    def test_create_post_creates_studio_event(self):
        response = self.client.post('/studio/events/new', {
            'title': 'Test Create', 'slug': 'test-create',
            'event_date': '15/03/2026', 'event_time': '14:00',
            'duration_hours': '1', 'timezone': 'Europe/Berlin',
            'status': 'draft', 'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(slug='test-create')
        self.assertEqual(event.origin, 'studio')
        self.assertFalse(bool(event.source_repo))


class StudioEventEditPlatformTest(TestCase):
    """Test editing events with different platforms via Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def _zoom_event(self, **overrides):
        start = timezone.now() + timedelta(days=30)
        defaults = {
            'title': 'Zoom Sync',
            'slug': 'zoom-sync',
            'start_datetime': start,
            'end_datetime': start + timedelta(hours=1),
            'timezone': 'Europe/Berlin',
            'platform': 'zoom',
            'status': 'upcoming',
            'zoom_meeting_id': '99999',
            'zoom_join_url': 'https://zoom.us/j/99999',
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def _post_edit(self, event, *, follow=False, **overrides):
        local_start = event.start_datetime.astimezone(
            ZoneInfo(event.timezone or 'UTC'),
        )
        data = {
            'title': event.title,
            'slug': event.slug,
            'description': event.description,
            'platform': event.platform,
            'event_date': local_start.strftime('%d/%m/%Y'),
            'event_time': local_start.strftime('%H:%M'),
            'duration_hours': '1',
            'timezone': event.timezone,
            'status': event.status,
            'required_level': str(event.required_level),
        }
        data.update(overrides)
        return self.client.post(f'/studio/events/{event.pk}/edit', data, follow=follow)

    def test_edit_zoom_event_preselects_zoom(self):
        """Edit form for a Zoom event pre-selects 'Zoom' in the dropdown."""
        event = Event.objects.create(
            title='Zoom Edit', slug='zoom-edit',
            start_datetime=timezone.now(), platform='zoom',
            zoom_meeting_id='12345', zoom_join_url='https://zoom.us/j/12345',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        # The zoom option should be selected
        self.assertIn(
            '<option value="zoom" selected>Zoom</option>',
            content,
        )

    def test_edit_custom_event_preselects_custom(self):
        """Edit form for a Custom URL event pre-selects 'Custom URL'."""
        event = Event.objects.create(
            title='Custom Edit', slug='custom-edit',
            start_datetime=timezone.now(), platform='custom',
            zoom_join_url='https://discord.gg/xyz',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn(
            '<option value="custom" selected>Custom URL</option>',
            content,
        )

    def test_edit_custom_event_prefills_url(self):
        """Edit form for a Custom URL event pre-fills the custom URL input."""
        event = Event.objects.create(
            title='Custom Prefill', slug='custom-prefill',
            start_datetime=timezone.now(), platform='custom',
            zoom_join_url='https://discord.gg/xyz',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('https://discord.gg/xyz', content)

    def test_edit_zoom_event_shows_zoom_section(self):
        """Edit form for Zoom event: zoom meeting section is present."""
        event = Event.objects.create(
            title='Zoom Section', slug='zoom-section',
            start_datetime=timezone.now(), platform='zoom',
            zoom_meeting_id='12345', zoom_join_url='https://zoom.us/j/12345',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="zoom-meeting-section"', content)
        self.assertIn('Zoom Meeting', content)
        self.assertIn('12345', content)

    def test_edit_save_custom_url(self):
        """Editing an event to custom platform saves URL correctly."""
        event = Event.objects.create(
            title='Switch to Custom', slug='switch-custom',
            start_datetime=timezone.now(), platform='zoom',
        )
        with (
            patch('events.services.zoom_lifecycle.update_meeting') as update_zoom,
            patch('events.services.zoom_lifecycle.delete_meeting') as delete_zoom,
        ):
            self.client.post(f'/studio/events/{event.pk}/edit', {
                'title': 'Switch to Custom',
                'slug': 'switch-custom',
                'platform': 'custom',
                'custom_url': 'https://youtube.com/live/xyz',
                'event_date': '15/03/2026',
                'event_time': '14:00',
                'duration_hours': '1',
                'timezone': 'Europe/Berlin',
                'status': 'upcoming',
                'required_level': '0',
            })
        event.refresh_from_db()
        self.assertEqual(event.platform, 'custom')
        self.assertEqual(event.zoom_join_url, 'https://youtube.com/live/xyz')
        self.assertEqual(event.zoom_meeting_id, '')
        update_zoom.assert_not_called()
        delete_zoom.assert_not_called()

    def test_edit_reschedule_patches_existing_zoom_meeting(self):
        event = self._zoom_event()

        with patch('events.services.zoom_lifecycle.update_meeting') as update_zoom:
            response = self._post_edit(
                event,
                event_time='19:00',
                duration_hours='2',
            )

        self.assertEqual(response.status_code, 302)
        update_zoom.assert_called_once()
        synced_event = update_zoom.call_args.args[0]
        self.assertEqual(synced_event.zoom_meeting_id, '99999')
        self.assertEqual(synced_event.zoom_join_url, 'https://zoom.us/j/99999')
        self.assertEqual(synced_event.end_datetime - synced_event.start_datetime,
                         timedelta(hours=2))

    def test_edit_title_change_patches_zoom_topic(self):
        event = self._zoom_event(slug='zoom-title-sync')

        with patch('events.services.zoom_lifecycle.update_meeting') as update_zoom:
            response = self._post_edit(
                event,
                title='Renamed Zoom Sync',
                slug='zoom-title-sync',
            )

        self.assertEqual(response.status_code, 302)
        update_zoom.assert_called_once()
        self.assertEqual(update_zoom.call_args.args[0].title, 'Renamed Zoom Sync')

    def test_edit_unrelated_fields_do_not_touch_zoom(self):
        event = self._zoom_event(slug='zoom-noop-sync')

        with (
            patch('events.services.zoom_lifecycle.update_meeting') as update_zoom,
            patch('events.services.zoom_lifecycle.delete_meeting') as delete_zoom,
        ):
            response = self._post_edit(event, description='Copy changed only')

        self.assertEqual(response.status_code, 302)
        update_zoom.assert_not_called()
        delete_zoom.assert_not_called()

    def test_edit_cancel_future_zoom_event_deletes_and_clears_fields(self):
        event = self._zoom_event(slug='zoom-cancel-sync')
        self.assertGreater(event.start_datetime, timezone.now())

        with patch('events.services.zoom_lifecycle.delete_meeting') as delete_zoom:
            response = self._post_edit(event, status='cancelled')

        self.assertEqual(response.status_code, 302)
        delete_zoom.assert_called_once()
        event.refresh_from_db()
        self.assertEqual(event.status, 'cancelled')
        self.assertEqual(event.zoom_meeting_id, '')
        self.assertEqual(event.zoom_join_url, '')

    def test_edit_zoom_failure_warns_without_rolling_back_event(self):
        event = self._zoom_event(slug='zoom-failure-sync')

        with patch(
            'events.services.zoom_lifecycle.update_meeting',
            side_effect=ZoomAPIError('Zoom unavailable', status_code=503),
        ):
            response = self._post_edit(
                event,
                title='Saved Despite Zoom Failure',
                slug='zoom-failure-sync',
                follow=True,
            )

        event.refresh_from_db()
        self.assertEqual(event.title, 'Saved Despite Zoom Failure')
        self.assertEqual(event.zoom_meeting_id, '99999')
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('Zoom unavailable' in message for message in messages))

    def test_edit_save_zoom_platform(self):
        """Editing an event to zoom platform sets platform='zoom'."""
        event = Event.objects.create(
            title='Switch to Zoom', slug='switch-zoom',
            start_datetime=timezone.now(), platform='custom',
            zoom_join_url='https://youtube.com/live/abc',
        )
        self.client.post(f'/studio/events/{event.pk}/edit', {
            'title': 'Switch to Zoom',
            'slug': 'switch-zoom',
            'platform': 'zoom',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        event.refresh_from_db()
        self.assertEqual(event.platform, 'zoom')

    def test_edit_custom_url_clears_zoom_meeting_id(self):
        """When switching to custom, zoom_meeting_id is cleared."""
        event = Event.objects.create(
            title='Clear Meeting ID', slug='clear-meeting',
            start_datetime=timezone.now(), platform='zoom',
            zoom_meeting_id='99999', zoom_join_url='https://zoom.us/j/99999',
        )
        self.client.post(f'/studio/events/{event.pk}/edit', {
            'title': 'Clear Meeting ID',
            'slug': 'clear-meeting',
            'platform': 'custom',
            'custom_url': 'https://meet.google.com/abc',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        event.refresh_from_db()
        self.assertEqual(event.platform, 'custom')
        self.assertEqual(event.zoom_join_url, 'https://meet.google.com/abc')
        self.assertEqual(event.zoom_meeting_id, '')


class StudioEventFormPlatformJSTest(TestCase):
    """Test that the form template includes JS for platform toggle."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_edit_form_has_platform_toggle_js(self):
        """Edit form includes JavaScript for platform visibility toggle."""
        event = Event.objects.create(
            title='JS Test', slug='js-test',
            start_datetime=timezone.now(),
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        content = response.content.decode()
        self.assertIn('updatePlatformVisibility', content)


class CustomURLPublicEventDetailTest(TestCase):
    """Test that custom URL events display correctly on the public detail page."""

    def setUp(self):
        self.client = Client()

    def test_custom_url_displayed_on_detail_page(self):
        """Custom URL is displayed on the public event detail page via the
        existing join link logic (which reads zoom_join_url)."""
        event = Event.objects.create(
            title='YouTube Event', slug='youtube-event',
            description='A YouTube live event.',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='upcoming',
            platform='custom',
            zoom_join_url='https://youtube.com/live/abc123',
        )
        user = User.objects.create_user(
            email='user@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user@test.com', password='pass')

        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        # Issue #1082: the join link is now the id-canonical
        # /events/<id>/<slug>/join URL via Event.get_join_url.
        self.assertContains(response, event.get_join_url())

    def test_custom_url_event_join_link_works(self):
        """The join link on a custom URL event points to the custom URL."""
        event = Event.objects.create(
            title='Discord Event', slug='discord-event-public',
            start_datetime=timezone.now() + timedelta(minutes=5),
            status='upcoming',
            platform='custom',
            zoom_join_url='https://discord.gg/abc',
        )
        user = User.objects.create_user(
            email='user2@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user2@test.com', password='pass')

        response = self.client.get(event.get_absolute_url())
        content = response.content.decode()
        # Issue #1082: id-canonical /events/<id>/<slug>/join URL.
        self.assertIn(event.get_join_url(), content)

    def test_zoom_event_still_works(self):
        """Standard Zoom events still display correctly."""
        event = Event.objects.create(
            title='Zoom Detail Event', slug='zoom-detail-event',
            start_datetime=timezone.now() + timedelta(minutes=4),
            status='upcoming',
            platform='zoom',
            zoom_join_url='https://zoom.us/j/99999',
            zoom_meeting_id='99999',
        )
        user = User.objects.create_user(
            email='user3@test.com',
            password='pass',
            email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user3@test.com', password='pass')

        response = self.client.get(event.get_absolute_url())
        # Issue #1082: id-canonical /events/<id>/<slug>/join URL.
        self.assertContains(response, event.get_join_url())
