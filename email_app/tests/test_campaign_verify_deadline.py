"""Verify-or-delete campaign: one wave, a 7-day promise, a link that outlives it."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.services.campaign_repermission import (
    VERIFY_DEADLINE_CTA,
    VERIFY_DEADLINE_EMAIL_TYPE,
    monitored_message_parts,
    verify_deadline_url_for_user,
)
from email_app.tasks.send_campaign import send_campaign
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


def _token(url):
    return parse_qs(urlparse(url).query)["token"][0]


@tag("core")
class VerifyDeadlineMessageTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = create_user_with_membership(
            email="ignored@example.com", tier=cls.free_tier, email_verified=False,
        )

    def make_campaign(self, **fields):
        values = {
            "subject": "Confirm your email",
            "body": "Hi, Alexey here.",
            "audience_verification": "unverified_only",
            "verify_deadline_days": 7,
        }
        values.update(fields)
        return EmailCampaign.objects.create(**values)

    def test_body_promises_deletion_and_links_to_verify_email(self):
        body, email_type = monitored_message_parts(
            self.make_campaign(), user=self.user,
        )

        self.assertEqual(email_type, VERIFY_DEADLINE_EMAIL_TYPE)
        self.assertIn("within 7 days", body)
        self.assertIn("will be deleted", body)
        self.assertIn(f"[{VERIFY_DEADLINE_CTA}](", body)
        self.assertIn("/api/verify-email?token=", body)
        self.assertNotIn("verify-and-subscribe", body)

    def test_link_token_verifies_not_subscribes_and_outlives_the_deadline(self):
        payload = jwt.decode(
            _token(verify_deadline_url_for_user(self.user, 7)),
            settings.SECRET_KEY,
            algorithms=["HS256"],
        )

        self.assertEqual(payload["action"], "verify_email")
        self.assertEqual(payload["user_id"], self.user.pk)
        remaining = payload["exp"] - timezone.now().timestamp()
        self.assertGreater(remaining, timedelta(days=7).total_seconds())

    def test_campaign_without_deadline_keeps_the_repermission_message(self):
        body, email_type = monitored_message_parts(
            self.make_campaign(verify_deadline_days=None), user=self.user,
        )

        self.assertEqual(email_type, "campaign_repermission")
        self.assertIn("verify-and-subscribe", body)

    def test_deadline_campaign_sends_everyone_as_one_released_wave(self):
        for index in range(130):
            create_user_with_membership(
                email=f"bulk-{index}@example.com",
                tier=self.free_tier,
                email_verified=False,
            )
        campaign = self.make_campaign(status="sending")

        result = send_campaign(campaign.pk)

        waves = list(campaign.waves.all())
        self.assertEqual(result["wave_count"], 1)
        self.assertEqual(len(waves), 1)
        self.assertIsNotNone(waves[0].released_at)
        self.assertEqual(
            waves[0].deliveries.count(), campaign.deliveries.count(),
        )
        self.assertGreater(campaign.deliveries.count(), 100)
