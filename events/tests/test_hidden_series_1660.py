"""Tests for the hidden-series visibility primitive (issue #1660).

Covers the model flag, the four independent listing/discovery query
points (``public_events_queryset`` and its callers, the calendar grid,
the ICS feed), the detail/recap/series-page entitlement gate, and the
dashboard "registered events" carve-out.

Related content and homepage/dashboard call-site coverage lives in
``content/tests`` (those views/services belong to the ``content`` app).
Sprint/course entitlement surfaces live in ``plans/tests`` and
``content/tests`` respectively.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Cohort, CohortEnrollment
from events.models import Event, EventSeries
from events.services.calendar_feed import feed_events_queryset
from events.services.series_entitlement import is_entitled_for_series
from events.services.time_windows import (
    past_events_queryset,
    public_events_queryset,
    registered_upcoming_events,
    upcoming_events_queryset,
)
from plans.models import Sprint, SprintEnrollment

User = get_user_model()


def _make_series(name='Buildcamp Office Hours', slug='buildcamp-oh', **kwargs):
    kwargs.setdefault('cadence', 'weekly')
    kwargs.setdefault('day_of_week', 0)
    kwargs.setdefault('start_time', datetime.time(17, 0))
    kwargs.setdefault('timezone', 'Europe/Berlin')
    return EventSeries.objects.create(name=name, slug=slug, **kwargs)


def _make_event(series, *, offset_days, status='upcoming', **kwargs):
    now = timezone.now()
    start = now + datetime.timedelta(days=offset_days)
    kwargs.setdefault('published', True)
    kwargs.setdefault('origin', 'studio')
    kwargs.setdefault('kind', 'standard')
    kwargs.setdefault('platform', 'zoom')
    kwargs.setdefault('timezone', 'Europe/Berlin')
    kwargs.setdefault('description', '')
    slug = kwargs.pop('slug', f'{series.slug}-{offset_days}-{status}')
    title = kwargs.pop('title', f'{series.name} — {offset_days}')
    return Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start,
        status=status,
        event_series=series,
        **kwargs,
    )


class EventSeriesVisibilityModelTest(TestCase):
    def test_default_visibility_is_public(self):
        series = _make_series()
        self.assertEqual(series.visibility, 'public')
        self.assertFalse(series.is_hidden)

    def test_hidden_visibility_sets_is_hidden(self):
        series = _make_series(visibility='hidden')
        self.assertTrue(series.is_hidden)


class PublicEventsQuerysetExclusionTest(TestCase):
    """The shared base every /events, member-API, and homepage surface uses."""

    @classmethod
    def setUpTestData(cls):
        cls.hidden_series = _make_series(visibility='hidden')
        cls.public_series = _make_series(
            name='Public Series', slug='public-series',
        )
        cls.hidden_upcoming = _make_event(
            cls.hidden_series, offset_days=3, status='upcoming',
        )
        cls.hidden_past = _make_event(
            cls.hidden_series, offset_days=-3, status='completed',
        )
        cls.public_upcoming = _make_event(
            cls.public_series, offset_days=3, status='upcoming',
        )
        cls.public_past = _make_event(
            cls.public_series, offset_days=-3, status='completed',
        )

    def test_public_events_queryset_excludes_hidden_series(self):
        pks = set(public_events_queryset().values_list('pk', flat=True))
        self.assertNotIn(self.hidden_upcoming.pk, pks)
        self.assertNotIn(self.hidden_past.pk, pks)
        self.assertIn(self.public_upcoming.pk, pks)
        self.assertIn(self.public_past.pk, pks)

    def test_upcoming_events_queryset_excludes_hidden_series(self):
        pks = set(upcoming_events_queryset().values_list('pk', flat=True))
        self.assertNotIn(self.hidden_upcoming.pk, pks)
        self.assertIn(self.public_upcoming.pk, pks)

    def test_past_events_queryset_excludes_hidden_series(self):
        pks = set(past_events_queryset().values_list('pk', flat=True))
        self.assertNotIn(self.hidden_past.pk, pks)
        self.assertIn(self.public_past.pk, pks)


class EventsCalendarGridHiddenSeriesTest(TestCase):
    """``events_calendar`` builds its own inline query (issue #1660)."""

    def test_calendar_grid_excludes_hidden_series_occurrence(self):
        hidden_series = _make_series(visibility='hidden')
        target_date = timezone.now().date().replace(day=15)
        event = Event.objects.create(
            title='Hidden session',
            slug='hidden-session-calendar',
            start_datetime=timezone.make_aware(
                datetime.datetime.combine(target_date, datetime.time(17, 0)),
            ),
            status='upcoming',
            event_series=hidden_series,
            published=True,
            origin='studio',
        )
        url = reverse(
            'events_calendar_month',
            kwargs={'year': target_date.year, 'month': target_date.month},
        )
        response = self.client.get(url)
        # No separate status-code assertion: a broken view would raise or
        # leave ``response.context`` unset, which the line below surfaces.
        self.assertNotIn(event, response.context['events_list'])


class CalendarFeedHiddenSeriesTest(TestCase):
    """``feed_events_queryset`` is an independent query (issue #1660)."""

    def test_feed_queryset_excludes_hidden_series_occurrence(self):
        hidden_series = _make_series(visibility='hidden')
        public_series = _make_series(name='Feed Public', slug='feed-public')
        hidden_event = _make_event(
            hidden_series, offset_days=2, status='upcoming',
        )
        public_event = _make_event(
            public_series, offset_days=2, status='upcoming',
        )
        pks = set(feed_events_queryset().values_list('pk', flat=True))
        self.assertNotIn(hidden_event.pk, pks)
        self.assertIn(public_event.pk, pks)

    def test_ics_feed_response_excludes_hidden_series_occurrence(self):
        hidden_series = _make_series(
            name='ICS Hidden', slug='ics-hidden', visibility='hidden',
        )
        hidden_event = _make_event(
            hidden_series, offset_days=2, status='upcoming',
            title='ICS Hidden Session',
        )
        response = self.client.get(reverse('events_calendar_feed'))
        # Combines the status-200 + content contract in one call.
        self.assertContains(response, 'BEGIN:VCALENDAR')
        body = response.content.decode('utf-8')
        self.assertNotIn(f'UID:event-{hidden_event.pk}', body)
        self.assertNotIn('ICS Hidden Session', body)


class RegisteredUpcomingEventsUnchangedTest(TestCase):
    """Issue #1660: dashboard's own-registration list stays unaffected."""

    def test_registrant_still_sees_own_hidden_series_registration(self):
        from events.models import EventRegistration

        hidden_series = _make_series(visibility='hidden')
        event = _make_event(hidden_series, offset_days=3, status='upcoming')
        user = User.objects.create_user(email='registrant@test.com')
        EventRegistration.objects.create(event=event, user=user)

        rows = registered_upcoming_events(user)
        self.assertIn(event, [row for row in rows])


class SeriesEntitlementHelperTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series(visibility='hidden')
        cls.staff = User.objects.create_user(
            email='staff-entitle@test.com', is_staff=True,
        )
        cls.enrolled_cohort_member = User.objects.create_user(
            email='cohort-member@test.com',
        )
        cls.enrolled_sprint_member = User.objects.create_user(
            email='sprint-member@test.com',
        )
        cls.non_entitled = User.objects.create_user(
            email='non-entitled@test.com',
        )

        from content.models import Course
        course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-1660', status='published',
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 1',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        CohortEnrollment.objects.create(
            cohort=cohort, user=cls.enrolled_cohort_member,
        )
        sprint = Sprint.objects.create(
            name='Buildcamp Sprint', slug='buildcamp-sprint-1660',
            start_date=datetime.date(2026, 9, 1),
            event_series=cls.series,
        )
        SprintEnrollment.objects.create(
            sprint=sprint, user=cls.enrolled_sprint_member,
        )

    def test_staff_always_entitled(self):
        self.assertTrue(is_entitled_for_series(self.staff, self.series))

    def test_anonymous_never_entitled(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(
            is_entitled_for_series(AnonymousUser(), self.series),
        )

    def test_cohort_enrolled_member_entitled(self):
        self.assertTrue(
            is_entitled_for_series(self.enrolled_cohort_member, self.series),
        )

    def test_sprint_enrolled_member_entitled(self):
        self.assertTrue(
            is_entitled_for_series(self.enrolled_sprint_member, self.series),
        )

    def test_non_entitled_authenticated_member_not_entitled(self):
        self.assertFalse(
            is_entitled_for_series(self.non_entitled, self.series),
        )

    def test_none_series_never_entitled(self):
        self.assertFalse(is_entitled_for_series(self.staff, None))


class EventDetailHiddenSeriesGateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series(visibility='hidden')
        cls.event = _make_event(cls.series, offset_days=3, status='upcoming')
        cls.staff = User.objects.create_user(
            email='detail-staff@test.com', is_staff=True,
        )
        cls.non_entitled = User.objects.create_user(
            email='detail-non-entitled@test.com',
        )

        from content.models import Course
        course = Course.objects.create(
            title='Detail Course', slug='detail-course-1660', status='published',
        )
        cls.cohort = Cohort.objects.create(
            course=course, name='Detail Cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        cls.entitled = User.objects.create_user(
            email='detail-entitled@test.com',
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.entitled)

    def _url(self):
        return reverse(
            'event_detail',
            kwargs={'event_id': self.event.pk, 'slug': self.event.slug},
        )

    def test_anonymous_gets_404(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_non_entitled_authenticated_gets_404(self):
        self.client.force_login(self.non_entitled)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_staff_gets_200(self):
        self.client.force_login(self.staff)
        response = self.client.get(self._url())
        self.assertContains(response, self.event.title)

    def test_entitled_cohort_member_gets_200(self):
        self.client.force_login(self.entitled)
        response = self.client.get(self._url())
        self.assertContains(response, self.event.title)

    def test_event_with_no_series_is_unaffected(self):
        standalone = Event.objects.create(
            title='Standalone', slug='standalone-1660',
            start_datetime=timezone.now() + datetime.timedelta(days=1),
            status='upcoming', published=True, origin='studio',
        )
        url = reverse(
            'event_detail',
            kwargs={'event_id': standalone.pk, 'slug': standalone.slug},
        )
        response = self.client.get(url)
        self.assertContains(response, standalone.title)


class EventRecapHiddenSeriesGateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series(visibility='hidden')
        cls.event = _make_event(
            cls.series, offset_days=-3, status='completed',
            recap_notes='## Notes\n\nWe covered a lot.',
        )
        cls.staff = User.objects.create_user(
            email='recap-staff@test.com', is_staff=True,
        )
        cls.non_entitled = User.objects.create_user(
            email='recap-non-entitled@test.com',
        )
        from content.models import Course
        course = Course.objects.create(
            title='Recap Course', slug='recap-course-1660', status='published',
        )
        cohort = Cohort.objects.create(
            course=course, name='Recap Cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        cls.entitled = User.objects.create_user(email='recap-entitled@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=cls.entitled)

    def _url(self):
        return reverse(
            'event_recap',
            kwargs={'event_id': self.event.pk, 'slug': self.event.slug},
        )

    def test_anonymous_gets_404_even_with_published_recap(self):
        self.assertTrue(self.event.has_recap)
        self.assertTrue(self.event.published)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_non_entitled_authenticated_gets_404(self):
        self.client.force_login(self.non_entitled)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_staff_gets_recap_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(self._url())
        self.assertContains(response, 'We covered a lot')

    def test_entitled_member_gets_recap_page(self):
        self.client.force_login(self.entitled)
        response = self.client.get(self._url())
        self.assertContains(response, 'We covered a lot')


class EventSeriesPublicHiddenGateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series(visibility='hidden')
        _make_event(cls.series, offset_days=3, status='upcoming')
        cls.staff = User.objects.create_user(
            email='series-staff@test.com', is_staff=True,
        )
        cls.non_entitled = User.objects.create_user(
            email='series-non-entitled@test.com',
        )
        from content.models import Course
        course = Course.objects.create(
            title='Series Course', slug='series-course-1660', status='published',
        )
        cohort = Cohort.objects.create(
            course=course, name='Series Cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=cls.series,
        )
        cls.entitled = User.objects.create_user(email='series-entitled@test.com')
        CohortEnrollment.objects.create(cohort=cohort, user=cls.entitled)

    def _url(self):
        return reverse(
            'event_series_public',
            kwargs={'series_id': self.series.pk, 'slug': self.series.slug},
        )

    def test_anonymous_gets_404(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_non_entitled_authenticated_gets_404(self):
        self.client.force_login(self.non_entitled)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_staff_gets_200(self):
        self.client.force_login(self.staff)
        response = self.client.get(self._url())
        self.assertContains(response, self.series.name)

    def test_entitled_member_gets_200(self):
        self.client.force_login(self.entitled)
        response = self.client.get(self._url())
        self.assertContains(response, self.series.name)


class RecapReadyHiddenSeriesTest(TestCase):
    """``notify_recap_ready`` still reaches hidden-series registrants."""

    def test_notify_recap_ready_succeeds_for_hidden_series_event(self):
        from events.models import EventRegistration
        from events.services.event_recap_notification import (
            notify_recap_ready,
        )

        series = _make_series(visibility='hidden')
        event = _make_event(
            series, offset_days=-3, status='completed',
            recap_notes='Session notes.',
        )
        registrant = User.objects.create_user(
            email='notify-hidden@test.com', email_verified=True,
        )
        EventRegistration.objects.create(event=event, user=registrant)

        summary = notify_recap_ready(event)
        self.assertEqual(summary['eligible'], 1)
        self.assertEqual(summary['notified'], 1)

        from notifications.models import Notification
        notification = Notification.objects.get(user=registrant)
        self.assertTrue(notification.url.endswith(event.get_recap_url()))
