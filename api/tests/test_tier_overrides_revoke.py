"""Tests for ``POST /api/tier-overrides/revoke``.

The endpoint shares ``payments.services.tier_override_revoke`` with the Studio
revoke button, so these tests also pin the shared audit trail.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog
from payments.models import Tier, TierOverride

User = get_user_model()

URL = "/api/tier-overrides/revoke"


class TierOverridesRevokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main = Tier.objects.get(slug="main")
        cls.admin = User.objects.create_user(
            email="admin@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="refund-bot")

    def _post(self, payload, *, token=None):
        headers = {}
        if token is not False:
            key = token.key if token is not None else self.token.key
            headers["HTTP_AUTHORIZATION"] = f"Token {key}"
        return self.client.post(
            URL, data=json.dumps(payload), content_type="application/json",
            **headers,
        )

    def _override(self, user, *, source="", days=30):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.membership.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=days),
            is_active=True,
            source=source,
        )

    def test_revokes_every_active_override_and_audits_each(self):
        member = User.objects.create_user(email="refund@example.com")
        manual = self._override(member)
        maven = self._override(member, source="maven:abc")

        response = self._post({"emails": ["Refund@Example.com"]})

        body = response.json()
        self.assertEqual(body["revoked"], 1)
        self.assertEqual(body["results"][0]["status"], "revoked")
        self.assertEqual(
            [o["id"] for o in body["results"][0]["overrides"]],
            [manual.pk, maven.pk],
        )
        self.assertFalse(
            TierOverride.objects.filter(user=member, is_active=True).exists()
        )
        audits = CommunityAuditLog.objects.filter(
            user=member, action="tier_override_revoked",
        ).order_by("pk")
        self.assertEqual(audits.count(), 2)
        self.assertIn("actor_token=refund-bot", audits[0].details)
        self.assertIn(f"override_id={manual.pk}", audits[0].details)
        self.assertIn("source=maven:abc", audits[1].details)

    def test_second_call_is_idempotent_no_active_override(self):
        member = User.objects.create_user(email="twice@example.com")
        self._override(member)
        self._post({"emails": ["twice@example.com"]})

        response = self._post({"emails": ["twice@example.com"]})

        body = response.json()
        self.assertEqual(body["revoked"], 0)
        self.assertEqual(body["no_active_override"], 1)
        self.assertEqual(body["results"][0]["status"], "no_active_override")
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=member, action="tier_override_revoked",
            ).count(),
            1,
        )

    def test_unknown_and_malformed_emails_are_reported(self):
        response = self._post({"emails": ["ghost@example.com", "nope"]})

        body = response.json()
        self.assertEqual(
            [r["status"] for r in body["results"]],
            ["user_not_found", "malformed"],
        )
        self.assertFalse(User.objects.filter(email="ghost@example.com").exists())

    def test_alias_email_resolves_to_canonical_user(self):
        member = User.objects.create_user(email="canon@example.com")
        EmailAlias.objects.create(user=member, email="old@example.com")
        override = self._override(member)

        response = self._post({"emails": ["old@example.com"]})

        self.assertEqual(response.json()["results"][0]["status"], "revoked")
        override.refresh_from_db()
        self.assertFalse(override.is_active)

    def test_dry_run_reports_without_writing(self):
        member = User.objects.create_user(email="dry@example.com")
        override = self._override(member)

        response = self._post({"emails": ["dry@example.com"], "dry_run": True})

        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["results"][0]["status"], "would_revoke")
        override.refresh_from_db()
        self.assertTrue(override.is_active)
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="tier_override_revoked").exists()
        )

    def test_missing_auth_header_rejected(self):
        member = User.objects.create_user(email="noauth@example.com")
        override = self._override(member)

        response = self._post({"emails": ["noauth@example.com"]}, token=False)

        self.assertEqual(response.status_code, 401)
        override.refresh_from_db()
        self.assertTrue(override.is_active)

    def test_non_staff_token_rejected(self):
        member = User.objects.create_user(email="victim@example.com")
        override = self._override(member)
        plain = User.objects.create_user(email="plain@example.com")
        # Bypass the manager's staff-only validator (legacy-demoted user).
        non_staff = Token(key="non-staff-revoke", user=plain, name="legacy")
        Token.objects.bulk_create([non_staff])

        response = self._post({"emails": ["victim@example.com"]}, token=non_staff)

        self.assertEqual(response.status_code, 401)
        override.refresh_from_db()
        self.assertTrue(override.is_active)

    def test_emails_must_be_a_list(self):
        response = self._post({"emails": "a@example.com"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_emails")
