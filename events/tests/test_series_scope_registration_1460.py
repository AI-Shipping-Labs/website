"""Registering for one session of a series registers the whole series (#1460).

Covers the whole-series default on ``POST /api/events/<slug>/register``,
the secondary ``scope="event"`` path, the new ``SeriesOccurrenceOptOut``
lifecycle on cancel, the one-email-per-registration rule, and the event
detail page copy the counts drive.
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
    SeriesOccurrenceOptOut,
    SeriesRegistration,
)
from events.services.cancel_token import generate_cancel_token
from events.services.occurrence_publication import (
    run_occurrence_publication_lifecycle,
)
from events.services.series_registration import (
    enroll_series_registrants_in_event,
    enroll_user_in_series,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()

SUMMARY_KEYS = {
    'registered',
    'skipped_already',
    'skipped_no_access',
    'skipped_opted_out',
    'total_occurrences',
}


def _make_series(name='AI Engineering Book Club', slug='book-club'):
    return EventSeries.objects.create(
        name=name,
        slug=slug,
        start_time=timezone.now().time(),
        timezone='Europe/Berlin',
    )


def _make_occurrence(series, *, position, offset_days=None, status='upcoming',
                     required_level=LEVEL_OPEN, slug=None, title=None):
    days = offset_days if offset_days is not None else position * 7
    start = timezone.now() + timedelta(days=days)
    return Event.objects.create(
        title=title or f'Week {position}',
        slug=slug or f'week-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        required_level=required_level,
        event_series=series,
        series_position=position,
    )


def _register(client, event, scope=None):
    if scope is None:
        return client.post(f'/api/events/{event.slug}/register')
    return client.post(
        f'/api/events/{event.slug}/register',
        data={'scope': scope},
        content_type='application/json',
    )


@tag('core')
class RegisterScopeSeriesDefaultTest(TierSetupMixin, TestCase):
    """The per-event endpoint defaults to whole-series registration."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        self.weeks = [
            _make_occurrence(self.series, position=i) for i in range(1, 5)
        ]
        self.client.force_login(self.user)

    def test_default_scope_creates_flag_and_registers_every_session(self):
        response = _register(self.client, self.weeks[0])

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )
        self.assertEqual(
            EventRegistration.objects.filter(
                user=self.user, event__event_series=self.series,
            ).count(),
            4,
        )

    def test_response_carries_series_slug_and_summary_buckets(self):
        response = _register(self.client, self.weeks[0])
        body = response.json()

        self.assertEqual(body['series_slug'], self.series.slug)
        self.assertEqual(set(body['summary']), SUMMARY_KEYS)
        self.assertEqual(body['summary']['registered'], 4)
        self.assertEqual(body['summary']['total_occurrences'], 4)
        self.assertEqual(body['summary']['skipped_opted_out'], 0)

    def test_scope_event_registers_only_this_session(self):
        response = _register(self.client, self.weeks[0], scope='event')

        self.assertEqual(response.status_code, 201)
        self.assertNotIn('summary', response.json())
        self.assertNotIn('series_slug', response.json())
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 1,
        )
        self.assertFalse(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )

    def test_unrecognised_scope_is_rejected(self):
        response = _register(self.client, self.weeks[0], scope='everything')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(SeriesRegistration.objects.count(), 0)

    def test_draft_sessions_are_excluded_from_the_fan_out(self):
        draft = _make_occurrence(
            self.series, position=5, status='draft', slug='week-5-draft',
        )

        _register(self.client, self.weeks[0])

        self.assertFalse(
            EventRegistration.objects.filter(
                user=self.user, event=draft,
            ).exists()
        )

    def test_past_sessions_are_never_registered(self):
        past = _make_occurrence(
            self.series, position=0, offset_days=-7, slug='week-0-past',
        )

        _register(self.client, self.weeks[0])

        self.assertFalse(
            EventRegistration.objects.filter(
                user=self.user, event=past,
            ).exists()
        )

    def test_already_registered_for_this_session_still_returns_409(self):
        EventRegistration.objects.create(event=self.weeks[0], user=self.user)

        response = _register(self.client, self.weeks[0])

        self.assertEqual(response.status_code, 409)


