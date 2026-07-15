"""Tests for the widget state + claim endpoints (issue #1070).

Covers per-user state resolution, the server-side min_level gate, the
flag-off paused short-circuit, dedup persistence, and CSRF enforcement.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, tag

from content.access import LEVEL_MAIN, LEVEL_REGISTERED
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from triggers.models import EventEmission, EventWidget

User = get_user_model()


def _set_flag(value):
    IntegrationSetting.objects.update_or_create(
        key="TRIGGERS_ENABLED",
        defaults={"value": "true" if value else "false", "group": "triggers"},
    )
    clear_config_cache()


@tag("core")
class WidgetStateViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.widget = EventWidget.objects.create(
            slug="v0-claim",
            event_name="v0_workshop",
            min_level=LEVEL_REGISTERED,
            claim_label="Claim your credit",
            claim_body="Get your v0 credit.",
            signin_cta="Sign in to claim",
            claimed_label="Claimed",
        )
        cls.member = User.objects.create_user(
            email="member@example.com", password="x", email_verified=True,
        )

    def setUp(self):
        cache.clear()
        _set_flag(True)

    def tearDown(self):
        clear_config_cache()

    def test_unknown_slug_returns_unavailable(self):
        resp = self.client.get("/widgets/nope/state")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "unavailable")

    def test_inactive_widget_returns_unavailable(self):
        EventWidget.objects.filter(pk=self.widget.pk).update(is_active=False)
        resp = self.client.get("/widgets/v0-claim/state")
        self.assertEqual(resp.json()["state"], "unavailable")

    def test_anonymous_gets_signin_required(self):
        resp = self.client.get("/widgets/v0-claim/state")
        data = resp.json()
        self.assertEqual(data["state"], "signin_required")
        self.assertEqual(data["signin_cta"], "Sign in to claim")

    def test_eligible_member_gets_claimable(self):
        self.client.force_login(self.member)
        resp = self.client.get("/widgets/v0-claim/state")
        data = resp.json()
        self.assertEqual(data["state"], "claimable")
        self.assertEqual(data["claim_label"], "Claim your credit")

    def test_flag_off_gives_paused(self):
        _set_flag(False)
        self.client.force_login(self.member)
        resp = self.client.get("/widgets/v0-claim/state")
        self.assertEqual(resp.json()["state"], "paused")

    def test_already_claimed_member_gets_claimed(self):
        EventEmission.objects.create(
            user=self.member,
            event_name="v0_workshop",
            properties={},
            envelope_id="evt_seed",
        )
        self.client.force_login(self.member)
        resp = self.client.get("/widgets/v0-claim/state")
        self.assertEqual(resp.json()["state"], "claimed")

    def test_under_level_member_gets_under_level(self):
        widget = EventWidget.objects.create(
            slug="premium-claim",
            event_name="premium_thing",
            min_level=LEVEL_MAIN,
        )
        self.client.force_login(self.member)
        resp = self.client.get(f"/widgets/{widget.slug}/state")
        self.assertEqual(resp.json()["state"], "under_level")


@tag("core")
class WidgetClaimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.widget = EventWidget.objects.create(
            slug="v0-claim",
            event_name="v0_workshop",
            min_level=LEVEL_REGISTERED,
            claimed_label="Claimed",
        )
        cls.member = User.objects.create_user(
            email="member@example.com", password="x", email_verified=True,
        )

    def setUp(self):
        cache.clear()
        _set_flag(True)

    def tearDown(self):
        clear_config_cache()

    def test_anonymous_claim_returns_401(self):
        resp = self.client.post("/widgets/v0-claim/claim")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(EventEmission.objects.count(), 0)

    def test_under_level_claim_returns_403_and_records_nothing(self):
        widget = EventWidget.objects.create(
            slug="premium-claim",
            event_name="premium_thing",
            min_level=LEVEL_MAIN,
        )
        self.client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            resp = self.client.post(f"/widgets/{widget.slug}/claim")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            EventEmission.objects.filter(
                user=self.member, event_name="premium_thing",
            ).exists()
        )

    def test_flag_off_claim_is_paused_no_emission(self):
        _set_flag(False)
        self.client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            resp = self.client.post("/widgets/v0-claim/claim")
        self.assertEqual(resp.json()["state"], "paused")
        self.assertEqual(EventEmission.objects.count(), 0)

    def test_eligible_claim_records_emission(self):
        self.client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            resp = self.client.post("/widgets/v0-claim/claim")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "claimed")
        self.assertEqual(
            EventEmission.objects.filter(
                user=self.member, event_name="v0_workshop",
            ).count(),
            1,
        )

    def test_duplicate_claim_is_noop_returns_claimed(self):
        self.client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            self.client.post("/widgets/v0-claim/claim")
            resp = self.client.post("/widgets/v0-claim/claim")
        self.assertEqual(resp.json()["state"], "claimed")
        self.assertEqual(EventEmission.objects.count(), 1)

    def test_claim_enforces_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            resp = csrf_client.post("/widgets/v0-claim/claim")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(EventEmission.objects.count(), 0)

    def test_repeated_claim_attempts_are_rate_limited_with_retry_hint(self):
        self.client.force_login(self.member)
        with patch("triggers.dispatch.async_task"):
            responses = [self.client.post("/widgets/v0-claim/claim") for _ in range(6)]
        self.assertEqual([response.status_code for response in responses[:5]], [200] * 5)
        self.assertEqual(responses[5].status_code, 429)
        self.assertEqual(responses[5].json()["state"], "rate_limited")
        self.assertEqual(responses[5]["Retry-After"], "60")
        self.assertEqual(EventEmission.objects.count(), 1)
