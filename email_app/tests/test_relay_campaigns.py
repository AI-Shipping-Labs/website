"""Unit and integration tests for Relay campaign and newsletter delivery (Path B)."""

import re
import uuid
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    alert_relay_campaign_failure,
    preview_native_test_campaign,
    queue_native_test_campaign,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


@tag("core")
class RelayCampaignDeliveryTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        # The 60-minute alert cooldown lives in the django_q cache; campaign
        # pks repeat across rolled-back tests, so a stale entry would silence
        # the delivery assertions below.
        caches["django_q"].clear()
        self.user = create_user_with_membership(
            email=f"recipient-{uuid.uuid4().hex[:8]}@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Relay Campaign Test Subject",
            body="Hello **member**, this is a test newsletter body.",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        self.delivery = CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.PENDING,
        )







    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    @patch("email_app.services.relay_campaigns.get_config")
    def test_test_campaign_uses_only_exact_allowed_list_without_audience_resolution(
        self, mock_get_config, mock_request,
    ):
        addresses = [f"seed-{index:03d}@example.test" for index in range(200)]
        mock_get_config.side_effect = lambda key, default="", **kwargs: (
            ",".join(addresses) if key == "CAMPAIGN_TEST_RECIPIENTS" else default
        )
        responses = []
        # GET, reconcile, four member chunks, 200 contact verifications, PUT,
        # recount, queue
        for index in range(9 + 200):
            response = MagicMock()
            response.status_code = 404 if index == 0 else 200
            response.json.return_value = (
                {"recipient_count": 200, "skipped_count": 0}
                if index in {207, 208}
                else {"queued": True, "campaign": {"status": "queued"}}
            )
            responses.append(response)
        mock_request.side_effect = responses

        with (
            patch("email_app.services.relay_campaigns.get_relay_template_key", return_value="campaign-broadcast"),
            patch("email_app.services.relay_campaigns.get_relay_from_email", return_value="newsletter"),
            patch.object(
                self.campaign,
                "get_eligible_recipients",
                side_effect=AssertionError("normal audience resolver must not run"),
            ),
        ):
            result = queue_native_test_campaign(self.campaign)

        self.assertEqual(result["recipient_count"], 200)
        self.assertTrue(result["queued"])
        self.assertTrue(result["campaign_key"].startswith(f"aishl-test-{self.campaign.pk}-"))
        self.assertEqual(result["recipient_list_key"], result["campaign_key"])
        member_calls = [call for call in mock_request.call_args_list if call.args[1].endswith("bulk-upsert")]
        queued_members = [
            member
            for call in member_calls
            for member in call.kwargs["json"]["members"]
        ]
        self.assertEqual(len(queued_members), 200)
        self.assertEqual({member["email"] for member in queued_members}, set(addresses))
        verification_calls = [
            call for call in mock_request.call_args_list
            if call.args[1].endswith("/api/contacts")
        ]
        self.assertEqual(len(verification_calls), 200)
        self.assertTrue(all(
            call.kwargs["json"]["verified"] is True for call in verification_calls
        ))
        self.assertEqual(
            {call.kwargs["json"]["email"] for call in verification_calls},
            set(addresses),
        )
        campaign_payload = next(
            call.kwargs["json"]
            for call in mock_request.call_args_list
            if call.args[0] == "PUT"
        )
        self.assertTrue(campaign_payload["metadata"]["is_test_campaign"])
        self.assertTrue(campaign_payload["subject"].startswith("[TEST] "))

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    @patch("email_app.services.relay_campaigns.get_config")
    def test_test_campaign_refuses_empty_invalid_and_oversize_lists_before_relay(
        self, mock_get_config, mock_request,
    ):
        for raw in ("", "not-an-address", ",".join(
            f"seed-{index:03d}@example.test" for index in range(501)
        )):
            with self.subTest(address_count=len(raw.split(",")) if raw else 0):
                mock_get_config.side_effect = lambda key, default="", **kwargs: (
                    raw if key == "CAMPAIGN_TEST_RECIPIENTS" else default
                )
                with self.assertRaises(RelayCampaignApiError):
                    queue_native_test_campaign(self.campaign)
        mock_request.assert_not_called()

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    @patch("email_app.services.relay_campaigns.get_config")
    def test_test_campaign_preflight_key_is_bound_to_campaign_and_exact_list(
        self, mock_get_config, mock_request,
    ):
        first = [f"seed-{index:03d}@example.test" for index in range(200)]
        second = [f"changed-{index:03d}@example.test" for index in range(200)]
        configured = {"value": ",".join(first)}
        mock_get_config.side_effect = lambda key, default="", **kwargs: (
            configured["value"] if key == "CAMPAIGN_TEST_RECIPIENTS" else default
        )

        plan = preview_native_test_campaign(self.campaign)
        self.assertEqual(plan["recipient_count"], 200)
        with self.assertRaisesRegex(RelayCampaignApiError, "belongs to another campaign"):
            preview_native_test_campaign(
                EmailCampaign(pk=self.campaign.pk + 1),
                campaign_key=plan["campaign_key"],
            )
        configured["value"] = ",".join(second)
        with self.assertRaisesRegex(RelayCampaignApiError, "changed after preflight"):
            queue_native_test_campaign(self.campaign, campaign_key=plan["campaign_key"])

        mock_request.assert_not_called()

    @patch("email_app.management.commands.queue_relay_test_campaign.queue_native_test_campaign")
    @patch("email_app.services.relay_campaigns.get_config")
    def test_test_campaign_command_requires_explicit_key_and_dry_run_hides_addresses(
        self, mock_get_config, mock_queue,
    ):
        addresses = [f"private-seed-{index:03d}@example.test" for index in range(200)]
        mock_get_config.side_effect = lambda key, default="", **kwargs: (
            ",".join(addresses) if key == "CAMPAIGN_TEST_RECIPIENTS" else default
        )
        output = StringIO()
        call_command(
            "queue_relay_test_campaign",
            str(self.campaign.pk),
            "--dry-run",
            stdout=output,
        )
        rendered = output.getvalue()
        self.assertIn("recipient_count=200", rendered)
        self.assertIn("domain_counts=example.test=200", rendered)
        self.assertNotIn("private-seed-", rendered)
        key = re.search(
            r"campaign_key=(aishl-test-[0-9]+-[0-9a-f]{32}-[0-9a-f]{32})",
            rendered,
        ).group(1)

        mock_queue.return_value = {
            "campaign_key": key,
            "recipient_count": 200,
            "queued": True,
        }
        call_command("queue_relay_test_campaign", str(self.campaign.pk), "--confirm", key)
        mock_queue.assert_called_once_with(self.campaign, campaign_key=key)

    @override_settings(
        EMAIL_BACKEND="email_app.backends.relay.RelayEmailBackend",
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.backends.relay.requests.post")
    def test_relay_failure_alert_is_sent_through_relay(self, mock_post):
        response = MagicMock()
        response.status_code = 202
        mock_post.return_value = response

        alert_relay_campaign_failure(self.campaign, "queue", RuntimeError("relay timeout"))

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(
            mock_post.call_args.args[0],
            "https://relay.test.datatalks.club/api/transactional/send",
        )
        self.assertEqual(mock_post.call_args.kwargs["json"]["email"], "dev@dtcdev.click")

    @patch("email_app.services.relay_campaign_alerts.send_mail", side_effect=RuntimeError("alert unavailable"))
    def test_failure_alert_does_not_mask_original_error(self, _mock_send_mail):
        original = RelayCampaignApiError("Relay request timed out")

        alert_relay_campaign_failure(self.campaign, "queue", original)

        self.assertEqual(str(original), "Relay request timed out")










@tag("core")
class RelayStudioCampaignTestSendTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = create_user_with_membership(
            email="staff@aishippinglabs.com",
            is_staff=True,
            tier=self.free_tier,
        )
        self.client.force_login(self.staff_user)
        self.campaign = EmailCampaign.objects.create(
            subject="Studio Test Newsletter",
            body="Hello from Studio test send.",
            status="draft",
        )

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_studio_test_send_calls_relay(self, mock_request):
        """Studio campaign test send sends via Relay with [TEST] prefix and returns success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"id": "relay-msg-studio-test", "status": "queued"},
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        response = self.client.post(
            reverse("studio_campaign_test_send", args=[self.campaign.pk]),
            {"test_recipients": "alexey.s.grigoriev@gmail.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_request.call_count, 1)
        payload = mock_request.call_args[1]["json"]

        self.assertEqual(payload["email"], "alexey.s.grigoriev@gmail.com")
        self.assertEqual(payload["template_key"], "campaign-broadcast")
        self.assertEqual(payload["context"]["subject"], "[TEST] Studio Test Newsletter")
        self.assertEqual(
            payload["context"]["body_markdown"],
            "Hello from Studio test send.",
        )
        self.assertTrue(payload["metadata"]["is_test_send"])
