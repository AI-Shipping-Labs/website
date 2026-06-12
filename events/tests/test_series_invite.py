"""Tests for series subscriber calendar invites (issue #869).

Covers:
- ``generate_series_ics``: one VEVENT per event, a METHOD property, stable
  per-event UIDs, and per-event SEQUENCE.
- ``send_series_registration_invite``: attaches a multi-event REQUEST .ics
  covering the enrolled occurrences and logs ``series_registration``.
- ``send_series_update_to_subscribers``: re-issues a REQUEST invite to
  subscribers reflecting a changed/added occurrence's bumped SEQUENCE;
  per-recipient isolation; targeting only the given users.
- ``send_series_cancellation_to_subscribers``: sends a single-VEVENT
  CANCEL .ics with a bumped SEQUENCE to subscribers registered for the
  occurrence; only accessible subscribers; logs ``series_cancellation``.
- The SES kill-switch short-circuits every send to a synthetic id.
- The access-filtered subset: a free subscriber's invite carries only the
  occurrences they can access.
"""

import email as email_lib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from icalendar import Calendar

from content.access import LEVEL_MAIN, LEVEL_OPEN
from email_app.models import EmailLog
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.calendar_invite import generate_series_ics
from events.services.series_invite import (
    send_series_cancellation_to_subscribers,
    send_series_registration_invite,
    send_series_update_to_subscribers,
)
from events.services.series_registration import (
    enroll_series_registrants_in_event,
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
                     required_level=LEVEL_OPEN, ics_sequence=0, slug=None):
    start = timezone.now() + timedelta(days=offset_days)
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=slug or f'{series.slug}-session-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        required_level=required_level,
        ics_sequence=ics_sequence,
        event_series=series,
        series_position=position,
    )


def _parse(ics_bytes):
    return Calendar.from_ical(ics_bytes)


def _vevents(cal):
    return [c for c in cal.walk() if c.name == 'VEVENT']


def _ics_from_raw(raw):
    """Extract and decode the text/calendar attachment from a raw SES message.

    The body parts are base64-encoded in the MIME, so a substring search on
    the raw string would miss VEVENT/UID/SEQUENCE. Parse the message, pull
    the calendar part, decode it, and return ``(ics_text, method)``.
    """
    msg = email_lib.message_from_string(raw)
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == 'text/calendar':
            method = part.get_param('method')
            payload = part.get_payload(decode=True).decode('utf-8')
            return payload, method
    raise AssertionError('no text/calendar part in message')


@tag('core')
class GenerateSeriesIcsTest(TestCase):
    """The multi-event invite builder structure."""

    def setUp(self):
        self.series = _make_series()
        self.e1 = _make_occurrence(self.series, offset_days=7, position=1,
                                   ics_sequence=0, slug='woh-1')
        self.e2 = _make_occurrence(self.series, offset_days=14, position=2,
                                   ics_sequence=3, slug='woh-2')

    def test_one_vevent_per_event_with_method(self):
        cal = _parse(generate_series_ics([self.e1, self.e2], method='REQUEST'))
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        self.assertEqual(len(_vevents(cal)), 2)

    def test_each_vevent_has_stable_per_event_uid(self):
        cal = _parse(generate_series_ics([self.e1, self.e2]))
        uids = {str(v.get('uid')) for v in _vevents(cal)}
        self.assertEqual(uids, {
            'event-woh-1@aishippinglabs.com',
            'event-woh-2@aishippinglabs.com',
        })

    def test_each_vevent_carries_its_own_sequence(self):
        cal = _parse(generate_series_ics([self.e1, self.e2]))
        by_uid = {str(v.get('uid')): int(v.get('sequence')) for v in _vevents(cal)}
        self.assertEqual(by_uid['event-woh-1@aishippinglabs.com'], 0)
        self.assertEqual(by_uid['event-woh-2@aishippinglabs.com'], 3)

    def test_cancel_method_passthrough(self):
        cal = _parse(generate_series_ics([self.e1], method='CANCEL'))
        self.assertEqual(str(cal.get('method')), 'CANCEL')


