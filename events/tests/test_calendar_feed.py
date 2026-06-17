"""Tests for the platform-wide subscribable events calendar feed (issue #578).

Covers:

- ``build_vevent`` produces the right UID, DTSTART/DTEND, SEQUENCE,
  SUMMARY (with the [Hosted on X] prefix for external events),
  description truncation, and URL/LOCATION shape.
- ``feed_events_queryset`` includes published upcoming/completed events
  within the 30-day backfill window and excludes drafts, cancelled,
  gated, and out-of-window rows.
- ``generate_feed_ics`` emits a VCALENDAR with feed-only metadata
  (X-WR-CALNAME etc.), no METHOD property, and one VEVENT per row.
- ``build_subscribe_urls`` builds the canonical Google / Apple / copy
  URLs with the correct URL encoding.
- ``GET /events/calendar.ics`` returns 200 with the right headers,
  handles ``If-None-Match`` -> 304, and refreshes ETag on edits.
- Refactor parity guard: ``generate_ics`` still emits the same UID,
  DTSTART, DTEND, SEQUENCE the legacy single-event invite produced.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import TestCase, override_settings
from django.utils import timezone
from icalendar import Calendar

from accounts.models import Token
from events.models import Event
from events.services.calendar_feed import (
    FEED_BACKFILL_DAYS,
    build_subscribe_urls,
    feed_events_queryset,
)
from events.services.calendar_invite import (
    AUDIENCE_PUBLIC_FEED,
    build_vevent,
    generate_feed_ics,
    generate_ics,
)
from tests.fixtures import StaffUserMixin


def _parse(ics_bytes):
    return Calendar.from_ical(ics_bytes)


def _vevents(cal):
    return [c for c in cal.walk() if c.name == 'VEVENT']


def _vevent_summaries(cal):
    return [str(v.get('summary')) for v in _vevents(cal)]


def _vevent_by_uid(cal, uid):
    for v in _vevents(cal):
        if str(v.get('uid')) == uid:
            return v
    return None


def _dt_utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class BuildVeventTest(TestCase):
    """Per-event payload shared by single-event invites and the feed."""

    @classmethod
    def setUpTestData(cls):
        cls.start = datetime(2026, 6, 15, 14, 0, tzinfo=dt_timezone.utc)
        cls.community = Event.objects.create(
            slug='community-evt',
            title='Community Workshop',
            description='A normal community session.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=2),
            status='upcoming',
            ics_sequence=4,
        )
        cls.external = Event.objects.create(
            slug='maven-cohort',
            title='LLM Cohort',
            description='External cohort hosted by Maven.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status='upcoming',
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm',
            ics_sequence=2,
        )

    def test_uid_is_stable_and_slug_based(self):
        v1 = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        v2 = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(str(v1.get('uid')), str(v2.get('uid')))
        self.assertEqual(
            str(v1.get('uid')), 'event-community-evt@aishippinglabs.com',
        )

    def test_summary_prefix_for_external_events(self):
        community = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        external = build_vevent(self.external, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(community.get('summary')),
            'Community Workshop',
        )
        self.assertEqual(
            str(external.get('summary')),
            '[Hosted on Maven] LLM Cohort',
        )

    def test_dtstart_dtend_serialize_to_utc(self):
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        dtstart = vevent.get('dtstart').dt
        dtend = vevent.get('dtend').dt
        self.assertEqual(
            dtstart.astimezone(dt_timezone.utc), self.start,
        )
        self.assertEqual(
            dtend.astimezone(dt_timezone.utc),
            self.start + timedelta(hours=2),
        )

    def test_dtend_defaults_to_start_plus_one_hour_when_null(self):
        event = Event.objects.create(
            slug='no-end-evt',
            title='No-end Event',
            start_datetime=self.start,
            end_datetime=None,
            status='upcoming',
        )
        vevent = build_vevent(event, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            vevent.get('dtend').dt.astimezone(dt_timezone.utc),
            self.start + timedelta(hours=1),
        )

    def test_sequence_matches_event_ics_sequence(self):
        community = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        external = build_vevent(self.external, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(community.get('sequence'), 4)
        self.assertEqual(external.get('sequence'), 2)

    def test_url_points_at_public_detail_page_not_join(self):
        # Issue #673: detail URL is ``/events/<id>/<slug>``.
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('url')),
            f'https://aishippinglabs.com{self.community.get_absolute_url()}',
        )

    def test_location_is_detail_url_for_community_events(self):
        # Issue #673: detail URL is ``/events/<id>/<slug>``.
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('location')),
            f'https://aishippinglabs.com{self.community.get_absolute_url()}',
        )

    def test_location_is_external_host_for_external_events(self):
        vevent = build_vevent(self.external, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(str(vevent.get('location')), 'Maven')

    def test_description_includes_join_line(self):
        # Issue #673: join line links to canonical ``/events/<id>/<slug>``.
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        description = str(vevent.get('description'))
        self.assertIn('A normal community session.', description)
        self.assertIn(
            f'Join: https://aishippinglabs.com{self.community.get_absolute_url()}',
            description,
        )

    def test_description_truncated_at_2000_chars(self):
        long_body = 'a' * 3500
        event = Event.objects.create(
            slug='long-desc-evt',
            title='Long Desc',
            description=long_body,
            start_datetime=self.start,
            status='upcoming',
        )
        vevent = build_vevent(event, audience=AUDIENCE_PUBLIC_FEED)
        description = str(vevent.get('description'))
        # 2000 chars of body + the "\n\nJoin: ..." suffix.
        self.assertIn('a' * 2000, description)
        self.assertNotIn('a' * 2001, description)
        # Issue #673: join line uses the canonical id+slug URL.
        self.assertIn(
            f'Join: https://aishippinglabs.com{event.get_absolute_url()}',
            description,
        )

    def test_summary_prefix_for_gated_events(self):
        """Issue #726: gated events get a ``[Members only]`` prefix."""
        gated = Event.objects.create(
            slug='gated-summary-evt',
            title='Members Workshop',
            description='Gated body text.',
            start_datetime=self.start,
            status='upcoming',
            required_level=20,
        )
        vevent = build_vevent(gated, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('summary')),
            '[Members only] Members Workshop',
        )

    def test_summary_prefix_combines_members_only_and_hosted_on(self):
        """Issue #726: external gated event combines both prefixes."""
        gated_external = Event.objects.create(
            slug='gated-external-evt',
            title='LLM Cohort',
            description='Gated external body.',
            start_datetime=self.start,
            status='upcoming',
            required_level=20,
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm',
        )
        vevent = build_vevent(gated_external, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('summary')),
            '[Members only] [Hosted on Maven] LLM Cohort',
        )

    def test_summary_unchanged_for_open_events(self):
        """Issue #726: open (level 0) events get no ``[Members only]`` prefix."""
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('summary')),
            'Community Workshop',
        )

    def test_description_stubbed_for_gated_events(self):
        """Issue #726: gated event description does NOT leak the body."""
        gated = Event.objects.create(
            slug='gated-desc-evt',
            title='Members Workshop',
            description='SECRET-MEMBERS-ONLY-BODY-DETAILS',
            start_datetime=self.start,
            status='upcoming',
            required_level=20,
        )
        vevent = build_vevent(gated, audience=AUDIENCE_PUBLIC_FEED)
        description = str(vevent.get('description'))
        # The original gated body MUST NOT appear in the public feed.
        self.assertNotIn('SECRET-MEMBERS-ONLY-BODY-DETAILS', description)
        # The stub MUST mention members-only and include the detail URL.
        self.assertIn('members-only', description)
        self.assertIn(
            f'https://aishippinglabs.com{gated.get_absolute_url()}',
            description,
        )
        # The title still appears in the stub so subscribers know what
        # the entry is even if their client hides the SUMMARY.
        self.assertIn('Members Workshop', description)

    def test_description_full_body_for_open_events(self):
        """Issue #726: open (level 0) events keep the full body + Join line."""
        vevent = build_vevent(self.community, audience=AUDIENCE_PUBLIC_FEED)
        description = str(vevent.get('description'))
        self.assertIn('A normal community session.', description)
        self.assertIn(
            f'Join: https://aishippinglabs.com{self.community.get_absolute_url()}',
            description,
        )


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class FeedEventsQuerysetTest(TestCase):
    """Inclusion rules for the platform-wide feed query."""

    @classmethod
    def setUpTestData(cls):
        cls.now = datetime(2026, 6, 1, 12, 0, tzinfo=dt_timezone.utc)
        # Inside the window: completed 10 days ago.
        cls.recent_completed = Event.objects.create(
            slug='recent-done',
            title='Recent Completed',
            start_datetime=cls.now - timedelta(days=10),
            status='completed',
        )
        # Inside the window: upcoming in 5 days.
        cls.future_upcoming = Event.objects.create(
            slug='future-upcoming',
            title='Future Upcoming',
            start_datetime=cls.now + timedelta(days=5),
            status='upcoming',
        )
        # Outside the window: completed 60 days ago.
        cls.old_completed = Event.objects.create(
            slug='old-done',
            title='Old Completed',
            start_datetime=cls.now - timedelta(days=60),
            status='completed',
        )
        # Draft — excluded.
        cls.draft = Event.objects.create(
            slug='draft-evt',
            title='Draft Event',
            start_datetime=cls.now + timedelta(days=3),
            status='draft',
        )
        # Cancelled — excluded.
        cls.cancelled = Event.objects.create(
            slug='cancelled-evt',
            title='Cancelled Event',
            start_datetime=cls.now + timedelta(days=3),
            status='cancelled',
        )
        # Gated (Main, level 20) — excluded from public feed.
        cls.gated = Event.objects.create(
            slug='gated-evt',
            title='Gated Event',
            start_datetime=cls.now + timedelta(days=3),
            status='upcoming',
            required_level=20,
        )
        # Unpublished but upcoming — excluded.
        cls.unpublished = Event.objects.create(
            slug='unpublished-evt',
            title='Unpublished',
            start_datetime=cls.now + timedelta(days=3),
            status='upcoming',
            published=False,
        )

    def test_includes_published_upcoming_and_recent_completed(self):
        qs = feed_events_queryset(now=self.now)
        slugs = list(qs.values_list('slug', flat=True))
        self.assertIn('recent-done', slugs)
        self.assertIn('future-upcoming', slugs)

    def test_includes_gated_events(self):
        """Issue #726: gated events appear in the public feed.

        The body is stubbed and the SUMMARY is prefixed with
        ``[Members only]`` in ``build_vevent``; the inclusion query
        no longer filters on ``required_level``.
        """
        qs = feed_events_queryset(now=self.now)
        slugs = set(qs.values_list('slug', flat=True))
        self.assertIn('gated-evt', slugs)

    def test_excludes_old_drafts_cancelled_unpublished(self):
        qs = feed_events_queryset(now=self.now)
        slugs = set(qs.values_list('slug', flat=True))
        self.assertNotIn('old-done', slugs)
        self.assertNotIn('draft-evt', slugs)
        self.assertNotIn('cancelled-evt', slugs)
        self.assertNotIn('unpublished-evt', slugs)

    def test_ordered_by_start_datetime_ascending(self):
        qs = feed_events_queryset(now=self.now)
        starts = list(qs.values_list('start_datetime', flat=True))
        self.assertEqual(starts, sorted(starts))

    def test_backfill_window_is_30_days(self):
        # Sanity-pin the documented constant — if someone changes the
        # window we want to know about it.
        self.assertEqual(FEED_BACKFILL_DAYS, 30)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class GenerateFeedIcsTest(TestCase):
    """VCALENDAR-level metadata and per-event content for the feed."""

    @classmethod
    def setUpTestData(cls):
        cls.start = timezone.now() + timedelta(days=1)
        cls.event_a = Event.objects.create(
            slug='evt-a', title='Event A',
            start_datetime=cls.start, status='upcoming',
        )
        cls.event_b = Event.objects.create(
            slug='evt-b', title='Event B',
            external_host='Luma',
            start_datetime=cls.start + timedelta(days=1),
            status='upcoming',
        )

    def test_feed_has_calendar_level_metadata(self):
        ics = generate_feed_ics([self.event_a, self.event_b])
        text = ics.decode('utf-8')
        self.assertIn('X-WR-CALNAME:AI Shipping Labs Events', text)
        self.assertIn('X-WR-CALDESC:', text)
        self.assertIn('X-WR-TIMEZONE:UTC', text)
        self.assertIn('REFRESH-INTERVAL;VALUE=DURATION:PT1H', text)
        self.assertIn('X-PUBLISHED-TTL:PT1H', text)
        self.assertIn('PRODID:-//AI Shipping Labs//Events Feed//EN', text)

    def test_feed_has_no_method_property(self):
        ics = generate_feed_ics([self.event_a])
        cal = _parse(ics)
        # ``METHOD`` is for invites; subscribed feeds must omit it.
        self.assertIsNone(cal.get('method'))

    def test_feed_emits_one_vevent_per_event(self):
        ics = generate_feed_ics([self.event_a, self.event_b])
        cal = _parse(ics)
        summaries = sorted(_vevent_summaries(cal))
        self.assertEqual(
            summaries, ['Event A', '[Hosted on Luma] Event B'],
        )

    def test_feed_handles_empty_queryset(self):
        ics = generate_feed_ics([])
        cal = _parse(ics)
        self.assertEqual(len(_vevents(cal)), 0)
        text = ics.decode('utf-8')
        self.assertTrue(text.startswith('BEGIN:VCALENDAR'))
        self.assertTrue(text.rstrip().endswith('END:VCALENDAR'))


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class BuildSubscribeUrlsTest(TestCase):
    """URL builders for the Subscribe-to-all-events CTA."""

    def test_feed_https_uses_site_base_url(self):
        urls = build_subscribe_urls()
        self.assertEqual(
            urls['feed_https'],
            'https://aishippinglabs.com/events/calendar.ics',
        )

    def test_feed_webcal_strips_scheme(self):
        urls = build_subscribe_urls()
        self.assertEqual(
            urls['feed_webcal'],
            'webcal://aishippinglabs.com/events/calendar.ics',
        )

    def test_apple_url_is_webcal(self):
        urls = build_subscribe_urls()
        self.assertEqual(urls['apple'], urls['feed_webcal'])

    def test_google_url_encodes_webcal_in_cid_parameter(self):
        urls = build_subscribe_urls()
        google = urls['google']
        self.assertTrue(
            google.startswith(
                'https://calendar.google.com/calendar/r?cid=',
            ),
        )
        # The cid value MUST be URL-encoded; decoding it should give
        # back the canonical webcal URL.
        from urllib.parse import unquote
        cid_value = google.split('cid=', 1)[1]
        self.assertEqual(
            unquote(cid_value),
            'webcal://aishippinglabs.com/events/calendar.ics',
        )

    def test_override_site_url_argument(self):
        urls = build_subscribe_urls(site_url='https://staging.example.com')
        self.assertEqual(
            urls['feed_webcal'],
            'webcal://staging.example.com/events/calendar.ics',
        )


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class EventsCalendarFeedViewTest(TestCase):
    """GET /events/calendar.ics view contract."""

    @classmethod
    def setUpTestData(cls):
        cls.future = timezone.now() + timedelta(days=2)
        cls.past = timezone.now() - timedelta(days=5)
        cls.upcoming = Event.objects.create(
            slug='upcoming-feed',
            title='Upcoming Feed Event',
            start_datetime=cls.future,
            status='upcoming',
        )
        cls.completed = Event.objects.create(
            slug='completed-feed',
            title='Completed Feed Event',
            start_datetime=cls.past,
            status='completed',
        )
        cls.draft = Event.objects.create(
            slug='draft-feed',
            title='Draft Feed Event',
            start_datetime=cls.future,
            status='draft',
        )
        cls.cancelled = Event.objects.create(
            slug='cancelled-feed',
            title='Cancelled Feed Event',
            start_datetime=cls.future,
            status='cancelled',
        )
        cls.gated = Event.objects.create(
            slug='gated-feed',
            title='Gated Feed Event',
            start_datetime=cls.future,
            status='upcoming',
            required_level=20,
        )
        cls.external = Event.objects.create(
            slug='external-feed',
            title='External Feed Event',
            external_host='Maven',
            start_datetime=cls.future,
            status='upcoming',
        )

    def test_returns_200_with_calendar_content_type(self):
        response = self.client.get('/events/calendar.ics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'], 'text/calendar; charset=utf-8',
        )

    def test_anonymous_access(self):
        # No login. The view must not redirect to login.
        response = self.client.get('/events/calendar.ics')
        self.assertEqual(response.status_code, 200)

    def test_body_parses_as_valid_vcalendar(self):
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        self.assertEqual(cal.name, 'VCALENDAR')

    def test_body_starts_and_ends_with_vcalendar_markers(self):
        response = self.client.get('/events/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('BEGIN:VCALENDAR'))
        self.assertTrue(text.rstrip().endswith('END:VCALENDAR'))

    def test_includes_upcoming_and_completed_events(self):
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        summaries = _vevent_summaries(cal)
        self.assertIn('Upcoming Feed Event', summaries)
        self.assertIn('Completed Feed Event', summaries)

    def test_excludes_draft_and_cancelled(self):
        response = self.client.get('/events/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertNotIn('Draft Feed Event', text)
        self.assertNotIn('draft-feed', text)
        self.assertNotIn('Cancelled Feed Event', text)
        self.assertNotIn('cancelled-feed', text)

    def test_includes_gated_event_with_members_only_prefix(self):
        """Issue #726: gated event appears with ``[Members only]`` SUMMARY."""
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        gated = _vevent_by_uid(
            cal, 'event-gated-feed@aishippinglabs.com',
        )
        self.assertIsNotNone(gated)
        self.assertEqual(
            str(gated.get('summary')),
            '[Members only] Gated Feed Event',
        )

    def test_gated_event_description_is_stubbed(self):
        """Issue #726: gated event DESCRIPTION does not leak the body."""
        self.gated.description = 'SECRET-GATED-FEED-BODY'
        self.gated.save(update_fields=['description'])
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        gated = _vevent_by_uid(
            cal, 'event-gated-feed@aishippinglabs.com',
        )
        self.assertIsNotNone(gated)
        description = str(gated.get('description'))
        self.assertIn('members-only', description)
        # The detail URL must be present so the subscriber can land
        # on the upgrade CTA.
        self.assertIn(
            f'https://aishippinglabs.com{self.gated.get_absolute_url()}',
            description,
        )
        self.assertNotIn('SECRET-GATED-FEED-BODY', description)

    def test_community_event_url_and_location_are_public_detail_not_join(self):
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        event = _vevent_by_uid(
            cal, 'event-upcoming-feed@aishippinglabs.com',
        )
        self.assertIsNotNone(event)
        detail_url = (
            f'https://aishippinglabs.com{self.upcoming.get_absolute_url()}'
        )
        self.assertEqual(str(event.get('url')), detail_url)
        self.assertEqual(str(event.get('location')), detail_url)
        self.assertNotIn('/join', str(event.get('url')))
        self.assertNotIn('/join', str(event.get('location')))

    def test_external_event_has_hosted_on_prefix(self):
        response = self.client.get('/events/calendar.ics')
        cal = _parse(response.content)
        external = _vevent_by_uid(
            cal, 'event-external-feed@aishippinglabs.com',
        )
        self.assertIsNotNone(external)
        self.assertEqual(
            str(external.get('summary')),
            '[Hosted on Maven] External Feed Event',
        )
        self.assertEqual(str(external.get('location')), 'Maven')

    def test_cache_headers_present(self):
        response = self.client.get('/events/calendar.ics')
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=300',
        )
        self.assertIn('ETag', response)
        self.assertIn('Last-Modified', response)
        # Weak ETag.
        self.assertTrue(response['ETag'].startswith('W/"'))

    def test_inline_content_disposition(self):
        response = self.client.get('/events/calendar.ics')
        self.assertIn('inline', response['Content-Disposition'])
        self.assertIn('ai-shipping-labs.ics', response['Content-Disposition'])

    def test_if_none_match_returns_304(self):
        response_a = self.client.get('/events/calendar.ics')
        etag = response_a['ETag']
        response_b = self.client.get(
            '/events/calendar.ics', HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(response_b.status_code, 304)
        self.assertEqual(response_b.content, b'')
        self.assertEqual(response_b['ETag'], etag)

    def test_if_none_match_does_not_match_after_edit(self):
        response_a = self.client.get('/events/calendar.ics')
        etag = response_a['ETag']
        # Edit a feed-eligible row: this bumps updated_at.
        self.upcoming.title = 'Upcoming Feed Event UPDATED'
        self.upcoming.save()

        response_b = self.client.get(
            '/events/calendar.ics', HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(response_b.status_code, 200)
        self.assertNotEqual(response_b['ETag'], etag)
        self.assertIn(
            b'Upcoming Feed Event UPDATED', response_b.content,
        )


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class EventsCalendarFeedScheduleRefreshTest(StaffUserMixin, TestCase):
    """Issue #1030: subscribed feed updates after schedule edits."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.api_token = Token.objects.create(
            user=cls.staff, name='calendar-feed-refresh',
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _create_event(self, **overrides):
        start = overrides.pop('start_datetime', _dt_utc(2027, 6, 15, 14))
        defaults = {
            'slug': 'feed-refresh-event',
            'title': 'Feed Refresh Event',
            'description': 'Public feed body.',
            'start_datetime': start,
            'end_datetime': start + timedelta(hours=1),
            'timezone': 'UTC',
            'status': 'upcoming',
            'published': True,
            'origin': 'studio',
            'ics_sequence': 4,
        }
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def _feed_response(self, **headers):
        return self.client.get('/events/calendar.ics', **headers)

    def _feed_vevents(self, uid):
        response = self._feed_response()
        self.assertEqual(response.status_code, 200)
        cal = _parse(response.content)
        return [v for v in _vevents(cal) if str(v.get('uid')) == uid]

    def _feed_vevent(self, uid):
        matches = self._feed_vevents(uid)
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _post_studio_edit(self, event, *, start, duration_hours='1', **overrides):
        data = {
            'title': event.title,
            'slug': event.slug,
            'description': event.description,
            'event_date': start.strftime('%d/%m/%Y'),
            'event_time': start.strftime('%H:%M'),
            'duration_hours': duration_hours,
            'timezone': 'UTC',
            'status': event.status,
            'required_level': str(event.required_level),
        }
        data.update(overrides)
        return self.client.post(
            f'/studio/events/{event.pk}/edit', data, follow=True,
        )

    def test_studio_start_edit_updates_dtstart_sequence_and_stable_uid(self):
        event = self._create_event()
        uid = 'event-feed-refresh-event@aishippinglabs.com'
        before = self._feed_vevent(uid)
        before_sequence = int(before.get('sequence'))

        new_start = _dt_utc(2027, 6, 22, 15, 30)
        response = self._post_studio_edit(event, start=new_start)
        self.assertEqual(response.status_code, 200)

        after = self._feed_vevent(uid)
        self.assertEqual(str(after.get('uid')), uid)
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            new_start,
        )
        self.assertGreater(int(after.get('sequence')), before_sequence)

    def test_studio_duration_only_edit_updates_dtend_and_sequence(self):
        event = self._create_event(slug='duration-refresh-event')
        uid = 'event-duration-refresh-event@aishippinglabs.com'
        before = self._feed_vevent(uid)
        before_sequence = int(before.get('sequence'))

        response = self._post_studio_edit(
            event, start=event.start_datetime, duration_hours='1.5',
        )
        self.assertEqual(response.status_code, 200)

        after = self._feed_vevent(uid)
        self.assertEqual(
            after.get('dtend').dt.astimezone(dt_timezone.utc),
            event.start_datetime + timedelta(minutes=90),
        )
        self.assertGreater(int(after.get('sequence')), before_sequence)

    def test_noop_studio_save_does_not_bump_sequence(self):
        event = self._create_event(slug='noop-refresh-event')
        uid = 'event-noop-refresh-event@aishippinglabs.com'
        before = self._feed_vevent(uid)
        before_sequence = int(before.get('sequence'))

        response = self._post_studio_edit(event, start=event.start_datetime)
        self.assertEqual(response.status_code, 200)

        after = self._feed_vevent(uid)
        self.assertEqual(int(after.get('sequence')), before_sequence)
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            event.start_datetime,
        )

    def test_old_if_none_match_gets_200_after_studio_schedule_edit(self):
        event = self._create_event(slug='etag-refresh-event')
        uid = 'event-etag-refresh-event@aishippinglabs.com'
        response_a = self._feed_response()
        old_etag = response_a['ETag']
        before = _vevent_by_uid(_parse(response_a.content), uid)
        before_sequence = int(before.get('sequence'))

        new_start = _dt_utc(2027, 6, 23, 16)
        self._post_studio_edit(event, start=new_start, duration_hours='2')

        response_b = self._feed_response(HTTP_IF_NONE_MATCH=old_etag)
        self.assertEqual(response_b.status_code, 200)
        self.assertNotEqual(response_b['ETag'], old_etag)
        after = _vevent_by_uid(_parse(response_b.content), uid)
        self.assertIsNotNone(after)
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            new_start,
        )
        self.assertEqual(
            after.get('dtend').dt.astimezone(dt_timezone.utc),
            new_start + timedelta(hours=2),
        )
        self.assertGreater(int(after.get('sequence')), before_sequence)

    def test_old_if_modified_since_same_second_gets_updated_content(self):
        event = self._create_event(slug='ims-refresh-event')
        uid = 'event-ims-refresh-event@aishippinglabs.com'
        base = _dt_utc(2027, 7, 1, 12)
        Event.objects.filter(pk=event.pk).update(
            updated_at=base.replace(microsecond=100000),
        )
        response_a = self._feed_response()
        old_last_modified = response_a['Last-Modified']

        new_start = _dt_utc(2027, 7, 8, 13)
        event.start_datetime = new_start
        event.end_datetime = new_start + timedelta(hours=1)
        event.save(update_fields=['start_datetime', 'end_datetime'])
        Event.objects.filter(pk=event.pk).update(
            updated_at=base.replace(microsecond=500000),
        )

        response_b = self._feed_response(
            HTTP_IF_MODIFIED_SINCE=old_last_modified,
        )
        self.assertEqual(response_b.status_code, 200)
        after = _vevent_by_uid(_parse(response_b.content), uid)
        self.assertIsNotNone(after)
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            new_start,
        )

    def test_title_edit_refreshes_etag_without_sequence_bump(self):
        event = self._create_event(slug='copy-refresh-event')
        uid = 'event-copy-refresh-event@aishippinglabs.com'
        response_a = self._feed_response()
        old_etag = response_a['ETag']
        before = _vevent_by_uid(_parse(response_a.content), uid)
        before_sequence = int(before.get('sequence'))

        response = self._post_studio_edit(
            event,
            start=event.start_datetime,
            title='Feed Refresh Event Updated',
        )
        self.assertEqual(response.status_code, 200)

        response_b = self._feed_response(HTTP_IF_NONE_MATCH=old_etag)
        self.assertEqual(response_b.status_code, 200)
        self.assertNotEqual(response_b['ETag'], old_etag)
        after = _vevent_by_uid(_parse(response_b.content), uid)
        self.assertEqual(str(after.get('summary')), 'Feed Refresh Event Updated')
        self.assertEqual(int(after.get('sequence')), before_sequence)

    def test_gated_event_stays_public_safe_after_schedule_edit(self):
        event = self._create_event(
            slug='gated-refresh-event',
            title='Gated Refresh Event',
            description='SECRET gated body https://zoom.us/j/raw-secret',
            required_level=20,
        )
        uid = 'event-gated-refresh-event@aishippinglabs.com'
        before = self._feed_vevent(uid)
        before_sequence = int(before.get('sequence'))

        new_start = _dt_utc(2027, 6, 24, 17)
        self._post_studio_edit(event, start=new_start)

        after = self._feed_vevent(uid)
        self.assertEqual(str(after.get('summary')), '[Members only] Gated Refresh Event')
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            new_start,
        )
        self.assertGreater(int(after.get('sequence')), before_sequence)
        description = str(after.get('description'))
        self.assertIn('members-only', description)
        self.assertNotIn('SECRET gated body', description)
        self.assertNotIn('https://zoom.us/j/raw-secret', description)

    def test_api_patch_schedule_edit_refreshes_feed(self):
        event = self._create_event(slug='api-refresh-event')
        uid = 'event-api-refresh-event@aishippinglabs.com'
        response_a = self._feed_response()
        old_etag = response_a['ETag']
        before = _vevent_by_uid(_parse(response_a.content), uid)
        before_sequence = int(before.get('sequence'))
        new_start = _dt_utc(2027, 6, 25, 18)
        new_end = new_start + timedelta(hours=2)

        response = self.client.patch(
            f'/api/events/{event.slug}',
            data=json.dumps({
                'start_datetime': new_start.isoformat(),
                'end_datetime': new_end.isoformat(),
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.api_token.key}',
        )
        self.assertEqual(response.status_code, 200)

        response_b = self._feed_response(HTTP_IF_NONE_MATCH=old_etag)
        self.assertEqual(response_b.status_code, 200)
        self.assertNotEqual(response_b['ETag'], old_etag)
        after = _vevent_by_uid(_parse(response_b.content), uid)
        self.assertEqual(
            after.get('dtstart').dt.astimezone(dt_timezone.utc),
            new_start,
        )
        self.assertEqual(
            after.get('dtend').dt.astimezone(dt_timezone.utc),
            new_end,
        )
        self.assertGreater(int(after.get('sequence')), before_sequence)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class GenerateIcsRefactorParityTest(TestCase):
    """Regression guard for the single-event ``.ics`` generator.

    Issue #578 extracted ``build_vevent`` and changed ``URL`` /
    ``LOCATION`` to point at the public detail page. UID, DTSTART,
    DTEND, SEQUENCE — the de-duplication-and-update keys — must still
    match the legacy generator so that previously-issued invites are
    recognized as the same event after the refactor.
    """

    @classmethod
    def setUpTestData(cls):
        cls.start = timezone.now() + timedelta(days=2)
        cls.event = Event.objects.create(
            slug='parity-evt',
            title='Parity Event',
            description='Body.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=3),
            status='upcoming',
            ics_sequence=7,
        )

    def test_uid_matches_legacy_slug_form(self):
        cal = _parse(generate_ics(self.event))
        vevent = _vevents(cal)[0]
        self.assertEqual(
            str(vevent.get('uid')),
            'event-parity-evt@aishippinglabs.com',
        )

    def test_dtstart_dtend_match_event_fields(self):
        cal = _parse(generate_ics(self.event))
        vevent = _vevents(cal)[0]
        self.assertEqual(
            vevent.get('dtstart').dt.astimezone(dt_timezone.utc),
            self.start.astimezone(dt_timezone.utc).replace(microsecond=0),
        )
        self.assertEqual(
            vevent.get('dtend').dt.astimezone(dt_timezone.utc),
            (self.start + timedelta(hours=3)).astimezone(
                dt_timezone.utc,
            ).replace(microsecond=0),
        )

    def test_sequence_matches_event_ics_sequence(self):
        cal = _parse(generate_ics(self.event))
        vevent = _vevents(cal)[0]
        self.assertEqual(vevent.get('sequence'), 7)

    def test_single_event_calendar_still_has_method_request(self):
        # The single-event invite keeps METHOD:REQUEST so mail clients
        # render the "Accept / Decline" buttons. Feed must NOT have it.
        cal = _parse(generate_ics(self.event))
        self.assertEqual(str(cal.get('method')), 'REQUEST')


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class EventsListSubscribeContextTest(TestCase):
    """The events list view exposes the subscribe URL dict in context."""

    def test_subscribe_urls_in_context(self):
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)
        urls = response.context['subscribe_urls']
        self.assertEqual(
            urls['feed_https'],
            'https://aishippinglabs.com/events/calendar.ics',
        )
        self.assertEqual(
            urls['apple'],
            'webcal://aishippinglabs.com/events/calendar.ics',
        )
        self.assertTrue(
            urls['google'].startswith(
                'https://calendar.google.com/calendar/r?cid=',
            ),
        )

    def test_subscribe_cta_rendered_for_anonymous(self):
        response = self.client.get('/events')
        self.assertContains(
            response, 'data-testid="events-subscribe-trigger"',
        )
        self.assertContains(
            response, 'data-testid="events-subscribe-google"',
        )
        self.assertContains(
            response, 'data-testid="events-subscribe-apple"',
        )
        self.assertContains(
            response, 'data-testid="events-subscribe-feed-input"',
        )

    def test_subscribe_block_does_not_leak_template_comment(self):
        """Multi-line ``{# #}`` only suppresses the first line.

        Regression guard: an earlier rev of the subscribe popover
        wrapped its docstring in ``{# ... #}`` across multiple lines,
        which caused the second-through-last lines (and the closing
        ``#}``) to render visibly above the popover trigger. The
        codebase's project memo
        ``feedback_django_comment_leak.md`` calls this out
        explicitly — use ``{% comment %}`` for multi-line blocks.
        """
        response = self.client.get('/events')
        # The internal docstring keywords would appear on the page if
        # the wrapper reverted to a multi-line ``{# #}`` block.
        self.assertNotContains(response, 'subscribe-to-all-events popover')
        self.assertNotContains(response, 'navigator.clipboard.writeText;')
        # The closing brace must not leak either.
        self.assertNotContains(response, 'triple-click and copy by hand. #}')