@tag('core')
class RegisterScopeStandaloneEventTest(TierSetupMixin, TestCase):
    """An event with no series behaves exactly as before."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        start = timezone.now() + timedelta(days=3)
        self.event = Event.objects.create(
            title='Standalone', slug='standalone',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', required_level=LEVEL_OPEN,
        )
        self.client.force_login(self.user)

    def test_single_row_no_flag_and_per_event_confirmation(self):
        response = _register(self.client, self.event)

        self.assertEqual(response.status_code, 201)
        self.assertNotIn('summary', response.json())
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 1,
        )
        self.assertEqual(SeriesRegistration.objects.count(), 0)
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.user, email_type='event_registration',
            ).count(),
            1,
        )

    def test_unregister_reports_no_series_opt_out(self):
        _register(self.client, self.event)

        response = self.client.delete(
            f'/api/events/{self.event.slug}/unregister',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['series_opt_out'])
        self.assertEqual(SeriesOccurrenceOptOut.objects.count(), 0)


@tag('core')
class RegisterScopeGatedSiblingsTest(TierSetupMixin, TestCase):
    """A free session in a mostly-paid series still sets the standing flag."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='free@test.com', password='pass', email_verified=True,
            tier=self.free_tier,
        )
        self.series = _make_series()
        self.free_session = _make_occurrence(self.series, position=1)
        self.gated = [
            _make_occurrence(
                self.series, position=i, required_level=LEVEL_MAIN,
            )
            for i in range(2, 5)
        ]
        self.client.force_login(self.user)

    def test_flag_created_and_gated_sessions_reported(self):
        response = _register(self.client, self.free_session)
        summary = response.json()['summary']

        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )
        self.assertEqual(summary['registered'], 1)
        self.assertEqual(summary['skipped_no_access'], 3)
        self.assertEqual(summary['total_occurrences'], 4)
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(), 1,
        )

    def test_tier_note_is_shown_before_and_after_registering(self):
        before = self.client.get(self.free_session.get_absolute_url())
        self.assertContains(before, 'data-testid="event-series-tier-note"')
        self.assertContains(
            before, '3 sessions in this series require Main.',
        )
        self.assertContains(before, 'href="/membership"')

        _register(self.client, self.free_session)

        after = self.client.get(self.free_session.get_absolute_url())
        self.assertContains(
            after, "You're registered for 1 of 4 sessions in "
            'AI Engineering Book Club',
        )
        self.assertContains(after, 'data-testid="event-series-tier-note"')
        self.assertContains(after, '3 sessions in this series require Main.')
        self.assertContains(after, 'href="/membership"')
        # The explainer must not contradict the "1 of 4" heading.
        self.assertContains(
            after, 'data-testid="event-series-registration-explainer"',
        )
        self.assertNotContains(
            after,
            "You're registered for every upcoming session in this series.",
        )