@tag('core')
@override_settings(SES_ENABLED=True)
class SendSeriesRegistrationInviteTest(TierSetupMixin, TestCase):
    """Registration confirmation attaches a multi-event REQUEST invite."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        self.e1 = _make_occurrence(self.series, offset_days=7, position=1,
                                   slug='woh-a')
        self.e2 = _make_occurrence(self.series, offset_days=14, position=2,
                                   slug='woh-b')

    @patch('events.services.registration_email.boto3')
    def test_attaches_multi_event_request_ics(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'ses-123'}

        log = send_series_registration_invite(
            self.user, self.series, [self.e1, self.e2],
        )

        self.assertEqual(log.email_type, 'series_registration')
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        ics, method = _ics_from_raw(raw)
        self.assertEqual(method, 'REQUEST')
        cal = _parse(ics)
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        # Two VEVENTs, one per enrolled occurrence.
        self.assertEqual(len(_vevents(cal)), 2)
        uids = {str(v.get('uid')) for v in _vevents(cal)}
        self.assertEqual(uids, {
            'event-woh-a@aishippinglabs.com',
            'event-woh-b@aishippinglabs.com',
        })


@tag('core')
class SesKillSwitchTest(TierSetupMixin, TestCase):
    """All sends short-circuit to a synthetic id when SES is disabled."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        self.e1 = _make_occurrence(self.series, offset_days=7, position=1,
                                   slug='woh-k')
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        EventRegistration.objects.create(event=self.e1, user=self.user)

    @override_settings(SES_ENABLED=False)
    @patch('events.services.registration_email.boto3')
    def test_no_boto_call_and_synthetic_id_when_disabled(self, mock_boto3):
        log = send_series_registration_invite(self.user, self.series, [self.e1])
        send_series_update_to_subscribers(self.e1)
        send_series_cancellation_to_subscribers(self.e1)

        mock_boto3.client.assert_not_called()
        self.assertEqual(log.ses_message_id, 'ses-disabled-noop')
        # Each path still records a log row (the attempt) with a synthetic id.
        for etype in ('series_registration', 'series_update', 'series_cancellation'):
            self.assertTrue(
                EmailLog.objects.filter(
                    user=self.user, email_type=etype,
                    ses_message_id='ses-disabled-noop',
                ).exists(),
                msg=f'no synthetic-id log for {etype}',
            )


@tag('core')
@override_settings(SES_ENABLED=True)
class SendSeriesUpdateTest(TierSetupMixin, TestCase):
    """Time-change / addition fan-out to subscribers."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pass', email_verified=True,
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        self.e1 = _make_occurrence(self.series, offset_days=7, position=1,
                                   ics_sequence=2, slug='woh-u1')
        self.e2 = _make_occurrence(self.series, offset_days=14, position=2,
                                   slug='woh-u2')
        for user in (self.alice, self.bob):
            SeriesRegistration.objects.create(series=self.series, user=user)
            EventRegistration.objects.create(event=self.e1, user=user)
            EventRegistration.objects.create(event=self.e2, user=user)

    @patch('events.services.registration_email.boto3')
    def test_update_carries_bumped_sequence_and_request_method(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        sent = send_series_update_to_subscribers(self.e1)

        self.assertEqual(sent, 2)
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        ics, method = _ics_from_raw(raw)
        self.assertEqual(method, 'REQUEST')
        cal = _parse(ics)
        # The changed occurrence appears with its bumped SEQUENCE 2.
        by_uid = {
            str(v.get('uid')): int(v.get('sequence'))
            for v in _vevents(cal)
        }
        self.assertEqual(by_uid['event-woh-u1@aishippinglabs.com'], 2)
        self.assertEqual(
            EmailLog.objects.filter(email_type='series_update').count(), 2,
        )

    @patch('events.services.registration_email.boto3')
    def test_targets_only_given_user_ids(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        sent = send_series_update_to_subscribers(
            self.e1, user_ids=[self.alice.id],
        )

        self.assertEqual(sent, 1)
        self.assertTrue(
            EmailLog.objects.filter(
                email_type='series_update', user=self.alice,
            ).exists(),
        )
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='series_update', user=self.bob,
            ).exists(),
        )

    @patch('events.services.series_invite._send_raw_email')
    def test_per_recipient_failure_isolated(self, mock_send):
        # Alice's send raises; Bob's must still go through.
        def side_effect(to_email, **kwargs):
            if to_email == 'alice@test.com':
                raise RuntimeError('SES boom')
            return 'ok'

        mock_send.side_effect = side_effect

        sent = send_series_update_to_subscribers(self.e1)

        self.assertEqual(sent, 1)
        self.assertTrue(
            EmailLog.objects.filter(
                email_type='series_update', user=self.bob,
            ).exists(),
        )
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='series_update', user=self.alice,
            ).exists(),
        )

    @patch('events.services.registration_email.boto3')
    def test_skips_unsubscribed(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        self.bob.unsubscribed = True
        self.bob.save(update_fields=['unsubscribed'])

        sent = send_series_update_to_subscribers(self.e1)

        self.assertEqual(sent, 1)
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='series_update', user=self.bob,
            ).exists(),
        )


@tag('core')
@override_settings(SES_ENABLED=True)
class SendSeriesCancellationTest(TierSetupMixin, TestCase):
    """Cancellation fan-out to subscribers registered for the occurrence."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        # Cancelled occurrence with a bumped sequence (Studio bumps it).
        self.cancelled = _make_occurrence(
            self.series, offset_days=7, position=1, status='cancelled',
            ics_sequence=5, slug='woh-c1',
        )
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        EventRegistration.objects.create(event=self.cancelled, user=self.user)
        # ``other`` subscribes to the series but never had this occurrence
        # on their calendar (no EventRegistration) — they get no CANCEL.
        SeriesRegistration.objects.create(series=self.series, user=self.other)

    @patch('events.services.registration_email.boto3')
    def test_cancel_single_vevent_bumped_sequence(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        sent = send_series_cancellation_to_subscribers(self.cancelled)

        self.assertEqual(sent, 1)
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        ics, method = _ics_from_raw(raw)
        self.assertEqual(method, 'CANCEL')
        cal = _parse(ics)
        self.assertEqual(str(cal.get('method')), 'CANCEL')
        vevents = _vevents(cal)
        self.assertEqual(len(vevents), 1)
        self.assertEqual(int(vevents[0].get('sequence')), 5)
        self.assertEqual(
            str(vevents[0].get('uid')), 'event-woh-c1@aishippinglabs.com',
        )

    @patch('events.services.registration_email.boto3')
    def test_only_registered_subscribers_get_cancel(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        send_series_cancellation_to_subscribers(self.cancelled)

        self.assertTrue(
            EmailLog.objects.filter(
                email_type='series_cancellation', user=self.user,
            ).exists(),
        )
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='series_cancellation', user=self.other,
            ).exists(),
        )


