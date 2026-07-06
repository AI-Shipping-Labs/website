"""Tests for the per-user dashboard card dismiss endpoint (issue #1129).

``POST /account/api/dismiss-card`` persists a member's own dashboard card
dismissals server-side (list on ``User.dashboard_dismissals``) so a
dismissed card stays gone across devices and sessions. The endpoint is
``@login_required`` + ``@require_POST``, allow-lists card keys, and is
idempotent.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

User = get_user_model()

DISMISS_URL = "/account/api/dismiss-card"


@tag("core")
class DismissDashboardCardEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="dm@test.com", password="pw")

    def setUp(self):
        self.client.login(email="dm@test.com", password="pw")

    def _post(self, body):
        return self.client.post(
            DISMISS_URL, data=json.dumps(body),
            content_type="application/json",
        )

    def test_url_name_resolves(self):
        self.assertEqual(reverse("dismiss_dashboard_card"), DISMISS_URL)

    def test_dismiss_onboarding_prompt_persists_and_echoes(self):
        response = self._post({"card": "onboarding_prompt"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "card": "onboarding_prompt"},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, ["onboarding_prompt"])

    def test_dismiss_slack_join_persists(self):
        response = self._post({"card": "slack_join"})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIn("slack_join", self.user.dashboard_dismissals)

    def test_unknown_card_returns_400_and_does_not_mutate(self):
        response = self._post({"card": "not-a-real-card"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, [])

    def test_missing_card_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, [])

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            DISMISS_URL, data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_dismiss_is_idempotent(self):
        first = self._post({"card": "onboarding_prompt"})
        second = self._post({"card": "onboarding_prompt"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.user.refresh_from_db()
        # No duplicate key.
        self.assertEqual(self.user.dashboard_dismissals, ["onboarding_prompt"])

    def test_two_distinct_cards_both_stored(self):
        self._post({"card": "onboarding_prompt"})
        self._post({"card": "slack_join"})
        self.user.refresh_from_db()
        self.assertEqual(
            sorted(self.user.dashboard_dismissals),
            ["onboarding_prompt", "slack_join"],
        )

    def test_get_not_allowed(self):
        response = self.client.get(DISMISS_URL)
        self.assertEqual(response.status_code, 405)


@tag("core")
class DismissDashboardCardAnonymousTest(TestCase):
    def test_anonymous_post_redirected_to_login_and_not_processed(self):
        response = self.client.post(
            DISMISS_URL, data=json.dumps({"card": "onboarding_prompt"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
