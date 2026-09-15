"""Sprint call-schedule leak fix + recap link (issue #1660).

Extends the coverage in ``plans.tests.test_sprint_event_series`` (which
only exercised ``upcoming``/no-status-filter fixtures) with the
draft/cancelled leak fix and the new recap link.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.models import Event, EventSeries
from plans.models import Sprint

User = get_user_model()


def _make_series(name='Sprint Calls', slug='sprint-calls-1660'):
    return EventSeries.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _make_event(series, *, position, status='upcoming', **kwargs):
    base = datetime.datetime(2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc)
    start = base + datetime.timedelta(days=7 * (position - 1))
    kwargs.setdefault('published', True)
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=f'{series.slug}-session-{position}',
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        timezone='Europe/Berlin',
        status=status,
        origin='studio',
        event_series=series,
        series_position=position,
        **kwargs,
    )


class SprintCallScheduleHiddenStatusTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series()
        cls.draft_event = _make_event(cls.series, position=1, status='draft')
        cls.cancelled_event = _make_event(
            cls.series, position=2, status='cancelled',
        )
        cls.completed_event = _make_event(
            cls.series, position=3, status='completed',
            recap_notes='Notes from the completed call.',
        )
        cls.upcoming_event = _make_event(
            cls.series, position=4, status='upcoming',
        )
        cls.sprint = Sprint.objects.create(
            name='Buildcamp sprint', slug='buildcamp-sprint-1660-status',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_series=cls.series,
        )
        cls.staff = User.objects.create_user(
            email='sprint-calls-staff@test.com', is_staff=True,
        )

    def _url(self):
        return reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})

    def test_draft_and_cancelled_calls_never_render(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="sprint-call-entry"', count=2)
        self.assertNotContains(response, self.draft_event.get_absolute_url())
        self.assertNotContains(response, self.cancelled_event.get_absolute_url())
        self.assertContains(response, self.completed_event.get_absolute_url())
        self.assertContains(response, self.upcoming_event.get_absolute_url())

    def test_draft_and_cancelled_calls_hidden_even_for_staff(self):
        # Issue #1660 spec: no staff-preview precedent exists for this
        # filter, so it applies unconditionally.
        self.client.force_login(self.staff)
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="sprint-call-entry"', count=2)
        self.assertNotContains(response, self.draft_event.get_absolute_url())
        self.assertNotContains(response, self.cancelled_event.get_absolute_url())

    def test_recap_link_present_for_completed_call_with_recap(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="sprint-call-recap"')
        self.assertContains(response, self.completed_event.get_recap_url())

    def test_no_recap_link_when_call_has_no_recap(self):
        no_recap_series = _make_series(
            name='No Recap Series', slug='no-recap-series-1660',
        )
        no_recap_event = _make_event(
            no_recap_series, position=1, status='completed',
        )
        sprint = Sprint.objects.create(
            name='No recap sprint', slug='no-recap-sprint-1660',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_series=no_recap_series,
        )
        self.assertFalse(no_recap_event.has_recap)
        response = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )
        self.assertNotContains(response, 'data-testid="sprint-call-recap"')
