"""Tests for the Studio series one-click Zoom-creation view (issue #859).

The view only enqueues the background job and flashes a message — the actual
Zoom calls are covered in ``events/tests/test_create_series_zoom_meetings.py``.
Here we assert the view enqueues for eligible occurrences, no-ops when nothing
is eligible, is POST + staff only, and that the detail page renders the button
with the eligibility count and the Zoom indicator column.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()


def _series_with_eligible(count, with_meeting=0):
    series = EventSeries.objects.create(
        name='Series', slug='series',
        start_time='18:00', timezone='Europe/Berlin',
    )
    now = timezone.now()
    pos = 1
    for _ in range(count):
        Event.objects.create(
            title=f'Eligible {pos}', slug=f'elig-{pos}', platform='zoom',
            start_datetime=now + timedelta(days=7 * pos),
            end_datetime=now + timedelta(days=7 * pos, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=series, series_position=pos,
        )
        pos += 1
    for _ in range(with_meeting):
        Event.objects.create(
            title=f'Has Meeting {pos}', slug=f'has-{pos}', platform='zoom',
            start_datetime=now + timedelta(days=7 * pos),
            end_datetime=now + timedelta(days=7 * pos, hours=1),
            timezone='Europe/Berlin', status='upcoming',
            event_series=series, series_position=pos,
            zoom_meeting_id=f'existing-{pos}',
            zoom_join_url=f'https://zoom.us/j/existing-{pos}',
        )
        pos += 1
    return series


class SeriesCreateZoomViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    @patch(
        'studio.views.event_series.enqueue_create_series_zoom_meetings',
    )
    def test_enqueues_for_eligible_and_redirects(self, mock_enqueue):
        series = _series_with_eligible(3)
        response = self.client.post(
            f'/studio/event-series/{series.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f'/studio/event-series/{series.pk}/',
        )
        mock_enqueue.assert_called_once_with(series.pk)

    @patch(
        'studio.views.event_series.enqueue_create_series_zoom_meetings',
    )
    def test_flash_message_names_eligible_count(self, mock_enqueue):
        series = _series_with_eligible(3)
        response = self.client.post(
            f'/studio/event-series/{series.pk}/create-zoom',
            follow=True,
        )
        msgs = [m.message for m in response.context['messages']]
        self.assertTrue(
            any('3 eligible occurrences' in m for m in msgs),
            msgs,
        )

    @patch(
        'studio.views.event_series.enqueue_create_series_zoom_meetings',
    )
    def test_no_enqueue_when_nothing_eligible(self, mock_enqueue):
        series = _series_with_eligible(0, with_meeting=2)
        response = self.client.post(
            f'/studio/event-series/{series.pk}/create-zoom',
            follow=True,
        )
        mock_enqueue.assert_not_called()
        msgs = [m.message for m in response.context['messages']]
        self.assertTrue(
            any('nothing to create' in m.lower() for m in msgs), msgs,
        )

    def test_get_returns_405(self):
        series = _series_with_eligible(1)
        response = self.client.get(
            f'/studio/event-series/{series.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 405)

    def test_nonexistent_series_returns_404(self):
        response = self.client.post('/studio/event-series/99999/create-zoom')
        self.assertEqual(response.status_code, 404)


class SeriesCreateZoomAccessControlTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.series = _series_with_eligible(1)

    def test_non_staff_forbidden(self):
        User.objects.create_user(
            email='reg@test.com', password='pw', is_staff=False,
        )
        self.client.login(email='reg@test.com', password='pw')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class SeriesDetailZoomRenderTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='pw')

    def test_button_shows_eligible_count(self):
        series = _series_with_eligible(2)
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertContains(response, 'data-testid="series-create-zoom"')
        self.assertContains(response, '2 occurrences need a Zoom meeting')

    def test_button_disabled_when_nothing_eligible(self):
        series = _series_with_eligible(0, with_meeting=2)
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertContains(
            response, 'data-testid="series-create-zoom-disabled"',
        )
        self.assertContains(response, 'All occurrences have Zoom meetings')
        self.assertNotContains(response, 'data-testid="series-create-zoom"')

    def test_zoom_indicator_reflects_meeting_state(self):
        series = _series_with_eligible(1, with_meeting=1)
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        content = response.content.decode()
        self.assertIn('data-zoom="yes"', content)
        self.assertIn('data-zoom="no"', content)

    def test_last_run_summary_rendered(self):
        series = _series_with_eligible(1)
        series.zoom_meetings_last_run = {
            'finished_at': '2026-06-12T10:00:00+00:00',
            'created': [1, 2],
            'skipped_existing': 1,
            'skipped_ineligible': 0,
            'failed': [
                {'event_id': 3, 'title': 'Broken One', 'error': 'boom 429'},
            ],
        }
        series.save(update_fields=['zoom_meetings_last_run'])
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertContains(response, 'data-testid="series-zoom-summary"')
        self.assertContains(response, 'Created 2')
        self.assertContains(response, 'Broken One')
        self.assertContains(response, 'boom 429')
