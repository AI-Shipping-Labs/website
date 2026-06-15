"""Tests for whole-series registration (issue #857).

Covers:
- The ``enroll_user_in_series`` fan-out helper: upcoming-only filtering,
  tier access, capacity, idempotency, and the structured summary.
- The ``enroll_series_registrants_in_event`` auto-enroll helper for
  occurrences added after a user is already series-registered.
- The POST/DELETE ``/api/events/series/<slug>/register`` API contract:
  fan-out, idempotency, authenticated-only, cancel-keeps-past.
- The public series page register UI and per-occurrence states.
- That individual per-event registration stays independent.
- Auto-enroll hooked into the studio add-occurrence and API bulk paths.
- One summary confirmation email (not N).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN
from email_app.models import EmailLog
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.series_registration import (
    enroll_series_registrants_in_event,
    enroll_user_in_series,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_series(**kwargs):
    defaults = {
        'name': 'Weekly Office Hours',
        'slug': 'weekly-office-hours',
        'start_time': timezone.now().time(),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(kwargs)
    return EventSeries.objects.create(**defaults)


def _make_occurrence(series, *, offset_days, position, status='upcoming',
                     required_level=LEVEL_OPEN, slug=None):
    start = timezone.now() + timedelta(days=offset_days)
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=slug or f'{series.slug}-session-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        required_level=required_level,
        event_series=series,
        series_position=position,
    )


@tag('core')
class EnrollUserInSeriesTest(TierSetupMixin, TestCase):
    """The fan-out helper creates per-event rows for eligible occurrences."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def test_fans_out_to_all_upcoming_open_occurrences(self):
        series = _make_series()
        for i in range(1, 4):
            _make_occurrence(series, offset_days=7 * i, position=i)

        summary = enroll_user_in_series(self.user, series)

        self.assertEqual(summary['registered'], 3)
        self.assertEqual(summary['total_occurrences'], 3)
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 3,
        )

    def test_skips_past_draft_and_cancelled_occurrences(self):
        series = _make_series()
        _make_occurrence(series, offset_days=7, position=1)  # upcoming
        _make_occurrence(series, offset_days=-7, position=2)  # past
        _make_occurrence(
            series, offset_days=14, position=3, status='draft',
        )
        _make_occurrence(
            series, offset_days=21, position=4, status='cancelled',
        )

        summary = enroll_user_in_series(self.user, series)

        # Only the single upcoming, non-draft, non-cancelled occurrence.
        self.assertEqual(summary['registered'], 1)
        self.assertEqual(summary['total_occurrences'], 1)
        registered_positions = set(
            EventRegistration.objects.filter(user=self.user)
            .values_list('event__series_position', flat=True)
        )
        self.assertEqual(registered_positions, {1})

    def test_heavily_registered_occurrence_still_enrolls(self):
        """Issue #984: capacity removed — an occurrence with many existing
        registrations is no longer skipped; the user enrolls in every
        accessible upcoming occurrence and no ``skipped_full`` key exists."""
        series = _make_series()
        busy_event = _make_occurrence(
            series, offset_days=7, position=1,
        )
        other = User.objects.create_user(email='o@test.com', password='pass')
        EventRegistration.objects.create(event=busy_event, user=other)
        _make_occurrence(series, offset_days=14, position=2)

        summary = enroll_user_in_series(self.user, series)

        self.assertEqual(summary['registered'], 2)
        self.assertNotIn('skipped_full', summary)
        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=busy_event,
            ).exists()
        )

    def test_mixed_tier_partial_enroll(self):
        series = _make_series()
        basic_user = User.objects.create_user(
            email='basic@test.com', password='pass', email_verified=True,
        )
        basic_user.tier = self.basic_tier
        basic_user.save()
        # 4 open + 2 main-only.
        for i in range(1, 5):
            _make_occurrence(series, offset_days=7 * i, position=i)
        _make_occurrence(
            series, offset_days=35, position=5, required_level=LEVEL_MAIN,
        )
        _make_occurrence(
            series, offset_days=42, position=6, required_level=LEVEL_MAIN,
        )

        summary = enroll_user_in_series(basic_user, series)

        self.assertEqual(summary['registered'], 4)
        self.assertEqual(summary['skipped_no_access'], 2)
        self.assertEqual(summary['total_occurrences'], 6)

    def test_idempotent_skips_already_registered(self):
        series = _make_series()
        event = _make_occurrence(series, offset_days=7, position=1)
        EventRegistration.objects.create(event=event, user=self.user)

        summary = enroll_user_in_series(self.user, series)

        self.assertEqual(summary['registered'], 0)
        self.assertEqual(summary['skipped_already'], 1)
        self.assertEqual(
            EventRegistration.objects.filter(
                user=self.user, event=event,
            ).count(),
            1,
        )


