"""Tests for staff recurring-schedule diagnostics."""

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase

from accounts.models import Token
from jobs.schedule_reconciliation import SCHEDULE_RECONCILIATION_CACHE_KEY

User = get_user_model()
URL = "/api/diagnostics/schedules"


class ScheduleDiagnosticsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff")
        cls.member_token = Token(
            key="schedule-member-token",
            user=cls.member,
            name="member",
        )
        Token.objects.bulk_create([cls.member_token])

    def setUp(self):
        caches["django_q"].clear()

    def _get(self):
        return self.client.get(
            URL,
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )

    def test_staff_token_returns_cached_payload_and_generated_at(self):
        payload = {
            "status": "degraded",
            "recorded_at": "2026-09-03T10:00:00+00:00",
            "last_success_at": "2026-09-03T09:00:00+00:00",
            "error": "RuntimeError: failed",
            "expected_names": ["health-check"],
            "missing_names": ["health-check"],
        }
        caches["django_q"].set(
            SCHEDULE_RECONCILIATION_CACHE_KEY,
            payload,
            timeout=None,
        )

        response = self._get()

        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["expected_names"], ["health-check"])
        self.assertIn("generated_at", body)

    def test_absent_cache_key_returns_unknown(self):
        response = self._get()

        self.assertEqual(
            response.json()["status"],
            "unknown",
        )
        self.assertIsNone(response.json()["recorded_at"])
        self.assertIsNone(response.json()["last_success_at"])

    def test_missing_token_returns_401(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_non_staff_token_returns_401(self):
        response = self.client.get(
            URL,
            HTTP_AUTHORIZATION=f"Token {self.member_token.key}",
        )
        self.assertEqual(response.status_code, 401)

    def test_post_returns_405_for_staff(self):
        response = self.client.post(
            URL,
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 405)
