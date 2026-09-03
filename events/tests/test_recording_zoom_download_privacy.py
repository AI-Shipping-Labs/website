"""Public pages must not render Zoom download URLs (issue #1505)."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from content.models import Workshop
from events.models import Event


class RecordingZoomDownloadUrlPrivacyTest(TestCase):
    def test_public_event_and_workshop_pages_omit_zoom_download_url(self):
        now = timezone.now()
        download_url = 'https://zoom.us/rec/download/secret-recording-token'
        event = Event.objects.create(
            title='Public recording event',
            slug='public-recording-event',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            required_level=0,
            recording_url='https://www.youtube.com/watch?v=public',
            recording_zoom_download_url=download_url,
        )
        workshop = Workshop.objects.create(
            slug='public-recording-workshop',
            title='Public recording workshop',
            date=date(2026, 4, 21),
            description='Workshop body',
            status='published',
            event=event,
        )

        event_response = self.client.get(event.get_absolute_url())
        self.assertNotContains(event_response, download_url)
        self.assertNotContains(event_response, 'recording_zoom_download_url')

        workshop_response = self.client.get(workshop.get_absolute_url())
        self.assertNotContains(workshop_response, download_url)
        self.assertNotContains(workshop_response, 'recording_zoom_download_url')
