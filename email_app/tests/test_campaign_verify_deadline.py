"""Verify-or-delete campaign: one wave, a 7-day promise, a link that outlives it."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, tag
from django.utils import timezone

from email_app.admin.email_campaign import EmailCampaignAdmin
from email_app.models import EmailCampaign
from email_app.services.campaign_repermission import (
    VERIFY_DEADLINE_CTA,
    VERIFY_DEADLINE_EMAIL_TYPE,
    monitored_message_parts,
    verify_deadline_url_for_user,
)
from email_app.services.campaign_submit import build_campaign_recipients
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


def _token(url):
    return parse_qs(urlparse(url).query)["token"][0]


class CampaignAdminConcurrentSendTest(TestCase):
    def test_admin_rejects_stale_content_after_send_begins(self):
        campaign = EmailCampaign.objects.create(
            subject="Confirm", body="Delete after 7 days.",
            audience_verification="unverified_only", verify_deadline_days=7,
        )
        stale_campaign = EmailCampaign.objects.get(pk=campaign.pk)
        EmailCampaign.objects.filter(pk=campaign.pk).update(
            status="sending",
        )
        stale_campaign.body = "No deletion warning."
        campaign_admin = EmailCampaignAdmin(EmailCampaign, AdminSite())

        with self.assertRaises(PermissionDenied):
            campaign_admin.save_model(None, stale_campaign, None, True)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sending")
        self.assertEqual(campaign.body, "Delete after 7 days.")

    def test_admin_stale_noop_preserves_send_snapshot(self):
        campaign = EmailCampaign.objects.create(
            subject="Confirm", body="Delete after 7 days.",
            audience_verification="unverified_only", verify_deadline_days=7,
        )
        stale_campaign = EmailCampaign.objects.get(pk=campaign.pk)
        snapshot_time = timezone.now()
        EmailCampaign.objects.filter(pk=campaign.pk).update(
            status="sending", audience_snapshotted_at=snapshot_time,
        )
        campaign_admin = EmailCampaignAdmin(EmailCampaign, AdminSite())

        campaign_admin.save_model(None, stale_campaign, None, True)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sending")
        self.assertEqual(campaign.audience_snapshotted_at, snapshot_time)


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
        below_divider = body.split("\n---\n")[-1].strip()
        self.assertRegex(
            below_divider,
            rf"^\[{VERIFY_DEADLINE_CTA}\]\(https://[^)\s]+/api/verify-email\?token=[^)\s]+\)$",
        )
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

    def test_deadline_campaign_submits_everyone_in_one_recipient_list(self):
        for index in range(130):
            create_user_with_membership(
                email=f"bulk-{index}@example.com",
                tier=self.free_tier,
                email_verified=False,
            )
        campaign = self.make_campaign(status="sending")

        recipients, users = build_campaign_recipients(campaign)

        self.assertGreater(len(recipients), 100)
        self.assertEqual(len(recipients), len(users))
        for recipient in recipients:
            context = recipient["metadata"]["relay_campaign_context"]
            self.assertIn("/api/verify-email?token=", context["verify_email_url"])


@tag("core")
class VerifyDeadlineTestSendTest(TierSetupMixin, TestCase):
    def make_campaign(self, **fields):
        values = {
            "subject": "Confirm your email",
            "body": "Hi, Alexey here.",
            "audience_verification": "unverified_only",
            "verify_deadline_days": 7,
        }
        values.update(fields)
        return EmailCampaign.objects.create(**values)

    def test_verified_operator_gets_the_deadline_test_with_unsubscribe_link(self):
        from email_app.services.campaign_test_send import _test_message

        operator = create_user_with_membership(
            email="operator@example.com", tier=self.free_tier, email_verified=True,
        )

        message = _test_message(
            self.make_campaign(), operator, operator.email, "[TEST] s",
        )

        self.assertEqual(
            message.body_markdown.split("\n---\n")[-1].strip().count("\n"), 0,
        )
        self.assertIn("/api/verify-email?token=", message.body_markdown)
        self.assertIn("/api/unsubscribe", message.unsubscribe_url)

    def test_repermission_test_still_rejects_a_verified_user(self):
        from email_app.services.campaign_test_send import (
            CampaignTestRecipientError,
            _test_message,
        )

        operator = create_user_with_membership(
            email="operator2@example.com", tier=self.free_tier, email_verified=True,
        )

        with self.assertRaises(CampaignTestRecipientError):
            _test_message(
                self.make_campaign(verify_deadline_days=None),
                operator, operator.email, "[TEST] s",
            )