@tag('core')
class RegistrationEmailScopeTest(TierSetupMixin, TestCase):
    """Exactly one confirmation email per registration."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        self.client.force_login(self.user)

    def _counts(self):
        return (
            EmailLog.objects.filter(
                user=self.user, email_type='series_registration',
            ).count(),
            EmailLog.objects.filter(
                user=self.user, email_type='event_registration',
            ).count(),
        )

    def test_multi_session_series_sends_one_series_invite_only(self):
        first = _make_occurrence(self.series, position=1)
        _make_occurrence(self.series, position=2)
        _make_occurrence(self.series, position=3)

        _register(self.client, first)

        series_emails, event_emails = self._counts()
        self.assertEqual(series_emails, 1)
        self.assertEqual(event_emails, 0)

    def test_single_session_series_sends_the_per_event_confirmation(self):
        only = _make_occurrence(self.series, position=1)

        _register(self.client, only)

        series_emails, event_emails = self._counts()
        self.assertEqual(series_emails, 0)
        self.assertEqual(event_emails, 1)

    def test_scope_event_sends_the_per_event_confirmation(self):
        first = _make_occurrence(self.series, position=1)
        _make_occurrence(self.series, position=2)

        _register(self.client, first, scope='event')

        series_emails, event_emails = self._counts()
        self.assertEqual(series_emails, 0)
        self.assertEqual(event_emails, 1)


@tag('core')
class SeriesOccurrenceOptOutLifecycleTest(TierSetupMixin, TestCase):
    """Cancelling one session sticks, and can be undone."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        self.weeks = [
            _make_occurrence(self.series, position=i) for i in range(1, 5)
        ]
        self.client.force_login(self.user)
        _register(self.client, self.weeks[0])

    def _unregister(self, event):
        return self.client.delete(f'/api/events/{event.slug}/unregister')

    def test_unregister_records_opt_out_and_reports_it(self):
        response = self._unregister(self.weeks[2])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['series_opt_out'])
        opt_out = SeriesOccurrenceOptOut.objects.get(
            event=self.weeks[2], user=self.user,
        )
        self.assertEqual(opt_out.series, self.series)
        # The standing flag survives a per-session cancel.
        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).exists()
        )
        # Other sessions keep their registrations.
        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=self.weeks[3],
            ).exists()
        )

    def test_series_only_registrant_without_row_gets_200_and_opt_out(self):
        EventRegistration.objects.filter(
            user=self.user, event=self.weeks[1],
        ).delete()

        response = self._unregister(self.weeks[1])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['series_opt_out'])
        self.assertTrue(
            SeriesOccurrenceOptOut.objects.filter(
                event=self.weeks[1], user=self.user,
            ).exists()
        )

    def test_user_with_neither_registration_nor_flag_gets_404(self):
        stranger = User.objects.create_user(
            email='stranger@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.client.force_login(stranger)

        response = self._unregister(self.weeks[1])

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            SeriesOccurrenceOptOut.objects.filter(user=stranger).count(), 0,
        )

    def test_event_page_offers_registration_again_after_opt_out(self):
        self._unregister(self.weeks[2])

        response = self.client.get(self.weeks[2].get_absolute_url())

        self.assertEqual(response.context['registration_source'], 'none')
        self.assertTrue(response.context['is_series_registered'])
        self.assertContains(response, 'data-event-register-button')
        self.assertNotContains(
            response, 'data-testid="event-registered-confirmation"',
        )

    def test_registering_again_clears_the_opt_out(self):
        self._unregister(self.weeks[2])

        response = _register(self.client, self.weeks[2])

        self.assertEqual(response.status_code, 201)
        self.assertFalse(
            SeriesOccurrenceOptOut.objects.filter(
                event=self.weeks[2], user=self.user,
            ).exists()
        )
        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=self.weeks[2],
            ).exists()
        )

    def test_deleting_the_series_registration_clears_every_opt_out(self):
        self._unregister(self.weeks[2])

        self.client.delete(
            f'/api/events/series/{self.series.slug}/register',
        )

        self.assertEqual(
            SeriesOccurrenceOptOut.objects.filter(user=self.user).count(), 0,
        )

    def test_re_registering_for_the_series_clears_every_opt_out(self):
        self._unregister(self.weeks[2])
        self.client.delete(
            f'/api/events/series/{self.series.slug}/register',
        )
        SeriesOccurrenceOptOut.objects.create(
            series=self.series, user=self.user, event=self.weeks[2],
        )

        self.client.post(f'/api/events/series/{self.series.slug}/register')

        self.assertEqual(
            SeriesOccurrenceOptOut.objects.filter(user=self.user).count(), 0,
        )
        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=self.weeks[2],
            ).exists()
        )

    def test_signed_token_cancel_records_the_same_opt_out(self):
        registration = EventRegistration.objects.get(
            user=self.user, event=self.weeks[3],
        )
        token = generate_cancel_token(registration)
        self.client.logout()

        response = self.client.post(
            f'/api/events/{self.weeks[3].slug}/cancel-registration'
            f'?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SeriesOccurrenceOptOut.objects.filter(
                event=self.weeks[3], user=self.user,
            ).exists()
        )


