"""Tests for the per-user dashboard card dismiss endpoint (issue #1129).

``POST /account/api/dismiss-card`` persists a member's own dashboard card
dismissals server-side (list on ``User.dashboard_dismissals``) so a
dismissed card stays gone across devices and sessions. The endpoint is
``@login_required`` + ``@require_POST``, allow-lists card keys, and is
idempotent.
"""

import datetime
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

    def _make_plan(self, member=None):
        from plans.models import Plan, Sprint

        member = member or self.user
        sprint = Sprint.objects.create(
            name=f"Sprint {Sprint.objects.count() + 1}",
            slug=f"dismiss-sprint-{Sprint.objects.count() + 1}",
            start_date=datetime.date(2026, 5, 1),
        )
        return Plan.objects.create(member=member, sprint=sprint)

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

    def test_plan_carry_over_prompt_for_owned_plan_persists(self):
        plan = self._make_plan()
        card = f"plan_carry_over_prompt:{plan.pk}"

        response = self._post({"card": card})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "card": card})
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, [card])

    def test_plan_carry_over_prompt_for_non_owned_plan_rejected(self):
        other = User.objects.create_user(email="other@test.com", password="pw")
        plan = self._make_plan(other)

        response = self._post({"card": f"plan_carry_over_prompt:{plan.pk}"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, [])

    def test_plan_carry_over_prompt_malformed_keys_rejected(self):
        for card in [
            "plan_carry_over_prompt:",
            "plan_carry_over_prompt:abc",
            "plan_carry_over_prompt:-1",
            "plan_carry_over_prompt:01",
            "plan_carry_over_prompt:1:extra",
            ["plan_carry_over_prompt:1"],
        ]:
            with self.subTest(card=card):
                response = self._post({"card": card})
                self.assertEqual(response.status_code, 400)

        self.user.refresh_from_db()
        self.assertEqual(self.user.dashboard_dismissals, [])

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