@tag('core')
class EnrollSeriesRegistrantsInEventTest(TierSetupMixin, TestCase):
    """New occurrences auto-enroll existing series registrants."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def test_new_upcoming_occurrence_enrolls_registrant(self):
        series = _make_series()
        SeriesRegistration.objects.create(series=series, user=self.user)
        new_event = _make_occurrence(series, offset_days=7, position=1)

        enrolled = enroll_series_registrants_in_event(new_event)

        self.assertEqual(enrolled, 1)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=new_event, user=self.user,
            ).exists()
        )

    def test_draft_occurrence_enrolls_nobody(self):
        series = _make_series()
        SeriesRegistration.objects.create(series=series, user=self.user)
        draft_event = _make_occurrence(
            series, offset_days=7, position=1, status='draft',
        )

        enrolled = enroll_series_registrants_in_event(draft_event)

        self.assertEqual(enrolled, 0)
        self.assertFalse(
            EventRegistration.objects.filter(
                event=draft_event, user=self.user,
            ).exists()
        )

    def test_tier_locked_registrant_not_enrolled(self):
        series = _make_series()
        basic_user = User.objects.create_user(
            email='basic@test.com', password='pass', email_verified=True,
        )
        basic_user.tier = self.basic_tier
        basic_user.save()
        SeriesRegistration.objects.create(series=series, user=basic_user)
        main_only = _make_occurrence(
            series, offset_days=7, position=1, required_level=LEVEL_MAIN,
        )

        enrolled = enroll_series_registrants_in_event(main_only)

        self.assertEqual(enrolled, 0)
        self.assertFalse(
            EventRegistration.objects.filter(
                event=main_only, user=basic_user,
            ).exists()
        )

    def test_event_without_series_is_noop(self):
        start = timezone.now() + timedelta(days=7)
        loner = Event.objects.create(
            title='Standalone', slug='standalone',
            start_datetime=start, status='upcoming',
        )
        self.assertEqual(enroll_series_registrants_in_event(loner), 0)


@tag('core')
class SeriesRegistrationApiTest(TierSetupMixin, TestCase):
    """POST/DELETE /api/events/series/<slug>/register contract."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def setUp(self):
        self.series = _make_series()
        for i in range(1, 4):
            _make_occurrence(self.series, offset_days=7 * i, position=i)
        self.url = f'/api/events/series/{self.series.slug}/register'

    def test_anonymous_cannot_register_and_no_rows_created(self):
        before = SeriesRegistration.objects.count()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(SeriesRegistration.objects.count(), before)
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 0,
        )

    def test_unknown_series_returns_404(self):
        self.client.force_login(self.user)
        response = self.client.post('/api/events/series/nope/register')
        self.assertEqual(response.status_code, 404)

    def test_register_creates_flag_and_fans_out(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['status'], 'registered')
        self.assertEqual(body['summary']['registered'], 3)
        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 3,
        )

    def test_register_is_idempotent(self):
        self.client.force_login(self.user)
        self.client.post(self.url)
        rows_after_first = EventRegistration.objects.filter(
            user=self.user,
        ).count()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'already_registered')
        self.assertEqual(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).count(),
            1,
        )
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(),
            rows_after_first,
        )

    def test_register_sends_one_summary_email(self):
        self.client.force_login(self.user)
        before = EmailLog.objects.filter(
            email_type='series_registration',
        ).count()

        self.client.post(self.url)

        self.assertEqual(
            EmailLog.objects.filter(
                email_type='series_registration', user=self.user,
            ).count(),
            before + 1,
        )

    def test_cancel_removes_flag_and_future_keeps_past(self):
        # Past occurrence the user attended (via series).
        past = _make_occurrence(self.series, offset_days=-7, position=0,
                                slug='woh-past')
        EventRegistration.objects.create(event=past, user=self.user)

        self.client.force_login(self.user)
        self.client.post(self.url)
        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )
        # Future occurrences dropped.
        future_count = EventRegistration.objects.filter(
            user=self.user, event__series_position__gt=0,
        ).count()
        self.assertEqual(future_count, 0)
        # Past attendance preserved.
        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=past,
            ).exists()
        )

    def test_cancel_when_not_registered_returns_404(self):
        self.client.force_login(self.user)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 404)

    def test_per_event_registration_independent_of_series(self):
        """A single occurrence can be registered without a series flag."""
        self.client.force_login(self.user)
        single = Event.objects.filter(event_series=self.series).first()
        response = self.client.post(
            f'/api/events/{single.slug}/register',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=single, user=self.user,
            ).exists()
        )
        # No standing series flag was created by a single-event register.
        self.assertFalse(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )


