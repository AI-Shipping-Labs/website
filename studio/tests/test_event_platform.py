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

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from events.models.event import EVENT_PLATFORM_CHOICES

User = get_user_model()


class EventPlatformModelTest(TestCase):
    """Test the platform field on the Event model."""

    def test_platform_field_exists(self):
        event = Event.objects.create(
            title='Platform Test', slug='platform-test',
            start_datetime=timezone.now(),
        )
        self.assertTrue(hasattr(event, 'platform'))

    def test_platform_default_is_zoom(self):
        event = Event.objects.create(
            title='Default Platform', slug='default-platform',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.platform, 'zoom')

    def test_platform_choices(self):
        self.assertEqual(EVENT_PLATFORM_CHOICES, [
            ('zoom', 'Zoom'),
            ('custom', 'Custom URL'),
        ])

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


class StudioEventCreatePlatformTest(TestCase):
    """Test creating events with different platforms via Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_has_platform_dropdown(self):
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('name="platform"', content)
        self.assertIn('id="platform-select"', content)
        self.assertIn('Zoom', content)
        self.assertIn('Custom URL', content)

    def test_create_form_has_custom_url_input(self):
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('name="custom_url"', content)
        self.assertIn('id="custom-url-input"', content)

    def test_create_form_custom_url_section_hidden_by_default(self):
        """Custom URL section is hidden by default (Zoom is the default)."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('id="custom-url-section"', content)
        # The section should have display:none initially
        self.assertIn('style="display: none;"', content)

    def test_create_zoom_event(self):
        """Creating an event with platform=zoom sets platform field correctly."""
        self.client.post('/studio/events/new', {
            'title': 'Zoom Workshop',
            'slug': 'zoom-workshop',
            'event_type': 'live',
            'platform': 'zoom',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        event = Event.objects.get(slug='zoom-workshop')
        self.assertEqual(event.platform, 'zoom')
        # zoom_join_url should not be set (it's set by create-zoom endpoint)
        self.assertEqual(event.zoom_join_url, '')

    def test_create_custom_url_event(self):
        """Creating with platform=custom stores custom_url in zoom_join_url."""
        self.client.post('/studio/events/new', {
            'title': 'YouTube Live Event',
            'slug': 'youtube-live',
            'event_type': 'live',
            'platform': 'custom',
            'custom_url': 'https://youtube.com/live/abc123',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '2',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        event = Event.objects.get(slug='youtube-live')
        self.assertEqual(event.platform, 'custom')
        self.assertEqual(event.zoom_join_url, 'https://youtube.com/live/abc123')
        self.assertEqual(event.zoom_meeting_id, '')

    def test_create_custom_event_clears_zoom_meeting_id(self):
        """Creating with platform=custom clears zoom_meeting_id."""
        self.client.post('/studio/events/new', {
            'title': 'Discord Event',
            'slug': 'discord-event',
            'event_type': 'live',
            'platform': 'custom',
            'custom_url': 'https://discord.gg/xyz',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='discord-event')
        self.assertEqual(event.zoom_meeting_id, '')

    def test_default_platform_when_not_specified(self):
        """When platform is not in POST data, default to zoom."""
        self.client.post('/studio/events/new', {
            'title': 'No Platform',
            'slug': 'no-platform',
            'event_type': 'live',
            'event_date': '15/03/2026',
            'event_time': '14:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'draft',
            'required_level': '0',
        })
        event = Event.objects.get(slug='no-platform')
        self.assertEqual(event.platform, 'zoom')


class StudioEventEditPlatformTest(TestCase):
    """Test editing events with different platforms via Studio."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

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
        self.client.post(f'/studio/events/{event.pk}/edit', {
            'title': 'Switch to Custom',
            'slug': 'switch-custom',
            'event_type': 'live',
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
            'event_type': 'live',
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
            'event_type': 'live',
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

    def test_create_form_has_platform_toggle_js(self):
        """Create form includes JavaScript for platform visibility toggle."""
        response = self.client.get('/studio/events/new')
        content = response.content.decode()
        self.assertIn('updatePlatformVisibility', content)
        self.assertIn("platformSelect.addEventListener('change'", content)

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
            start_datetime=timezone.now() + timedelta(minutes=10),
            status='upcoming',
            platform='custom',
            zoom_join_url='https://youtube.com/live/abc123',
        )
        user = User.objects.create_user(email='user@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user@test.com', password='pass')

        response = self.client.get('/events/youtube-event')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://youtube.com/live/abc123')

    def test_custom_url_event_join_link_works(self):
        """The join link on a custom URL event points to the custom URL."""
        event = Event.objects.create(
            title='Discord Event', slug='discord-event-public',
            start_datetime=timezone.now() + timedelta(minutes=5),
            status='upcoming',
            platform='custom',
            zoom_join_url='https://discord.gg/abc',
        )
        user = User.objects.create_user(email='user2@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user2@test.com', password='pass')

        response = self.client.get('/events/discord-event-public')
        content = response.content.decode()
        self.assertIn('href="https://discord.gg/abc"', content)

    def test_zoom_event_still_works(self):
        """Standard Zoom events still display correctly."""
        event = Event.objects.create(
            title='Zoom Detail Event', slug='zoom-detail-event',
            start_datetime=timezone.now() + timedelta(minutes=10),
            status='upcoming',
            platform='zoom',
            zoom_join_url='https://zoom.us/j/99999',
            zoom_meeting_id='99999',
        )
        user = User.objects.create_user(email='user3@test.com', password='pass')
        EventRegistration.objects.create(event=event, user=user)
        self.client.login(email='user3@test.com', password='pass')

        response = self.client.get('/events/zoom-detail-event')
        self.assertContains(response, 'https://zoom.us/j/99999')
