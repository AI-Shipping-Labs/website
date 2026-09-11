"""Read-only staff API for re-permission wave monitoring."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


class CampaignWavesApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email="wave-api@example.com",
            is_staff=True,
            email_verified=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="waves")

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_returns_sanitized_wave_summary(self):
        campaign = EmailCampaign.objects.create(
            subject="Waves",
            body="Body",
            audience_verification="unverified_only",
            status="sending",
        )
        wave = CampaignWave.objects.create(campaign=campaign, number=1)
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=wave,
            recipient_user_pk=999,
            recipient_email="private@example.com",
        )

        response = self.client.get(
            f"/api/campaigns/{campaign.pk}/waves",
            **self.auth(),
        )

        payload = response.json()
        self.assertEqual(payload["campaign_id"], campaign.pk)
        self.assertEqual(payload["waves"][0]["recipient_count"], 1)
        self.assertEqual(payload["blocking_reason"], "no_released_wave")
        self.assertEqual(payload["thresholds"]["bounce_stop_percent"], 2.0)
        self.assertNotContains(response, "private@example.com")
        self.assertNotIn("recipient_email", payload["waves"][0])

    def test_rejects_non_monitored_campaign(self):
        campaign = EmailCampaign.objects.create(subject="Normal", body="Body")

        response = self.client.get(
            f"/api/campaigns/{campaign.pk}/waves",
            **self.auth(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "not_monitored_campaign")

    def test_requires_staff_token_and_has_no_mutation_method(self):
        campaign = EmailCampaign.objects.create(
            subject="Waves",
            body="Body",
            audience_verification="unverified_only",
        )
        url = f"/api/campaigns/{campaign.pk}/waves"

        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post(url, **self.auth()).status_code, 405)

    def test_campaign_api_accepts_and_documents_unverified_only(self):
        response = self.client.post(
            "/api/campaigns",
            data=('{"subject":"Confirm","body":"Intro","audience_verification":"unverified_only"}'),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["audience_verification"], "unverified_only")

        spec = self.client.get("/api/openapi.json", **self.auth()).json()
        enum = spec["paths"]["/api/campaigns"]["post"]["requestBody"]["content"]["application/json"]["schema"][
            "properties"
        ]["audience_verification"]["enum"]
        self.assertEqual(enum, ["verified_only", "unverified_only", "everyone"])
        self.assertIn(
            "/api/campaigns/{campaign_id}/waves",
            spec["paths"],
        )

    def test_count_and_preview_apply_the_strict_unverified_audience(self):
        eligible = create_user_with_membership(
            email="eligible-wave-api@example.com",
            tier=self.free_tier,
            email_verified=False,
            unsubscribed=False,
        )
        create_user_with_membership(
            email="verified-wave-api@example.com",
            tier=self.free_tier,
            email_verified=True,
        )
        create_user_with_membership(
            email="unsubscribed-wave-api@example.com",
            tier=self.free_tier,
            email_verified=False,
            unsubscribed=True,
        )
        campaign = EmailCampaign.objects.create(
            subject="Strict preview",
            body="Body",
            audience_verification="unverified_only",
        )

        count = self.client.post(
            "/api/campaigns/recipient-count",
            data='{"audience_verification":"unverified_only"}',
            content_type="application/json",
            **self.auth(),
        )
        preview = self.client.get(
            f"/api/campaigns/{campaign.pk}/recipients",
            **self.auth(),
        )

        self.assertEqual(count.json()["recipient_count"], 1)
        self.assertEqual(preview.json()["count"], 1)
        self.assertEqual(preview.json()["recipients"][0]["email"], eligible.email)