@tag('core')
class OptOutRespectedByFanOutTest(TierSetupMixin, TestCase):
    """The fan-out helpers never resurrect a cancelled session."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        self.week1 = _make_occurrence(self.series, position=1)
        self.week2 = _make_occurrence(self.series, position=2)
        SeriesRegistration.objects.create(series=self.series, user=self.user)

    def test_enroll_user_in_series_skips_and_counts_opt_outs(self):
        SeriesOccurrenceOptOut.objects.create(
            series=self.series, user=self.user, event=self.week2,
        )

        summary = enroll_user_in_series(self.user, self.series)

        self.assertEqual(summary['registered'], 1)
        self.assertEqual(summary['skipped_opted_out'], 1)
        self.assertFalse(
            EventRegistration.objects.filter(
                user=self.user, event=self.week2,
            ).exists()
        )

    def test_enroll_series_registrants_skips_and_counts_opt_outs(self):
        new_session = _make_occurrence(self.series, position=3)
        SeriesOccurrenceOptOut.objects.create(
            series=self.series, user=self.user, event=new_session,
        )

        enrolled = enroll_series_registrants_in_event(new_session)

        self.assertEqual(enrolled, 0)
        self.assertEqual(enrolled.skipped_opted_out, 1)
        self.assertFalse(
            EventRegistration.objects.filter(
                user=self.user, event=new_session,
            ).exists()
        )


@tag('core')
class DraftPublicationAutoEnrolTest(TierSetupMixin, TestCase):
    """Publishing a draft session still auto-enrols series registrants."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        _make_occurrence(self.series, position=1)
        self.client.force_login(self.user)

    def test_publishing_a_draft_occurrence_registers_existing_subscribers(self):
        draft = _make_occurrence(
            self.series, position=2, status='draft', slug='week-2-draft',
        )
        _register(self.client, Event.objects.get(slug='week-1'))
        self.assertFalse(
            EventRegistration.objects.filter(
                user=self.user, event=draft,
            ).exists()
        )

        draft.status = 'upcoming'
        draft.save(update_fields=['status'])
        run_occurrence_publication_lifecycle(draft)

        self.assertTrue(
            EventRegistration.objects.filter(
                user=self.user, event=draft,
            ).exists()
        )


