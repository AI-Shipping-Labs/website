"""Dead-intent API: bounded classification, privacy, and terminal retention."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from community_base.jobs.models import JobIntent
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from jobs.dead_intent_summary import MAX_REPORTED_HANDLERS, dead_intent_summary

URL = "/api/worker/intents/dead-summary"
HANDLER = "coursework.form_pooled_batches"
PRIVATE_TEXT = "private-person@example.invalid:secret-value"


def _intent(handler=HANDLER, error="handler_error", status=JobIntent.Status.DEAD):
    return JobIntent(
        handler=handler, last_error=error, status=status,
        key_hash=uuid.uuid4().hex, payload_hash="private-payload-hash",
        payload={"private": PRIVATE_TEXT}, correlation_id=PRIVATE_TEXT,
        available_at=timezone.now(), attempts=5, max_attempts=5,
    )


class DeadIntentApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        staff = get_user_model().objects.create_user(
            email="dead-intents-staff@example.invalid", is_staff=True,
        )
        cls.token = Token.objects.create(user=staff, name="dead summary")
        member = get_user_model().objects.create_user(email="member@example.invalid")
        Token.objects.bulk_create([Token(key="non-staff-token", user=member)])
        rows = [_intent() for _ in range(50)]
        rows.extend(_intent(handler=PRIVATE_TEXT) for _ in range(10))
        rows.extend(_intent(error=f"{PRIVATE_TEXT}-{index}") for index in range(3))
        JobIntent.objects.bulk_create(rows)
        cls.oldest = timezone.now() - timedelta(days=20)
        cls.newest = timezone.now() - timedelta(days=1)
        JobIntent.objects.filter(pk=rows[0].pk).update(created_at=cls.oldest)
        JobIntent.objects.exclude(pk=rows[0].pk).update(created_at=cls.newest)
        for status in (JobIntent.Status.PENDING, JobIntent.Status.FAILED,
                       JobIntent.Status.SUCCEEDED):
            JobIntent.objects.bulk_create([_intent(status=status)])

    def _get(self):
        return self.client.get(URL, HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_requires_staff_token_even_for_session_staff(self):
        self.client.force_login(self.token.user)
        for authorization in ("", "Bearer invalid", "Token invalid",
                              "Token non-staff-token"):
            with self.subTest(authorization=authorization):
                response = self.client.get(URL, HTTP_AUTHORIZATION=authorization)
                self.assertEqual(response.status_code, 401)

    def test_reconciles_63_dead_and_emits_only_safe_categories(self):
        response = self._get()
        body = response.json()
        self.assertEqual(body["total_dead"], 63)
        counts = {}
        for group in body["groups"]:
            counts[(group["handler_category"], group["error_category"])] = group["count"]
        self.assertEqual(counts, {
            (HANDLER, "handler_error"): 50,
            (HANDLER, "unknown_error"): 3,
            ("unknown_handler", "handler_error"): 10,
        })
        self.assertEqual(sum(counts.values()), body["total_dead"])
        self.assertEqual(body["disposition"], "retained_dead")
        self.assertEqual(body["retry_policy"], "no_automatic_retry")

    def test_summary_dates_and_response_shape_exclude_private_data(self):
        body = self._get().json()
        self.assertEqual(set(body), {"total_dead", "oldest_created_at",
                         "newest_created_at", "disposition", "retry_policy", "groups"})
        oldest = datetime.fromisoformat(body["oldest_created_at"])
        newest = datetime.fromisoformat(body["newest_created_at"])
        self.assertEqual(oldest, self.oldest.replace(microsecond=self.oldest.microsecond // 1000 * 1000))
        self.assertEqual(newest, self.newest.replace(microsecond=self.newest.microsecond // 1000 * 1000))
        for group in body["groups"]:
            self.assertEqual(set(group), {"handler_category", "error_category", "count",
                             "oldest_created_at", "newest_created_at"})
        response_text = str(body)
        for private in (PRIVATE_TEXT, "private-payload-hash", self.token.key):
            self.assertNotIn(private, response_text)

    def test_all_requests_retain_every_intent_without_mutation(self):
        before = list(JobIntent.objects.order_by("pk").values())
        self.assertEqual(self._get().json()["total_dead"], 63)
        for method in ("post", "put", "patch", "delete", "head", "options"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    URL, HTTP_AUTHORIZATION=f"Token {self.token.key}",
                )
                self.assertEqual(response.status_code, 405)
        self.assertEqual(list(JobIntent.objects.order_by("pk").values()), before)


class DeadIntentAggregationTest(TestCase):
    def test_empty_summary(self):
        self.assertEqual(dead_intent_summary(), {
            "total_dead": 0, "oldest_created_at": None, "newest_created_at": None,
            "disposition": "retained_dead", "retry_policy": "no_automatic_retry",
            "groups": [],
        })

    @patch("jobs.dead_intent_summary.registered_handler_names")
    def test_handler_overflow_and_unknown_errors_are_aggregated_before_bounding(self, names):
        names.return_value = tuple(f"handler.{index:03}" for index in range(120))
        rows = [_intent(handler=name, error=f"untrusted-{index}")
                for index, name in enumerate(names.return_value)]
        rows.append(_intent(handler=PRIVATE_TEXT, error=PRIVATE_TEXT))
        JobIntent.objects.bulk_create(rows)
        with self.assertNumQueries(1):
            body = dead_intent_summary()
        self.assertEqual(body["total_dead"], 121)
        self.assertEqual(len(body["groups"]), MAX_REPORTED_HANDLERS + 2)
        counts = {}
        for group in body["groups"]:
            self.assertEqual(group["error_category"], "unknown_error")
            counts[group["handler_category"]] = group["count"]
        self.assertEqual(counts["other_registered_handler"], 20)
        self.assertEqual(counts["unknown_handler"], 1)
        self.assertEqual(sum(counts.values()), 121)
        self.assertNotIn(PRIVATE_TEXT, str(body))
