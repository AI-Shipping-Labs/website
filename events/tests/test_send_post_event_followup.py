"""Tests for the post-event follow-up background tasks (issue #680).

Coverage:

- ``enqueue_post_event_followup`` defers via ``jobs.tasks.async_task``.
- ``send_post_event_followup_fanout`` enqueues one stage-2 task per
  registration.
- ``send_post_event_followup_one`` is idempotent via
  ``EventReminderLog(interval='followup')`` (second pass returns
  ``already_sent``).
- Unsubscribed users still receive the follow-up (transactional gate).
- A local refusal logs via ``logger.exception`` and returns ``errored``
  without blocking subsequent per-user tasks.
- The ``feedback_url`` context key is populated when the issue #679
  surface is available and is absent otherwise.
- The generic-fallback summary is substituted when
  ``event.post_event_summary`` is blank.
- ``classify_email_type('post_event_followup')`` returns
  ``transactional``.

Since A1.2 slice 3 the send records a durable ``EmailDelivery`` and the
provider send happens from the delivery worker, so content assertions
drain pending deliveries through a stub SES client.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from email_app.testing import StubSESClient, deliver_pending_mail
from events.models import Event, EventRegistration
from events.tasks.send_post_event_followup import (
    enqueue_post_event_followup,
    send_post_event_followup_fanout,
    send_post_event_followup_one,
)
from notifications.models import EventReminderLog

User = get_user_model()


def _drain_and_get_sent_html():
    """Drain pending deliveries through a stub SES and return the HTML."""
    stub = StubSESClient()
    with patch(
        'community_base.mail.backends.ses_local.configured_client',
        return_value=stub,
    ):
        deliver_pending_mail()
    assert len(stub.calls) == 1, stub.calls
    return stub.calls[0]['Content']['Simple']['Body']['Html']['Data']


class EnqueuePostEventFollowupTest(TestCase):
    """The enqueue helper defers via ``async_task`` with the dotted path."""

    @patch('jobs.tasks.helpers.q_async_task')
    def test_enqueue_calls_async_task_with_dotted_path(self, mock_q):
        mock_q.return_value = 'task-id'
        enqueue_post_event_followup(42)

        self.assertEqual(mock_q.call_count, 1)
        args = mock_q.call_args.args
        # The worker resolves the function from its dotted path; no
        # implicit import contract.
        self.assertEqual(
            args[0],
            'events.tasks.send_post_event_followup.send_post_event_followup_fanout',
        )
        self.assertEqual(args[1], 42)


class FanoutTest(TestCase):
    """The fan-out enqueues one stage-2 task per registration."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Recap fanout',
            slug='recap-fanout',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=recap-fanout',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_enqueues_one_per_registration(self, mock_q):
        mock_q.return_value = 'task-id'
        for i in range(3):
            user = User.objects.create_user(email=f'fan{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)

        send_post_event_followup_fanout(self.event.pk)

        self.assertEqual(mock_q.call_count, 3)
        for call in mock_q.call_args_list:
            args = call.args
            self.assertEqual(
                args[0],
                'events.tasks.send_post_event_followup.send_post_event_followup_one',
            )
            self.assertEqual(args[1], self.event.pk)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_missing_event_returns_skipped(self, mock_q):
        mock_q.return_value = 'task-id'
        result = send_post_event_followup_fanout(999_999)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        self.assertEqual(mock_q.call_count, 0)


class SendPostEventFollowupOneTest(TestCase):
    """Stage-2 per-user send: dedup, delivery record, transactional."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Recap one-shot',
            slug='recap-one-shot',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=recap-one',
            post_event_summary='Thanks for joining the recap test session.',
        )
        cls.user = User.objects.create_user(email='one@test.com')
        cls.unsub_user = User.objects.create_user(
            email='unsub@test.com', unsubscribed=True,
        )

    def setUp(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        EventRegistration.objects.create(event=self.event, user=self.unsub_user)

    def test_send_records_delivery_writes_email_log_and_dedup_row(self):
        result = send_post_event_followup_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'sent')
        delivery = EmailDelivery.objects.get(
            purpose='post_event_followup', recipient_user=self.user,
        )
        self.assertEqual(
            delivery.idempotency_key,
            f'post_event_followup:{self.event.pk}:{self.user.pk}',
        )
        deliver_pending_mail()
        self.assertTrue(
            EmailLog.objects.filter(
                email_type='post_event_followup', user=self.user,
            ).exists(),
        )
        self.assertTrue(
            EventReminderLog.objects.filter(
                event=self.event, user=self.user, interval='followup',
            ).exists(),
        )

    def test_second_pass_is_dedup_no_op(self):
        send_post_event_followup_one(self.event.pk, self.user.pk)
        deliver_pending_mail()
        result = send_post_event_followup_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'already_sent')
        # Only ONE delivery and ONE EmailLog row even after two passes.
        self.assertEqual(EmailDelivery.objects.count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(
                email_type='post_event_followup', user=self.user,
            ).count(),
            1,
        )

    def test_unsubscribed_user_still_receives(self):
        """Issue #680: post_event_followup is transactional. The
        unsubscribed flag must NOT block the send."""
        result = send_post_event_followup_one(
            self.event.pk, self.unsub_user.pk,
        )
        self.assertEqual(result['status'], 'sent')
        deliver_pending_mail()
        self.assertTrue(
            EmailLog.objects.filter(
                email_type='post_event_followup', user=self.unsub_user,
            ).exists(),
        )

    def test_render_includes_summary_and_recording_and_notes(self):
        send_post_event_followup_one(self.event.pk, self.user.pk)

        sent_html = _drain_and_get_sent_html()
        self.assertIn('Thanks for joining the recap test session.', sent_html)
        self.assertIn('https://youtube.com/watch?v=recap-one', sent_html)
        self.assertIn(
            "Workshop notes are still being put together",
            sent_html,
        )
        # CTA absent by default (issue #679 not shipped).
        self.assertNotIn('Leave feedback', sent_html)

    def test_published_recap_replaces_the_notes_placeholder(self):
        # Issue #1458: the placeholder used to be unconditional, so every
        # follow-up promised notes that never arrived.
        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='## What we covered\n\nBatching.',
            recap_notes_html='<h2>What we covered</h2><p>Batching.</p>',
        )
        send_post_event_followup_one(self.event.pk, self.user.pk)

        sent_html = _drain_and_get_sent_html()
        self.event.refresh_from_db()
        self.assertIn(self.event.get_recap_url(), sent_html)
        self.assertIn('Read the recap', sent_html)
        self.assertNotIn(
            'Workshop notes are still being put together', sent_html,
        )

    def test_blank_summary_uses_generic_fallback(self):
        Event.objects.filter(pk=self.event.pk).update(post_event_summary='')
        send_post_event_followup_one(self.event.pk, self.user.pk)

        sent_html = _drain_and_get_sent_html()
        self.assertIn('Thanks for joining us at Recap one-shot', sent_html)

    def test_local_refusal_logs_and_does_not_raise(self):
        with patch(
            'email_app.package_mail.package_send',
            side_effect=MailError('refused'),
        ):
            with self.assertLogs(
                'events.tasks.send_post_event_followup',
                level='ERROR',
            ) as cm:
                result = send_post_event_followup_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'errored')
        self.assertTrue(
            any('Failed to record post_event_followup' in m for m in cm.output),
            f'expected failure log, got {cm.output}',
        )
        # Dedup row stays put so the cron won't loop on a poison address.
        self.assertTrue(
            EventReminderLog.objects.filter(
                event=self.event, user=self.user, interval='followup',
            ).exists(),
        )
        # The refusal must not burn the stable key: the next cron tick or
        # manual press can still deliver to this user.
        self.assertEqual(EmailDelivery.objects.count(), 0)

    @patch('email_app.package_mail.send_package_mail')
    def test_event_without_recording_url_skips(self, mock_send):
        # The cron's gate is the primary defence. Belt-and-braces: the
        # per-user task also short-circuits when both URL fields are
        # empty, so a hand-rolled enqueue can't email an empty link.
        Event.objects.filter(pk=self.event.pk).update(
            recording_url='', recording_s3_url='',
        )
        result = send_post_event_followup_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'no_recording_url')
        mock_send.assert_not_called()

    @patch('email_app.package_mail.send_package_mail')
    def test_missing_event_returns_skipped(self, mock_send):
        result = send_post_event_followup_one(999_999, self.user.pk)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        mock_send.assert_not_called()

    @patch('email_app.package_mail.send_package_mail')
    def test_missing_user_returns_skipped(self, mock_send):
        result = send_post_event_followup_one(self.event.pk, 999_999)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_user')
        mock_send.assert_not_called()


class FeedbackUrlWiringTest(TestCase):
    """Issue #680: feedback CTA is conditional on the #679 surface."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Recap with feedback',
            slug='recap-feedback',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=recap-feedback',
        )
        cls.user = User.objects.create_user(email='feedback@test.com')

    def setUp(self):
        EventRegistration.objects.create(event=self.event, user=self.user)

    def test_feedback_url_absent_when_dependency_unavailable(self):
        """When EventFeedback import fails OR the URL doesn't resolve,
        the CTA block is empty."""
        send_post_event_followup_one(self.event.pk, self.user.pk)
        sent_html = _drain_and_get_sent_html()
        self.assertNotIn('Leave feedback', sent_html)

    def test_feedback_url_present_when_dependency_satisfied(self):
        """Issue #680 / #679 wiring: when the import succeeds AND the
        URL resolves, the CTA renders. We simulate the #679 surface by
        monkey-patching the worker-side feedback probe (issue #1613 moved
        the builder to events.services.post_event_mail)."""
        with patch(
            'events.services.post_event_mail.feedback_url_for',
            return_value='https://example.test/events/recap-feedback/feedback',
        ):
            send_post_event_followup_one(self.event.pk, self.user.pk)
            # The worker mints the context at drain time (issue #1613),
            # so the probe stays patched through the drain.
            sent_html = _drain_and_get_sent_html()
        self.assertIn('Leave feedback', sent_html)
        self.assertIn(
            'https://example.test/events/recap-feedback/feedback',
            sent_html,
        )


class TransactionalClassificationTest(TestCase):
    """``classify_email_type`` reports the new template as transactional."""

    def test_post_event_followup_is_transactional(self):
        self.assertEqual(
            classify_email_type('post_event_followup'),
            EMAIL_KIND_TRANSACTIONAL,
        )
