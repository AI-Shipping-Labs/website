"""API surface for the verify-or-delete campaign."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import DeletedUserArchive, Token
from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


class CampaignVerifyApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        staff = User.objects.create_user(
            email="staff-verify@test.com", password="pw", is_staff=True,
            email_verified=True,
        )
        cls.token = Token.objects.create(user=staff, name="verify")
        cls.campaign = EmailCampaign.objects.create(
            subject="Confirm",
            body="Body",
            audience_verification="unverified_only",
            verify_deadline_days=7,
            status="sent",
        )
        cls.target = create_user_with_membership(
            email="target@example.com", tier=cls.free_tier, email_verified=False,
        )
        log = EmailLog.objects.create(
            campaign=cls.campaign,
            user=cls.target,
            recipient_email=cls.target.email,
            email_type="campaign_verify_deadline",
        )
        CampaignDelivery.objects.create(
            campaign=cls.campaign,
            user=cls.target,
            recipient_user_pk=cls.target.pk,
            recipient_email=cls.target.email,
            state=CampaignDelivery.State.SENT,
            email_log=log,
            completed_at=timezone.now() - timedelta(days=8),
        )

    def call(self, method, path, body=None):
        return getattr(self.client, method)(
            path,
            data=json.dumps(body) if body is not None else None,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def purge_path(self):
        return f"/api/campaigns/{self.campaign.pk}/unverified-deletion"

    def test_default_call_is_a_dry_run(self):
        response = self.call("post", self.purge_path(), {})

        self.assertTrue(response.json()["dry_run"])
        self.assertEqual(response.json()["reasons"], {"deletable": 1})
        self.assertTrue(User.objects.filter(pk=self.target.pk).exists())

    def test_real_run_requires_explicit_confirmation(self):
        response = self.call("post", self.purge_path(), {"dry_run": False})

        self.assertEqual(response.status_code, 422)
        self.assertTrue(User.objects.filter(pk=self.target.pk).exists())

    def test_confirmed_run_deletes_and_backup_is_exportable(self):
        response = self.call(
            "post", self.purge_path(), {"dry_run": False, "confirm": "delete"},
        )

        self.assertEqual(response.json()["deleted"], 1)
        self.assertFalse(User.objects.filter(pk=self.target.pk).exists())
        export = self.call(
            "get", f"/api/campaigns/{self.campaign.pk}/deleted-users",
        ).json()
        self.assertEqual(export["total"], 1)
        self.assertEqual(export["results"][0]["email"], "target@example.com")
        self.assertEqual(DeletedUserArchive.objects.count(), 1)

    def test_unknown_campaign_is_404_and_plain_campaign_is_409(self):
        self.assertEqual(
            self.call("post", "/api/campaigns/99999/unverified-deletion", {}).status_code,
            404,
        )
        plain = EmailCampaign.objects.create(subject="p", body="b")
        self.assertEqual(
            self.call("post", f"/api/campaigns/{plain.pk}/unverified-deletion", {}).status_code,
            409,
        )

    def test_audit_endpoint_returns_age_buckets(self):
        response = self.call("get", "/api/campaigns/unverified-audit")

        self.assertEqual(response.json()["unverified"]["total"], 1)
        self.assertIn("under_30_days", response.json()["by_signup_age"])

    def test_endpoints_require_a_token(self):
        for path in (
            "/api/campaigns/unverified-audit",
            f"/api/campaigns/{self.campaign.pk}/deleted-users",
        ):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_deadline_can_be_set_only_on_unverified_campaigns(self):
        ok = self.call("post", "/api/campaigns", {
            "subject": "s", "body": "b",
            "audience_verification": "unverified_only",
            "verify_deadline_days": 7,
        })
        bad = self.call("post", "/api/campaigns", {
            "subject": "s", "body": "b", "verify_deadline_days": 7,
        })

        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["verify_deadline_days"], 7)
        self.assertEqual(bad.status_code, 422)
