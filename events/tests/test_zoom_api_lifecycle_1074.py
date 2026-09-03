"""Relocated staff-token Zoom lifecycle API owners (#1482)."""

import json
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


@tag('core')
class ApiZoomLifecycleTest(TestCase):
    """Owns Zoom cancel cleanup, reschedule update, and series-rename PATCH.

    Relocated from Playwright ``TestApiZoomLifecycle``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            email='api-zoom-lifecycle-1074@test.com',
            password='pw',
            is_staff=True,
        )
        self.token = Token.objects.create(
            user=self.staff, name='zoom-lifecycle',
        )

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def _create_zoom_event(self, slug):
        start = (timezone.now() + timedelta(days=30)).replace(
            second=0, microsecond=0,
        )
        return Event.objects.create(
            title=f'Zoom Lifecycle {slug}',
            slug=slug,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            timezone='Europe/Berlin',
            origin='studio',
            platform='zoom',
            zoom_meeting_id=f'zoom-{slug}',
            zoom_join_url=f'https://zoom.us/j/{slug}',
        )

    def _patch_event(self, slug, payload):
        return self.client.patch(
            f'/api/events/{slug}',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(),
        )

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_api_patch_reschedule_patches_existing_zoom_meeting(
        self, update_zoom,
    ):
        event = self._create_zoom_event('api-reschedule-1074')
        new_start = event.start_datetime + timedelta(days=2, hours=1)
        new_end = new_start + timedelta(hours=2)

        response = self._patch_event(
            event.slug,
            {
                'start_datetime': new_start.isoformat(),
                'end_datetime': new_end.isoformat(),
            },
        )

        body = response.json()
        self.assertNotIn('zoom_error', body)
        self.assertEqual(update_zoom.call_count, 1)
        patched = update_zoom.call_args.args[0]
        self.assertEqual(patched.pk, event.pk)
        self.assertEqual(patched.start_datetime, new_start)
        self.assertEqual(patched.end_datetime, new_end)
        saved = Event.objects.get(pk=event.pk)
        self.assertEqual(saved.zoom_meeting_id, f'zoom-{event.slug}')
        self.assertEqual(saved.zoom_join_url, f'https://zoom.us/j/{event.slug}')

    @patch('events.services.zoom_lifecycle.delete_meeting')
    def test_api_patch_cancel_deletes_zoom_meeting_and_clears_fields(
        self, delete_zoom,
    ):
        event = self._create_zoom_event('api-cancel-1074')
        seen = []

        def capture(deleted_event):
            seen.append(
                (deleted_event.pk, deleted_event.zoom_meeting_id),
            )

        delete_zoom.side_effect = capture
        response = self._patch_event(event.slug, {'status': 'cancelled'})

        body = response.json()
        self.assertEqual(body['status'], 'cancelled')
        self.assertEqual(body['zoom_join_url'], '')
        self.assertEqual(seen, [(event.pk, f'zoom-{event.slug}')])
        saved = Event.objects.get(pk=event.pk)
        self.assertEqual(saved.zoom_meeting_id, '')
        self.assertEqual(saved.zoom_join_url, '')

    @patch('events.services.zoom_lifecycle.update_meeting')
    def test_api_series_rename_patches_auto_titled_zoom_occurrences(
        self, update_zoom,
    ):
        start = (timezone.now() + timedelta(days=30)).replace(
            second=0, microsecond=0,
        )
        series = EventSeries.objects.create(
            name='Zoom Lifecycle Series',
            slug='zoom-lifecycle-series-1074',
            start_time=time(17, 0),
            timezone='UTC',
        )
        occurrences = []
        for position in (1, 2):
            occurrence_start = start + timedelta(days=7 * position)
            occurrences.append(Event.objects.create(
                title=f'Zoom Lifecycle Series — Session {position}',
                slug=f'zoom-lifecycle-series-1074-{position}',
                start_datetime=occurrence_start,
                end_datetime=occurrence_start + timedelta(hours=1),
                status='upcoming',
                timezone='UTC',
                origin='studio',
                platform='zoom',
                event_series=series,
                series_position=position,
                title_is_auto=True,
                zoom_meeting_id=f'zoom-series-1074-{position}',
                zoom_join_url=f'https://zoom.us/j/series-1074-{position}',
            ))

        response = self.client.patch(
            f'/api/event-series/{series.pk}',
            data=json.dumps({'name': 'Renamed Zoom Lifecycle Series'}),
            content_type='application/json',
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(body['name'], 'Renamed Zoom Lifecycle Series')
        self.assertEqual(update_zoom.call_count, 2)
        self.assertEqual(
            {call.args[0].pk for call in update_zoom.call_args_list},
            {occurrence.pk for occurrence in occurrences},
        )