@tag('core')
class EventCardSeriesCopyTest(TierSetupMixin, TestCase):
    """The registration card renders the session-scoped controls."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series = _make_series()
        self.client.force_login(self.user)

    def test_multi_session_series_shows_both_controls_and_note(self):
        weeks = [_make_occurrence(self.series, position=i) for i in range(1, 5)]

        response = self.client.get(weeks[0].get_absolute_url())

        self.assertContains(
            response, 'data-testid="event-register-series-button"',
        )
        self.assertContains(response, 'Register for all 4 sessions')
        self.assertContains(
            response, 'data-testid="event-register-single-button"',
        )
        self.assertContains(response, 'Just this session')
        self.assertContains(
            response, 'data-testid="event-series-scope-note"',
        )
        self.assertContains(
            response,
            'This session is part of AI Engineering Book Club.',
        )

    def test_single_session_series_shows_plain_register_and_note(self):
        only = _make_occurrence(self.series, position=1)

        response = self.client.get(only.get_absolute_url())

        self.assertContains(response, 'data-testid="event-register-button"')
        self.assertNotContains(
            response, 'data-testid="event-register-single-button"',
        )
        self.assertContains(
            response, 'data-testid="event-series-scope-note"',
        )

    def test_registered_series_state_shows_summary_and_session_cancel(self):
        weeks = [_make_occurrence(self.series, position=i) for i in range(1, 5)]
        _register(self.client, weeks[0])

        response = self.client.get(weeks[2].get_absolute_url())

        self.assertContains(
            response, 'data-testid="event-registered-series-summary"',
        )
        self.assertContains(
            response,
            "You're registered for all 4 sessions in "
            'AI Engineering Book Club',
        )
        # The explainer may only claim every session when that is true.
        self.assertContains(
            response,
            "You're registered for every upcoming session in this series.",
        )
        self.assertContains(
            response, 'data-testid="event-cancel-session-button"',
        )
        self.assertContains(response, 'Cancel this session')

    def test_legacy_single_session_registrant_is_offered_the_series(self):
        weeks = [_make_occurrence(self.series, position=i) for i in range(1, 5)]
        EventRegistration.objects.create(event=weeks[0], user=self.user)

        response = self.client.get(weeks[0].get_absolute_url())

        self.assertContains(
            response, 'data-testid="event-register-rest-of-series-link"',
        )
        self.assertContains(
            response, 'Register for the rest of AI Engineering Book Club',
        )
        self.assertContains(response, self.series.get_absolute_url())
        # No standing flag: the card must not claim a series registration.
        self.assertContains(response, "You're registered!")
        self.assertNotContains(
            response, 'data-testid="event-registered-series-summary"',
        )
        self.assertNotContains(response, "You're registered for all 4 sessions")
        self.assertNotContains(response, "You're registered for 1 of 4 sessions")
        self.assertNotContains(
            response, 'data-testid="event-series-registration-explainer"',
        )

    def test_opted_out_sibling_reports_the_real_registered_count(self):
        weeks = [_make_occurrence(self.series, position=i) for i in range(1, 5)]
        _register(self.client, weeks[0])
        self.client.delete(f'/api/events/{weeks[2].slug}/unregister')

        response = self.client.get(weeks[3].get_absolute_url())

        self.assertContains(
            response,
            "You're registered for 3 of 4 sessions in "
            'AI Engineering Book Club',
        )
        self.assertNotContains(response, "You're registered for all 4 sessions")
        # The explainer must agree with the heading.
        self.assertContains(
            response, 'data-testid="event-series-registration-explainer"',
        )
        self.assertNotContains(
            response,
            "You're registered for every upcoming session in this series.",
        )
        self.assertContains(
            response,
            'New sessions added to the series are registered automatically.',
        )

    def test_series_only_registrant_without_rows_counts_this_session(self):
        weeks = [_make_occurrence(self.series, position=i) for i in range(1, 5)]
        SeriesRegistration.objects.create(series=self.series, user=self.user)

        response = self.client.get(weeks[1].get_absolute_url())

        self.assertEqual(response.context['registration_source'], 'series')
        self.assertContains(
            response,
            "You're registered for 1 of 4 sessions in "
            'AI Engineering Book Club',
        )
        self.assertNotContains(response, "You're registered for all 4 sessions")

    def test_standalone_event_has_no_series_controls(self):
        start = timezone.now() + timedelta(days=2)
        event = Event.objects.create(
            title='Standalone', slug='standalone-card',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', required_level=LEVEL_OPEN,
        )

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, 'data-testid="event-register-button"')
        self.assertNotContains(
            response, 'data-testid="event-register-single-button"',
        )
        self.assertNotContains(
            response, 'data-testid="event-series-scope-note"',
        )


@tag('core')
class AnonymousSeriesSignupTest(TierSetupMixin, TestCase):
    """The anonymous email form applies the same whole-series default."""

    def setUp(self):
        super().setUp()
        self.series = _make_series(name='Book Club', slug='anon-book-club')
        self.sessions = [
            _make_occurrence(self.series, position=i) for i in range(1, 4)
        ]

    def test_form_discloses_the_series_scope(self):
        response = self.client.get(self.sessions[1].get_absolute_url())

        self.assertContains(
            response, 'data-testid="event-anonymous-series-scope-note"',
        )
        self.assertContains(response, 'This session is part of Book Club.')
        self.assertContains(response, 'covers all 3 sessions')

    def test_signup_creates_the_flag_and_registers_free_sessions(self):
        response = self.client.post(
            f'/api/events/{self.sessions[1].slug}/register',
            data={'email': 'newcomer@test.com'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='newcomer@test.com')
        self.assertTrue(
            SeriesRegistration.objects.filter(
                series=self.series, user=user,
            ).exists()
        )
        self.assertEqual(
            EventRegistration.objects.filter(user=user).count(), 3,
        )
        self.assertEqual(response.json()['series_slug'], self.series.slug)