@tag('core')
class SeriesPublicPageTest(TierSetupMixin, TestCase):
    """The public series page renders the register UI and states."""

    def setUp(self):
        self.series = _make_series()
        self.upcoming = _make_occurrence(self.series, offset_days=7, position=1)
        self.past = _make_occurrence(
            self.series, offset_days=-7, position=0, slug='woh-old',
        )
        self.url = self.series.get_absolute_url()

    def test_anonymous_sees_login_cta(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'series-register-login-cta')
        self.assertContains(response, 'Register for all upcoming sessions')

    def test_authenticated_unregistered_sees_register_button(self):
        user = User.objects.create_user(
            email='m@test.com', password='pass', email_verified=True,
        )
        user.tier = self.main_tier
        user.save()
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertFalse(response.context['is_series_registered'])
        self.assertContains(response, 'data-series-register')
        self.assertContains(response, 'series-register-button')

    def test_registered_user_sees_registered_state_and_cancel(self):
        user = User.objects.create_user(
            email='m@test.com', password='pass', email_verified=True,
        )
        user.tier = self.main_tier
        user.save()
        SeriesRegistration.objects.create(series=self.series, user=user)
        EventRegistration.objects.create(event=self.upcoming, user=user)
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertTrue(response.context['is_series_registered'])
        self.assertContains(response, "You're registered for this series")
        self.assertContains(response, 'data-series-cancel')
        # The upcoming occurrence shows a Registered chip.
        self.assertContains(response, 'series-event-state-registered')
        # The past occurrence shows the Past state.
        self.assertContains(response, 'series-event-state-past')


@tag('core')
class SeriesEntryPointTest(TierSetupMixin, TestCase):
    """A series occurrence in the events list links to the series screen."""

    def test_series_event_card_links_to_series_page(self):
        series = _make_series()
        _make_occurrence(series, offset_days=7, position=1)

        response = self.client.get('/events')

        self.assertEqual(response.status_code, 200)
        # The card's primary link points at the series page, not the
        # individual event detail.
        self.assertContains(response, series.get_absolute_url())


@tag('core')
class StudioAddOccurrenceAutoEnrollTest(TierSetupMixin, TestCase):
    """Studio add-occurrence + API bulk auto-enroll existing registrants."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
            email_verified=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        cls.member.tier = cls.main_tier
        cls.member.save()

    def test_studio_edit_publishing_occurrence_enrolls_registrant(self):
        series = _make_series()
        SeriesRegistration.objects.create(series=series, user=self.member)
        # New occurrence starts as a draft (not registrable yet).
        draft = _make_occurrence(
            series, offset_days=14, position=1, status='draft',
        )
        self.assertFalse(
            EventRegistration.objects.filter(
                event=draft, user=self.member,
            ).exists()
        )

        self.client.force_login(self.staff)
        start_local = draft.start_datetime.strftime('%d/%m/%Y')
        time_local = draft.start_datetime.strftime('%H:%M')
        response = self.client.post(
            f'/studio/events/{draft.pk}/edit',
            {
                'title': draft.title,
                'slug': draft.slug,
                'description': '',
                'platform': 'zoom',
                'timezone': 'UTC',
                'event_date': start_local,
                'event_time': time_local,
                'duration_hours': '1',
                'location': '',
                'status': 'upcoming',
                'required_level': '0',
                'tags': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'upcoming')
        self.assertTrue(
            EventRegistration.objects.filter(
                event=draft, user=self.member,
            ).exists()
        )
