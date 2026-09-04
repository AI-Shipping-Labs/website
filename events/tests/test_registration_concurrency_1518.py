"""Conflict-safe event registration under unique-constraint collisions (#1518).

SQLite serializes writes, so thread-only tests can miss the race. These
cases inject ``IntegrityError`` on insert (and overlap two POSTs) so they
fail if a unique ``(event, user)`` / ``(series, user)`` collision 500s or
creates a second row.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import MemberAPIKey
from analytics.models import UserActivity
from content.access import LEVEL_OPEN
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.registration import (
    get_or_create_event_registration,
    get_or_create_series_registration,
)
from events.services.series_registration import enroll_user_in_series
from tests.fixtures import TierSetupMixin

User = get_user_model()

def _unique_event_registration(*args, **kwargs):
    raise IntegrityError(
        'UNIQUE constraint failed: events_eventregistration.event_id, '
        'events_eventregistration.user_id',
    )


def _unique_series_registration(*args, **kwargs):
    raise IntegrityError(
        'UNIQUE constraint failed: events_seriesregistration.series_id, '
        'events_seriesregistration.user_id',
    )


def _upcoming_event(**kwargs):
    start = timezone.now() + timedelta(days=7)
    defaults = {
        'title': 'Open Call',
        'slug': 'open-call-1518',
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'upcoming',
        'required_level': LEVEL_OPEN,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _series_with_occurrences(n=3, slug='series-1518'):
    series = EventSeries.objects.create(
        name='Concurrency Series',
        slug=slug,
        start_time=timezone.now().time(),
        timezone='Europe/Berlin',
    )
    events = []
    for position in range(1, n + 1):
        events.append(_upcoming_event(
            title=f'Session {position}',
            slug=f'{slug}-session-{position}',
            start_datetime=timezone.now() + timedelta(days=7 * position),
            end_datetime=timezone.now() + timedelta(days=7 * position, hours=1),
            event_series=series,
            series_position=position,
        ))
    return series, events


def _activity_count(user, event=None):
    qs = UserActivity.objects.filter(
        user=user, event_type=UserActivity.EVENT_EVENT_REGISTER,
    )
    if event is not None:
        qs = qs.filter(object_id=event.slug)
    return qs.count()


@tag('core')
class GetOrCreateEventRegistrationTest(TierSetupMixin, TestCase):
    """The insert helper is the uniqueness guard, not exists()-then-create."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='helper@test.com', password='pass', email_verified=True,
        )
        cls.event = _upcoming_event()

    def test_first_insert_records_activity(self):
        registration, created = get_or_create_event_registration(
            self.event, self.user,
        )

        self.assertTrue(created)
        self.assertEqual(registration.event, self.event)
        self.assertEqual(registration.user, self.user)
        self.assertEqual(_activity_count(self.user, self.event), 1)

    def test_integrity_error_returns_existing_without_activity(self):
        existing = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        UserActivity.objects.filter(user=self.user).delete()

        with patch.object(
            EventRegistration.objects,
            'create',
            side_effect=_unique_event_registration,
        ):
            registration, created = get_or_create_event_registration(
                self.event, self.user,
            )

        self.assertFalse(created)
        self.assertEqual(registration.pk, existing.pk)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )
        self.assertEqual(_activity_count(self.user, self.event), 0)

    def test_unique_event_user_constraint_still_enforced(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventRegistration.objects.create(event=self.event, user=self.user)


@tag('core')
class GetOrCreateSeriesRegistrationTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='series-helper@test.com', password='pass',
            email_verified=True,
        )
        cls.series = EventSeries.objects.create(
            name='Flag Series', slug='flag-series-1518',
        )

    def test_integrity_error_returns_existing_flag(self):
        existing = SeriesRegistration.objects.create(
            series=self.series, user=self.user,
        )

        with patch.object(
            SeriesRegistration.objects,
            'create',
            side_effect=_unique_series_registration,
        ):
            registration, created = get_or_create_series_registration(
                self.series, self.user,
            )

        self.assertFalse(created)
        self.assertEqual(registration.pk, existing.pk)
        self.assertEqual(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).count(),
            1,
        )

    def test_unique_series_user_constraint_still_enforced(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SeriesRegistration.objects.create(series=self.series, user=self.user)


@tag('core')
class AuthenticatedRegisterCollisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='auth-race@test.com', password='pass', email_verified=True,
        )
        self.event = _upcoming_event(slug='auth-race-1518')
        self.client.force_login(self.user)

    def test_injected_integrity_error_returns_409_not_500(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        UserActivity.objects.filter(user=self.user).delete()

        with (
            patch(
                'events.views.api.has_event_registration',
                return_value=False,
            ),
            patch.object(
                EventRegistration.objects,
                'create',
                side_effect=_unique_event_registration,
            ),
            patch(
                'events.views.api._send_registration_emails',
            ) as send_email,
        ):
            response = self.client.post(
                f'/api/events/{self.event.slug}/register',
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'Already registered')
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )
        self.assertEqual(_activity_count(self.user, self.event), 0)
        send_email.assert_not_called()

    def test_overlapping_posts_never_500_and_keep_one_row(self):
        first = self.client.post(f'/api/events/{self.event.slug}/register')
        second = self.client.post(f'/api/events/{self.event.slug}/register')

        self.assertNotEqual(first.status_code, 500)
        self.assertNotEqual(second.status_code, 500)
        self.assertEqual({first.status_code, second.status_code}, {201, 409})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['error'], 'Already registered')
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )
        self.assertEqual(_activity_count(self.user, self.event), 1)


