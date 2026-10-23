"""Transactional concurrency coverage for SES bounce and complaint feedback."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, tag

from api.tests.test_ses_events import (
    URL,
    VALIDATOR_PATH,
    _bounce_payload,
    _complaint_payload,
)
from email_app.models import EmailLog, SesEvent
from email_app.services import ses_feedback

User = get_user_model()


def _correlated(payload, ses_message_id):
    inner = json.loads(payload["Message"])
    inner["mail"]["messageId"] = ses_message_id
    payload["Message"] = json.dumps(inner)
    return payload


def _post(payload):
    return Client().post(
        URL,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _soft_feedback(email, message_id, ses_message_id):
    user = User.objects.create_user(email=email)
    email_log = EmailLog.objects.create(
        user=user,
        email_type="campaign",
        ses_message_id=ses_message_id,
    )
    payload = _correlated(
        _bounce_payload(message_id, user.email, bounce_type="Transient"),
        email_log.ses_message_id,
    )
    return user, email_log, payload


@tag("postgresql", "slow_platform")
class SesFeedbackConcurrencyTest(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("SES feedback concurrency requires PostgreSQL row locks")

    def _post_concurrently(self, payloads):
        barrier = threading.Barrier(len(payloads))
        original_claim = ses_feedback._claim_event

        def synchronized_claim(event):
            barrier.wait(timeout=5)
            return original_claim(event)

        def post(payload):
            close_old_connections()
            try:
                return _post(payload)
            finally:
                connection.close()

        with (
            mock.patch(VALIDATOR_PATH, return_value=True),
            mock.patch.object(
                ses_feedback,
                "_claim_event",
                side_effect=synchronized_claim,
            ),
            ThreadPoolExecutor(max_workers=len(payloads)) as pool,
        ):
            futures = [pool.submit(post, payload) for payload in payloads]
            return [future.result(timeout=15) for future in futures]

    def test_duplicate_soft_bounce_claim_applies_side_effects_once(self):
        user, email_log, payload = _soft_feedback(
            "duplicate-soft@example.com",
            "sns-duplicate-soft",
            "ses-duplicate-soft",
        )
        original_stamp = ses_feedback._stamp_email_log_bounce
        with mock.patch.object(
            ses_feedback,
            "_stamp_email_log_bounce",
            wraps=original_stamp,
        ) as stamp:
            responses = self._post_concurrently([payload, payload])

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            sorted(response.json()["status"] for response in responses),
            ["duplicate", "ok"],
        )
        self.assertEqual(SesEvent.objects.filter(message_id="sns-duplicate-soft").count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 1)
        email_log.refresh_from_db()
        self.assertIsNotNone(email_log.bounced_at)
        self.assertEqual(stamp.call_count, 1)

    def test_distinct_soft_bounces_for_one_user_both_increment(self):
        user = User.objects.create_user(email="distinct-soft@example.com")
        payloads = [
            _bounce_payload(message_id, user.email, bounce_type="Transient")
            for message_id in ("sns-distinct-soft-1", "sns-distinct-soft-2")
        ]

        responses = self._post_concurrently(payloads)

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual([response.json()["status"] for response in responses], ["ok", "ok"])
        self.assertEqual(
            SesEvent.objects.filter(message_id__startswith="sns-distinct-soft-").count(),
            2,
        )
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 2)

    def test_duplicate_permanent_bounce_and_complaint_mutate_once(self):
        cases = (
            ("permanent", _bounce_payload, "mark_permanent_bounce"),
            ("complaint", _complaint_payload, "_mark_complaint"),
        )
        for kind, payload_factory, mutation_name in cases:
            with self.subTest(kind=kind):
                user = User.objects.create_user(email=f"duplicate-{kind}@example.com")
                message_id = f"sns-duplicate-{kind}"
                payload = payload_factory(message_id, user.email)
                original_mutation = getattr(ses_feedback, mutation_name)
                with mock.patch.object(
                    ses_feedback,
                    mutation_name,
                    wraps=original_mutation,
                ) as mutation:
                    responses = self._post_concurrently([payload, payload])

                self.assertEqual(
                    sorted(response.json()["status"] for response in responses),
                    ["duplicate", "ok"],
                )
                self.assertEqual(SesEvent.objects.filter(message_id=message_id).count(), 1)
                self.assertEqual(mutation.call_count, 1)
                user.refresh_from_db()
                self.assertTrue(user.unsubscribed)


@tag("slow_platform")
class SesFeedbackRollbackTest(TransactionTestCase):
    def test_exception_rolls_back_claim_user_and_email_log_then_retry_succeeds(self):
        user, email_log, payload = _soft_feedback(
            "rollback-soft@example.com",
            "sns-rollback-soft",
            "ses-rollback-soft",
        )
        original_finalize = ses_feedback._finalize_claim

        def fail_before_commit(*args, **kwargs):
            original_finalize(*args, **kwargs)
            raise RuntimeError("forced pre-commit failure")

        with (
            mock.patch(VALIDATOR_PATH, return_value=True),
            mock.patch.object(
                ses_feedback,
                "_finalize_claim",
                side_effect=fail_before_commit,
            ),
            self.assertRaisesMessage(RuntimeError, "forced pre-commit failure"),
        ):
            _post(payload)

        self._assert_rolled_back(user, email_log)
        self._assert_retry_succeeds(user, email_log, payload)

    def _assert_rolled_back(self, user, email_log):
        self.assertFalse(SesEvent.objects.filter(message_id="sns-rollback-soft").exists())
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 0)
        email_log.refresh_from_db()
        self.assertIsNone(email_log.bounced_at)
        self.assertEqual(email_log.bounce_type, "")

    def _assert_retry_succeeds(self, user, email_log, payload):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = _post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertTrue(SesEvent.objects.filter(message_id="sns-rollback-soft").exists())
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 1)
        email_log.refresh_from_db()
        self.assertIsNotNone(email_log.bounced_at)