@tag('core')
class AdditionAutoEnrollTriggerTest(TierSetupMixin, TestCase):
    """Auto-enrolling subscribers into a new occurrence re-invites them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='member@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        SeriesRegistration.objects.create(series=self.series, user=self.user)

    @patch('events.tasks.notify_series_invite.enqueue_series_update')
    def test_enroll_into_new_occurrence_enqueues_update_for_enrolled(
        self, mock_update,
    ):
        new_event = _make_occurrence(self.series, offset_days=7, position=9,
                                     slug='woh-new')

        enrolled = enroll_series_registrants_in_event(new_event)

        self.assertEqual(enrolled, 1)
        mock_update.assert_called_once()
        args = mock_update.call_args.args
        self.assertEqual(args[0], new_event.pk)
        # Scoped to the newly enrolled subscriber.
        self.assertEqual(list(args[1]), [self.user.id])

    @patch('events.tasks.notify_series_invite.enqueue_series_update')
    def test_no_update_when_nobody_newly_enrolled(self, mock_update):
        # User already registered for the occurrence — no new enrollment.
        existing = _make_occurrence(self.series, offset_days=7, position=8,
                                    slug='woh-existing')
        EventRegistration.objects.create(event=existing, user=self.user)

        enroll_series_registrants_in_event(existing)

        mock_update.assert_not_called()


@tag('core')
@override_settings(SES_ENABLED=True)
class AccessFilteredSubsetTest(TierSetupMixin, TestCase):
    """A subscriber's invite carries only the occurrences they can access."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_user = User.objects.create_user(
            email='free@test.com', password='pass', email_verified=True,
        )

    def setUp(self):
        self.series = _make_series()
        self.open1 = _make_occurrence(self.series, offset_days=7, position=1,
                                      required_level=LEVEL_OPEN, slug='woh-o1')
        self.open2 = _make_occurrence(self.series, offset_days=14, position=2,
                                      required_level=LEVEL_OPEN, slug='woh-o2')
        self.gated = _make_occurrence(self.series, offset_days=21, position=3,
                                      required_level=LEVEL_MAIN, slug='woh-g1')
        SeriesRegistration.objects.create(series=self.series, user=self.free_user)
        # The free user only ends up registered for the two open sessions.
        EventRegistration.objects.create(event=self.open1, user=self.free_user)
        EventRegistration.objects.create(event=self.open2, user=self.free_user)

    @patch('events.services.registration_email.boto3')
    def test_update_invite_excludes_inaccessible(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        send_series_update_to_subscribers(self.open1)

        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        ics, _ = _ics_from_raw(raw)
        cal = _parse(ics)
        uids = {str(v.get('uid')) for v in _vevents(cal)}
        self.assertIn('event-woh-o1@aishippinglabs.com', uids)
        self.assertIn('event-woh-o2@aishippinglabs.com', uids)
        self.assertNotIn('event-woh-g1@aishippinglabs.com', uids)
