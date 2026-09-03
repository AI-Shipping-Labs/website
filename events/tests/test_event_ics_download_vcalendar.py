"""Relocated per-event ICS VCALENDAR HTTP owner (#1483)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event


class EventIcsDownloadVcalendarTest(TestCase):
    """Owns valid VCALENDAR delivery for the per-event `.ics` download.

    Relocated from Playwright
    ``TestPostRegistrationConfirmation.test_ics_download_returns_vcalendar``.
    """

    def test_ics_download_returns_vcalendar(self):
        Event.objects.create(
            slug='ics-evt',
            title='ICS Event',
            description='A workshop run by the community.',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
        )

        # Public download — no auth needed for non-draft events.
        # Issue #673: ``/events/<slug>/calendar.ics`` (slug-keyed) is the
        # intentional ICS surface — kept on slug for email/.ics emails.
        response = self.client.get('/events/ics-evt/calendar.ics')
        body = response.content.decode('utf-8')

        self.assertIn('text/calendar', response['Content-Type'])
        self.assertIn('BEGIN:VCALENDAR', body)
        self.assertIn('END:VCALENDAR', body)
        self.assertIn('SUMMARY:ICS Event', body)
