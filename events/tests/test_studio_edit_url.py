"""Tests for ``get_studio_edit_url`` on event models (issue #667).

Adds the per-record Studio editor link surfaced by the floating
"Edit in Studio" button on public event and event-series detail pages.
"""

from datetime import time

from django.test import TestCase, tag
from django.utils import timezone

from events.models import Event, EventSeries


@tag('core')
class EventModelStudioEditUrlTest(TestCase):
    """``Event`` and ``EventSeries`` return the canonical Studio URL.

    These strings must match the Studio URL conf so the public-page
    "Edit in Studio" button never points at a 404.
    """

    def test_event_studio_edit_url(self):
        event = Event.objects.create(
            title='Sample Event',
            slug='sample-event',
            start_datetime=timezone.now(),
        )
        self.assertEqual(
            event.get_studio_edit_url(),
            f'/studio/events/{event.pk}/edit',
        )

    def test_event_series_studio_edit_url(self):
        series = EventSeries.objects.create(
            name='Sample Series',
            slug='sample-series',
            start_time=time(18, 0),
        )
        self.assertEqual(
            series.get_studio_edit_url(),
            f'/studio/event-series/{series.pk}/',
        )
