"""Relocated public calendar-feed HTTP owners (#1483)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event


def _create_event(
    *,
    slug,
    title,
    status='upcoming',
    external_host='',
    required_level=0,
    published=True,
    start_datetime=None,
    description='',
    ics_sequence=0,
):
    if start_datetime is None:
        start_datetime = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        slug=slug,
        title=title,
        description=description or f'Body for {title}.',
        status=status,
        external_host=external_host,
        required_level=required_level,
        published=published,
        start_datetime=start_datetime,
        ics_sequence=ics_sequence,
    )


class AnonymousHttpFetchOfFeedTest(TestCase):
    """Owns anonymous feed status, Content-Type, and public VEVENT body.

    Relocated from Playwright ``TestAnonymousHttpFetchOfFeed``.
    """

    def test_feed_returns_200_and_includes_public_events(self):
        future = timezone.now() + timedelta(days=3)
        _create_event(
            slug='open-evt',
            title='Open Anonymous Event',
            start_datetime=future,
        )
        _create_event(
            slug='draft-evt-feed',
            title='Draft Should Not Appear',
            status='draft',
            start_datetime=future,
        )
        # Issue #726: gated events appear in the feed with a
        # ``[Members only]`` prefix on the SUMMARY; only draft /
        # cancelled events are excluded outright.
        _create_event(
            slug='gated-evt-feed',
            title='Gated Members Event',
            required_level=20,
            start_datetime=future,
        )

        response = self.client.get('/events/calendar.ics')
        self.assertEqual(
            response['Content-Type'], 'text/calendar; charset=utf-8',
        )

        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('BEGIN:VCALENDAR'))
        self.assertTrue(text.rstrip().endswith('END:VCALENDAR'))
        self.assertIn('Open Anonymous Event', text)
        self.assertNotIn('Draft Should Not Appear', text)
        self.assertNotIn('draft-evt-feed', text)
        self.assertIn('[Members only] Gated Members Event', text)


class EtagShortCircuitTest(TestCase):
    """Owns If-None-Match 304 then 200 after a feed-eligible edit.

    Relocated from Playwright ``TestEtagShortCircuit``.
    """

    def test_if_none_match_returns_304_then_200_after_edit(self):
        future = timezone.now() + timedelta(days=4)
        event = _create_event(
            slug='etag-evt',
            title='Etag Event Original',
            start_datetime=future,
        )

        response_a = self.client.get('/events/calendar.ics')
        etag = response_a['ETag']
        self.assertTrue(etag.startswith('"feed-'))

        response_b = self.client.get(
            '/events/calendar.ics', HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(response_b.status_code, 304)
        self.assertEqual(response_b.content, b'')

        edited = Event.objects.get(pk=event.pk)
        edited.title = 'Etag Event Updated'
        edited.save()

        response_c = self.client.get(
            '/events/calendar.ics', HTTP_IF_NONE_MATCH=etag,
        )
        self.assertNotEqual(response_c.status_code, 304)
        new_etag = response_c['ETag']
        self.assertNotEqual(new_etag, etag)
        self.assertIn(b'Etag Event Updated', response_c.content)


class EditsPropagateAsUpdatesNotDuplicatesTest(TestCase):
    """Owns one VEVENT per slug-UID after a title + sequence bump.

    Relocated from Playwright ``TestEditsPropagateAsUpdatesNotDuplicates``.
    """

    def test_edited_event_appears_once_with_higher_sequence(self):
        future = timezone.now() + timedelta(days=5)
        event = _create_event(
            slug='evt-demo',
            title='Demo Original',
            description='Stable demo body.',
            start_datetime=future,
            ics_sequence=1,
        )

        response_a = self.client.get('/events/calendar.ics')
        self.assertIn(b'SUMMARY:Demo Original', response_a.content)

        event = Event.objects.get(pk=event.pk)
        event.title = 'Demo Updated'
        event.ics_sequence = 2
        event.save()

        response_b = self.client.get('/events/calendar.ics')
        text = response_b.content.decode('utf-8')

        uid_line = 'UID:event-evt-demo@aishippinglabs.com'
        self.assertEqual(text.count(uid_line), 1)
        self.assertIn('SUMMARY:Demo Updated', text)
        self.assertNotIn('SUMMARY:Demo Original', text)
        self.assertIn('SEQUENCE:2', text)


class ExternalEventsAreMarkedInFeedTest(TestCase):
    """Owns Maven SUMMARY prefix, LOCATION, and platform detail URL.

    Relocated from Playwright ``TestExternalEventsAreMarkedInFeed``.
    """

    def test_maven_event_summary_location_url(self):
        future = timezone.now() + timedelta(days=2)
        event = _create_event(
            slug='maven-llm',
            title='LLM Engineering Cohort',
            external_host='Maven',
            start_datetime=future,
        )

        response = self.client.get('/events/calendar.ics')
        text = response.content.decode('utf-8')

        self.assertIn('[Hosted on Maven] LLM Engineering Cohort', text)
        self.assertIn('LOCATION:Maven', text)
        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        self.assertIn(event.get_absolute_url(), text)


class GatedAndDraftStayOutOfPublicFeedTest(TestCase):
    """Owns draft/cancelled exclusion and gated members-only SUMMARY.

    Relocated from Playwright ``TestGatedAndDraftStayOutOfPublicFeed``.
    """

    def test_only_open_published_event_appears(self):
        future = timezone.now() + timedelta(days=2)
        _create_event(
            slug='open-only', title='Open Free Event',
            start_datetime=future,
        )
        _create_event(
            slug='draft-only', title='Draft Only',
            status='draft', start_datetime=future,
        )
        _create_event(
            slug='cancelled-only', title='Cancelled Only',
            status='cancelled', start_datetime=future,
        )
        # Issue #726: gated events ARE included in the feed with a
        # ``[Members only]`` SUMMARY prefix; only draft/cancelled stay
        # out.
        _create_event(
            slug='main-only', title='Main Tier Only',
            required_level=20, start_datetime=future,
        )

        response = self.client.get('/events/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertIn('Open Free Event', text)
        self.assertIn('[Members only] Main Tier Only', text)
        for forbidden in (
            'Draft Only', 'draft-only',
            'Cancelled Only', 'cancelled-only',
        ):
            self.assertNotIn(forbidden, text)


class PerEventDownloadDropsMembersOnlyPrefixTest(TestCase):
    """Owns attendee-download title rules vs the public-feed prefix.

    Relocated from Playwright ``TestPerEventDownloadDropsMembersOnlyPrefix``.
    """

    def test_gated_event_download_has_no_members_only_prefix(self):
        future = timezone.now() + timedelta(days=3)
        _create_event(
            slug='exploring-vercel',
            title='Exploring Vercel',
            required_level=20,
            start_datetime=future,
        )

        response = self.client.get('/events/exploring-vercel/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertIn('SUMMARY:Exploring Vercel', text)
        self.assertNotIn('[Members only]', text)

        feed = self.client.get('/events/calendar.ics')
        self.assertIn(
            '[Members only] Exploring Vercel',
            feed.content.decode('utf-8'),
        )

    def test_gated_external_download_keeps_hosted_drops_members_only(self):
        future = timezone.now() + timedelta(days=3)
        _create_event(
            slug='maven-gated',
            title='Maven Gated Cohort',
            external_host='Maven',
            required_level=20,
            start_datetime=future,
        )

        response = self.client.get('/events/maven-gated/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertIn(
            'SUMMARY:[Hosted on Maven] Maven Gated Cohort', text,
        )
        self.assertNotIn('[Members only]', text)

    def test_open_event_download_is_plain_title(self):
        future = timezone.now() + timedelta(days=3)
        _create_event(
            slug='open-download',
            title='Open Download Event',
            start_datetime=future,
        )

        response = self.client.get('/events/open-download/calendar.ics')
        text = response.content.decode('utf-8')
        self.assertIn('SUMMARY:Open Download Event', text)
        self.assertNotIn('[Members only]', text)
