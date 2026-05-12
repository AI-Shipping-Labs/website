"""Unit tests for ``events.services.calendar_links.build_calendar_links``.

These exercise the pure URL-builder layer (no SES, no email rendering).
The rendered-email assertions live in ``test_calendar_invite.py``.
"""

import datetime
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from events.models import Event
from events.services.calendar_links import build_calendar_links
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _qs(url):
    """Return ``parse_qs`` of the query portion of ``url``."""
    return parse_qs(urlparse(url).query)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class BuildCalendarLinksTest(TestCase):
    """Behaviour of the deep-link URL builder."""

    @classmethod
    def setUpTestData(cls):
        cls.start_utc = datetime.datetime(
            2026, 6, 15, 18, 0, tzinfo=datetime.timezone.utc,
        )
        cls.end_utc = datetime.datetime(
            2026, 6, 15, 19, 30, tzinfo=datetime.timezone.utc,
        )
        cls.event = Event.objects.create(
            slug='ai-agents-workshop',
            title='AI Agents Workshop',
            description='Build a working agent in 90 minutes',
            start_datetime=cls.start_utc,
            end_datetime=cls.end_utc,
            status='upcoming',
        )

    def test_google_calendar_url_has_template_action_and_event_fields(self):
        """Google deep link should be parseable and carry every field."""
        url = build_calendar_links(self.event)['google']

        self.assertTrue(url.startswith(
            'https://calendar.google.com/calendar/render?'
        ))
        params = _qs(url)
        self.assertEqual(params['action'], ['TEMPLATE'])
        self.assertEqual(params['text'], ['AI Agents Workshop'])
        self.assertEqual(
            params['dates'],
            ['20260615T180000Z/20260615T193000Z'],
        )
        self.assertIn(
            '/events/ai-agents-workshop/join',
            params['location'][0],
        )
        details = params['details'][0]
        self.assertIn('Build a working agent in 90 minutes', details)
        self.assertIn(
            'https://aishippinglabs.com/events/ai-agents-workshop/join',
            details,
        )

    def test_default_end_is_start_plus_one_hour_when_end_missing(self):
        """Missing ``end_datetime`` should default to start + 1h everywhere."""
        start = datetime.datetime(
            2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc,
        )
        event = Event.objects.create(
            slug='no-end',
            title='No End',
            start_datetime=start,
            end_datetime=None,
            status='upcoming',
        )

        links = build_calendar_links(event)
        google_params = _qs(links['google'])
        outlook_params = _qs(links['outlook'])
        office_params = _qs(links['office365'])

        self.assertEqual(
            google_params['dates'],
            ['20260701T090000Z/20260701T100000Z'],
        )
        self.assertEqual(outlook_params['enddt'], ['2026-07-01T10:00:00Z'])
        self.assertEqual(office_params['enddt'], ['2026-07-01T10:00:00Z'])

    def test_non_utc_start_is_converted_to_utc_in_google_dates(self):
        """A 20:00 Berlin DST start must serialize as 18:00 UTC."""
        start_berlin = datetime.datetime(
            2026, 8, 10, 20, 0, tzinfo=ZoneInfo('Europe/Berlin'),
        )
        event = Event.objects.create(
            slug='berlin-event',
            title='Berlin Meetup',
            start_datetime=start_berlin,
            end_datetime=start_berlin + timedelta(hours=1),
            status='upcoming',
        )

        params = _qs(build_calendar_links(event)['google'])
        dates = params['dates'][0]

        # Berlin is UTC+2 during August DST, so 20:00 Berlin == 18:00 UTC.
        self.assertTrue(
            dates.startswith('20260810T180000Z'),
            f'Expected start in UTC but got {dates}',
        )
        self.assertNotIn('20260810T200000Z', dates)

    def test_special_characters_in_title_and_description_round_trip(self):
        """``&``, ``=``, ``?`` and spaces survive a parse_qs round-trip."""
        event = Event.objects.create(
            slug='special-chars',
            title='Q&A: AI in production?',
            description='Use ?, =, & freely',
            start_datetime=self.start_utc,
            end_datetime=self.end_utc,
            status='upcoming',
        )

        params = _qs(build_calendar_links(event)['google'])
        self.assertEqual(params['text'], ['Q&A: AI in production?'])
        self.assertIn('Use ?, =, & freely', params['details'][0])

    def test_non_ascii_title_round_trips_through_parse_qs(self):
        """Cyrillic + emoji in the title should decode back verbatim."""
        event = Event.objects.create(
            slug='non-ascii',
            title='Машинное обучение 🚀',
            start_datetime=self.start_utc,
            end_datetime=self.end_utc,
            status='upcoming',
        )

        params = _qs(build_calendar_links(event)['google'])
        self.assertEqual(params['text'], ['Машинное обучение 🚀'])

    def test_long_description_is_truncated_to_keep_url_short(self):
        """A 5000-char description must not produce a 4000+ char URL."""
        event = Event.objects.create(
            slug='long-desc',
            title='Long Desc',
            description='x' * 5000,
            start_datetime=self.start_utc,
            end_datetime=self.end_utc,
            status='upcoming',
        )

        url = build_calendar_links(event)['google']
        self.assertLess(len(url), 4000, f'URL too long: {len(url)} chars')

        details = _qs(url)['details'][0]
        # 2000 chars of description plus the "\n\nJoin: <url>" suffix.
        join_line = (
            '\n\nJoin: https://aishippinglabs.com/events/long-desc/join'
        )
        self.assertEqual(len(details), 2000 + len(join_line))

    def test_empty_description_still_includes_join_line_in_details(self):
        """Even with no description, ``details`` must carry the join URL."""
        event = Event.objects.create(
            slug='empty-desc',
            title='Empty Desc',
            description='',
            start_datetime=self.start_utc,
            end_datetime=self.end_utc,
            status='upcoming',
        )

        params = _qs(build_calendar_links(event)['google'])
        self.assertIn(
            'https://aishippinglabs.com/events/empty-desc/join',
            params['details'][0],
        )

    def test_outlook_live_url_shape(self):
        """Outlook.com URL hits ``outlook.live.com`` with the compose params."""
        url = build_calendar_links(self.event)['outlook']

        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, 'outlook.live.com')
        params = _qs(url)
        self.assertEqual(params['path'], ['/calendar/action/compose'])
        self.assertEqual(params['rru'], ['addevent'])
        self.assertEqual(params['subject'], ['AI Agents Workshop'])
        self.assertEqual(params['startdt'], ['2026-06-15T18:00:00Z'])
        self.assertEqual(params['enddt'], ['2026-06-15T19:30:00Z'])
        self.assertIn(
            '/events/ai-agents-workshop/join',
            params['location'][0],
        )

    def test_office365_url_shape(self):
        """Microsoft 365 URL hits ``outlook.office.com`` with the same shape."""
        url = build_calendar_links(self.event)['office365']

        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, 'outlook.office.com')
        params = _qs(url)
        self.assertEqual(params['path'], ['/calendar/action/compose'])
        self.assertEqual(params['rru'], ['addevent'])
        self.assertEqual(params['startdt'], ['2026-06-15T18:00:00Z'])
        self.assertEqual(params['enddt'], ['2026-06-15T19:30:00Z'])


@override_settings(SITE_BASE_URL='https://env.example.com')
class CalendarLinksJoinUrlOverrideTest(TestCase):
    """Studio override of ``SITE_BASE_URL`` (issue #435) drives the location."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            slug='override-event',
            title='Override Test',
            start_datetime=datetime.datetime(
                2026, 6, 15, 18, 0, tzinfo=datetime.timezone.utc,
            ),
            end_datetime=datetime.datetime(
                2026, 6, 15, 19, 0, tzinfo=datetime.timezone.utc,
            ),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_google_location_uses_studio_override(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://studio.example.com',
            group='site',
        )
        clear_config_cache()

        params = _qs(build_calendar_links(self.event)['google'])
        self.assertEqual(
            params['location'],
            ['https://studio.example.com/events/override-event/join'],
        )
        self.assertNotIn('env.example.com', params['location'][0])
