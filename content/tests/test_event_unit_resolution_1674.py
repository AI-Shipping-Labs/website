"""Tests for kind='event' unit resolution at render time (issue #1674).

``Unit.session_position`` (not a stored FK) resolves to an ``events.Event``
per viewer/cohort at render time:

1. The viewer's own mode='cohort' cohort's series, at that position.
2. Otherwise, the most recent PAST mode='cohort' cohort's series with an
   Event at that position (the self-paced fallback path).
3. No match anywhere -> None (clean empty state).

Also covers that completing an event unit uses the ordinary manual
completion toggle and counts identically regardless of resolution path.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from content.services import completion as completion_service
from content.services.course_units import (
    build_unit_session_card_context,
    resolve_session_event,
)
from events.models import Event, EventSeries

User = get_user_model()


def _make_series(**kwargs):
    kwargs.setdefault('cadence', 'none')
    kwargs.setdefault('day_of_week', None)
    kwargs.setdefault('start_time', None)
    return EventSeries.objects.create(**kwargs)


def _make_event(*, series, position, when, status='upcoming', **kwargs):
    kwargs.setdefault('title', f'Session {position}')
    kwargs.setdefault('slug', f'session-{position}-{series.pk}-{when.timestamp():.0f}')
    kwargs.setdefault('published', True)
    kwargs.setdefault('origin', 'studio')
    return Event.objects.create(
        event_series=series, series_position=position, start_datetime=when,
        status=status, **kwargs,
    )


class ResolveSessionEventTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='resolve-course', status='published',
            required_level=0,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Week 4', slug='week-4', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.week, title='Live Q&A', slug='live-qa', sort_order=1,
            kind='event', session_position=4,
        )
        cls.user = User.objects.create_user(email='learner@test.com', password='pw')

    def _now(self):
        return datetime.datetime.now(tz=datetime.timezone.utc)

    def test_own_cohort_series_event_resolves_first(self):
        series = _make_series(name='OH', slug='resolve-series-1')
        cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
            event_series=series,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=cohort)
        event = _make_event(series=series, position=4, when=self._now() + datetime.timedelta(days=3))

        resolved = resolve_session_event(self.unit, self.user)
        self.assertEqual(resolved, event)

    def test_falls_back_to_most_recent_past_cohort(self):
        past_series = _make_series(name='OH past', slug='resolve-series-past')
        past_cohort = Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 3, 1),
            event_series=past_series,
        )
        past_event = _make_event(
            series=past_series, position=4,
            when=self._now() - datetime.timedelta(days=200), status='completed',
            recap_notes='Recap notes.',
        )
        del past_cohort  # only needed for the Cohort row to exist

        # User has no CohortEnrollment at all — the normal self-paced path.
        resolved = resolve_session_event(self.unit, self.user)
        self.assertEqual(resolved, past_event)

    def test_most_recent_past_cohort_wins_over_older_one(self):
        older_series = _make_series(name='Older', slug='resolve-series-older')
        Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 1),
            event_series=older_series,
        )
        older_event = _make_event(
            series=older_series, position=4,
            when=self._now() - datetime.timedelta(days=600), status='completed',
        )
        newer_series = _make_series(name='Newer', slug='resolve-series-newer')
        Cohort.objects.create(
            course=self.course, name='Cohort 2', mode='cohort',
            start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 3, 1),
            event_series=newer_series,
        )
        newer_event = _make_event(
            series=newer_series, position=4,
            when=self._now() - datetime.timedelta(days=200), status='completed',
        )

        resolved = resolve_session_event(self.unit, self.user)
        self.assertEqual(resolved, newer_event)
        self.assertNotEqual(resolved, older_event)

    def test_self_paced_cohort_member_falls_back_too(self):
        self_paced = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self_paced)
        past_series = _make_series(name='Fallback', slug='resolve-series-sp')
        Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 3, 1),
            event_series=past_series,
        )
        fallback_event = _make_event(
            series=past_series, position=4,
            when=self._now() - datetime.timedelta(days=200), status='completed',
        )
        resolved = resolve_session_event(self.unit, self.user)
        self.assertEqual(resolved, fallback_event)

    def test_no_event_anywhere_resolves_to_none(self):
        self.assertIsNone(resolve_session_event(self.unit, self.user))

    def test_no_match_clean_empty_state_context(self):
        self.assertIsNone(build_unit_session_card_context(self.unit, self.user))

    def test_draft_event_is_not_a_match(self):
        series = _make_series(name='Draft series', slug='resolve-series-draft')
        Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 3, 1),
            event_series=series,
        )
        _make_event(
            series=series, position=4,
            when=self._now() - datetime.timedelta(days=200), status='draft',
        )
        self.assertIsNone(resolve_session_event(self.unit, self.user))

    def test_hidden_fallback_event_is_not_exposed_without_entitlement(self):
        series = _make_series(
            name='Hidden fallback', slug='resolve-series-hidden-fallback',
            visibility='hidden',
        )
        Cohort.objects.create(
            course=self.course, name='Hidden Cohort', mode='cohort',
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 3, 1), event_series=series,
        )
        _make_event(
            series=series, position=4,
            when=self._now() - datetime.timedelta(days=200),
            status='completed',
        )

        self.assertIsNone(resolve_session_event(self.unit, self.user))

    def test_hidden_own_cohort_event_resolves_for_entitled_member(self):
        series = _make_series(
            name='Hidden own cohort', slug='resolve-series-hidden-own',
            visibility='hidden',
        )
        cohort = Cohort.objects.create(
            course=self.course, name='Hidden active cohort', mode='cohort',
            start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 11, 22), event_series=series,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=cohort)
        event = _make_event(
            series=series, position=4,
            when=self._now() + datetime.timedelta(days=3),
        )

        self.assertEqual(resolve_session_event(self.unit, self.user), event)


class EventUnitCompletionTest(TestCase):
    """Completing a kind='event' unit uses the ordinary manual toggle."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='event-completion-course', status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Week', slug='week', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Session', slug='session', sort_order=1,
            kind='event', session_position=1,
        )
        cls.user = User.objects.create_user(email='completer@test.com', password='pw')

    def test_mark_completed_counts_toward_total(self):
        self.assertFalse(completion_service.is_completed(self.user, self.unit))
        completion_service.mark_completed(self.user, self.unit)
        self.assertTrue(completion_service.is_completed(self.user, self.unit))
        self.assertEqual(self.course.completed_units(self.user), 1)


class SessionUnitPresentationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='ai-buildcamp',
            status='published', required_level=0,
        )
        cls.foundation = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundation',
            sort_order=1,
        )
        cls.session_module = Module.objects.create(
            course=cls.course, parent=cls.foundation, title='Session 1',
            slug='session', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.session_module, title='Session 1', slug='session',
            sort_order=1, kind='event', session_position=1,
        )
        cls.user = User.objects.create_user(
            email='session-presentation@test.com', password='pw',
        )
        cls.series = _make_series(
            name='Buildcamp office hours', slug='session-presentation-series',
            visibility='hidden',
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1), event_series=cls.series,
            external_key='4',
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort)
        cls.event = _make_event(
            series=cls.series, position=1,
            when=datetime.datetime.now(tz=datetime.timezone.utc)
                - datetime.timedelta(days=1),
            status='completed',
            title='AI Engineering Buildcamp — Office Hours — Session 1',
            description=(
                'Weekly office hours for cohort 4 of the AI Engineering '
                'Buildcamp (Maven course). Mondays 17:00 Europe/Berlin. '
                'Recordings and recaps are shared with enrolled cohort '
                'members. Hidden series: occurrences render only on the '
                'buildcamp course page for entitled members, never in public '
                'event listings.'
            ),
            recap_notes='Session 1 recap: we reviewed retrieval quality.',
            recording_url='https://www.youtube.com/watch?v=p64Pik3OeIA',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_session_lesson_hides_internal_note_and_keeps_event_details(self):
        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, 'data-testid="unit-session-content"')
        self.assertContains(response, 'data-testid="unit-session-event-metadata"')
        self.assertNotContains(response, 'data-testid="unit-session-recording-section"')
        self.assertNotContains(response, 'data-testid="unit-session-recap-section"')
        self.assertContains(response, self.event.title)
        self.assertContains(response, 'Weekly office hours for cohort 4')
        self.assertContains(response, 'Mondays 17:00 Europe/Berlin')
        self.assertContains(response, 'data-testid="unit-session-event-details"')
        self.assertContains(response, f'href="{self.event.get_absolute_url()}"')
        self.assertNotContains(response, 'Hidden series:')
        self.assertNotContains(response, 'public event listings')
        self.assertContains(response, 'Session 1 recap: we reviewed retrieval quality.')
        self.assertContains(response, 'data-testid="unit-session-recording"')
        self.assertContains(response, 'data-video-id="p64Pik3OeIA"')

    def test_upcoming_session_keeps_maven_registration_note(self):
        self.event.start_datetime = (
            datetime.datetime.now(tz=datetime.timezone.utc)
            + datetime.timedelta(days=3)
        )
        self.event.status = 'upcoming'
        self.event.save(update_fields=['start_datetime', 'status'])

        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, 'data-testid="unit-session-maven-registration"')
        self.assertContains(
            response,
            'Maven handles registration automatically; no separate registration is needed.',
        )