@tag('core')
class AnonymousRegisterCollisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.event = _upcoming_event(slug='anon-race-1518')
        self.user = User.objects.create_user(
            email='visitor@test.com', password='pass', email_verified=True,
        )

    def _post(self, **extra):
        return self.client.post(
            f'/api/events/{self.event.slug}/register',
            data=json.dumps({'email': self.user.email}),
            content_type='application/json',
            **extra,
        )

    @patch('events.views.api._send_event_verification_email')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_injected_integrity_error_returns_201_already_registered(
        self, mock_reg_email, mock_verify,
    ):
        EventRegistration.objects.create(event=self.event, user=self.user)

        with (
            patch(
                'events.views.api.get_event_registration',
                return_value=None,
            ),
            patch.object(
                EventRegistration.objects,
                'create',
                side_effect=_unique_event_registration,
            ),
        ):
            response = self._post(REMOTE_ADDR='198.51.100.10')

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body['already_registered'])
        self.assertEqual(body['status'], 'registered')
        self.assertNotIn('email', body)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).count(),
            1,
        )
        mock_reg_email.assert_not_called()
        mock_verify.assert_not_called()


@tag('core')
class SeriesRegisterCollisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='series-race@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series, self.occurrences = _series_with_occurrences()
        self.url = f'/api/events/series/{self.series.slug}/register'
        self.client.force_login(self.user)

    @patch('events.services.series_invite.send_series_registration_invite')
    def test_injected_integrity_error_returns_200_already_registered(
        self, mock_invite,
    ):
        SeriesRegistration.objects.create(series=self.series, user=self.user)

        with (
            patch(
                'events.views.api.has_series_registration',
                return_value=False,
            ),
            patch.object(
                SeriesRegistration.objects,
                'create',
                side_effect=_unique_series_registration,
            ),
        ):
            response = self.client.post(self.url)

        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(response.json()['status'], 'already_registered')
        self.assertEqual(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).count(),
            1,
        )
        self.assertEqual(
            EventRegistration.objects.filter(user=self.user).count(),
            0,
        )
        mock_invite.assert_not_called()

    def test_overlapping_series_posts_keep_one_flag_and_one_row_each(self):
        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.status_code, 201)
        self.assertNotEqual(second.status_code, 500)
        self.assertEqual(second.json()['status'], 'already_registered')
        self.assertEqual(
            SeriesRegistration.objects.filter(
                series=self.series, user=self.user,
            ).count(),
            1,
        )
        for event in self.occurrences:
            self.assertEqual(
                EventRegistration.objects.filter(
                    event=event, user=self.user,
                ).count(),
                1,
            )
        self.assertEqual(_activity_count(self.user), 3)


@tag('core')
class MemberApiRegisterCollisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.member = User.objects.create_user(
            email='member-race@test.com', password='pass',
            tier=self.main_tier,
        )
        _key, self.plaintext = MemberAPIKey.create_for_user(
            user=self.member, name='events-1518',
        )
        self.event = _upcoming_event(
            slug='member-race-1518', required_level=LEVEL_OPEN,
        )

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.plaintext}'}

    def test_injected_integrity_error_returns_409_not_500(self):
        EventRegistration.objects.create(event=self.event, user=self.member)

        with (
            patch(
                'member_api.views.events.has_event_registration',
                return_value=False,
            ),
            patch.object(
                EventRegistration.objects,
                'create',
                side_effect=_unique_event_registration,
            ),
            patch(
                'member_api.views.events._send_registration_emails',
            ) as send_email,
        ):
            response = self.client.post(
                f'/member-api/v1/events/{self.event.id}/register',
                data={},
                content_type='application/json',
                **self._auth(),
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'already_registered')
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.member,
            ).count(),
            1,
        )
        send_email.assert_not_called()


@tag('core')
class SeriesFanOutCollisionTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email='fanout-race@test.com', password='pass', email_verified=True,
            tier=self.main_tier,
        )
        self.series, self.occurrences = _series_with_occurrences(
            slug='fanout-1518',
        )
        self.first, self.second, self.third = self.occurrences

    def test_collision_on_third_occurrence_does_not_abort_or_duplicate(self):
        # Sibling request already wrote the third row. Hide it from the
        # exists() snapshot so the fan-out still attempts the insert.
        EventRegistration.objects.create(event=self.third, user=self.user)
        real_create = EventRegistration.objects.create
        real_filter = EventRegistration.objects.filter

        def hide_sibling_from_snapshot(*args, **kwargs):
            qs = real_filter(*args, **kwargs)
            if 'event__in' in kwargs and kwargs.get('user') == self.user:
                return qs.none()
            return qs

        def create_third_collides(**kwargs):
            if kwargs['event'].pk == self.third.pk:
                _unique_event_registration()
            return real_create(**kwargs)

        with (
            patch.object(
                EventRegistration.objects,
                'filter',
                hide_sibling_from_snapshot,
            ),
            patch.object(
                EventRegistration.objects,
                'create',
                side_effect=create_third_collides,
            ),
        ):
            summary = enroll_user_in_series(self.user, self.series)

        self.assertEqual(summary['registered'], 2)
        self.assertEqual(summary['skipped_already'], 1)
        self.assertEqual(
            {event.pk for event in summary['new_events']},
            {self.first.pk, self.second.pk},
        )
        for event in self.occurrences:
            self.assertEqual(
                EventRegistration.objects.filter(
                    event=event, user=self.user,
                ).count(),
                1,
            )
        self.assertEqual(_activity_count(self.user, self.first), 1)
        self.assertEqual(_activity_count(self.user, self.second), 1)
        self.assertEqual(_activity_count(self.user, self.third), 0)
