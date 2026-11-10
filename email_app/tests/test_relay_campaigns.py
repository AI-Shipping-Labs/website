"""Unit and integration tests for Relay campaign and newsletter delivery (Path B)."""

import json
import re
import uuid
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    alert_relay_campaign_failure,
    preview_native_test_campaign,
    queue_native_test_campaign,
)
from email_app.tasks.send_campaign import (
    send_campaign_batch,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


@tag("core")
class RelayCampaignDeliveryTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
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
        PUBLIC_BASE_URL="https://aishippinglabs.test",
    )
    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_pending_campaign_queues_exact_snapshot_through_relay_campaign_api(self, mock_request):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.body = "large campaign body " * 5_000
        self.campaign.save(update_fields=["relay_campaign_state", "body"])
        responses = []
        for status, document in [
            (404, {}),
            (200, {"recipient_list": {}}),
            (200, {"recipient_list": {}}),
            (201, {"campaign": {"status": "draft"}}),
            (200, {"recipient_count": 1, "skipped_count": 0}),
            (200, {"queued": True, "recipient_count": 1, "skipped_count": 0,
                   "campaign": {"status": "queued"}}),
        ]:
            response = MagicMock()
            response.status_code = status
            response.json.return_value = document
            responses.append(response)
        mock_request.side_effect = responses

        result = send_campaign_batch(
            self.campaign.pk,
            delivery_ids=[self.delivery.pk],
            send_delay=0,
        )

        self.assertEqual(result["relay_campaign_key"], f"aishl-campaign-{self.campaign.pk}")
        self.assertTrue(result["relay_queued"])
        self.assertEqual(mock_request.call_count, 6)
        clear_call = mock_request.call_args_list[1]
        self.assertTrue(clear_call.args[1].endswith("/members/reconcile"))
        self.assertEqual(clear_call.kwargs["json"]["members"], [])
        list_call = mock_request.call_args_list[2]
        self.assertTrue(list_call.args[1].endswith("/members/bulk-upsert"))
        member = list_call.kwargs["json"]["members"][0]
        self.assertEqual(member["source_object_key"], f"website-user-{self.user.pk}")
        self.assertTrue(member["subscribe_if_missing"])
        recipient_context = member["metadata"]["relay_campaign_context"]
        self.assertEqual(set(recipient_context), {"verify_email_url", "client_recipient_key"})
        self.assertNotIn(self.campaign.body, json.dumps(list_call.kwargs["json"]))
        self.assertLess(len(json.dumps(list_call.kwargs["json"])), 2_000)
        campaign_call = mock_request.call_args_list[3]
        self.assertEqual(campaign_call.kwargs["json"]["template_key"], "campaign-broadcast")
        self.assertEqual(campaign_call.kwargs["json"]["recipient_list_key"], f"aishl-campaign-{self.campaign.pk}")
        self.assertEqual(campaign_call.kwargs["json"]["allow_unverified"], False)
        campaign_context = campaign_call.kwargs["json"]["context"]
        self.assertEqual(campaign_context["body_markdown"], self.campaign.body)
        self.assertEqual(json.dumps(campaign_call.kwargs["json"]).count(self.campaign.body), 1)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(self.delivery.email_log.ses_message_id, "")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_retry_after_partial_local_persistence_confirms_recipient_key(self, mock_request):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        existing = MagicMock()
        existing.status_code = 200
        existing.json.return_value = {
            "campaign": {"status": "queued", "recipient_count": 1377, "skipped_count": 0},
        }
        recipients = MagicMock()
        recipients.status_code = 200
        recipients.json.return_value = {
            "recipients": [{
                "client_recipient_key": str(self.delivery.pk),
                "status": "pending",
            }],
        }
        mock_request.side_effect = [existing, recipients]

        result = send_campaign_batch(
            self.campaign.pk,
            delivery_ids=[self.delivery.pk],
            send_delay=0,
        )

        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(mock_request.call_count, 2)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.SENT)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_queued_retry_missing_recipient_key_fails_without_marking_sent(
        self, mock_request, mock_alert,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        existing = MagicMock()
        existing.status_code = 200
        existing.json.return_value = {
            "campaign": {"status": "queued", "recipient_count": 1377, "skipped_count": 0},
        }
        recipients = MagicMock()
        recipients.status_code = 200
        recipients.json.return_value = {"recipients": []}
        mock_request.side_effect = [existing, recipients]

        with self.assertRaisesRegex(RelayCampaignApiError, "could not confirm 1 local recipients"):
            send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.PENDING)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertEqual(mock_alert.call_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_relay_preflight_count_mismatch_fails_before_local_sent(
        self, mock_request, mock_alert,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        responses = []
        for status, document in [
            (404, {}),
            (200, {"recipient_list": {}}),
            (200, {"recipient_list": {}}),
            (201, {"campaign": {"status": "draft"}}),
            (200, {"recipient_count": 0, "skipped_count": 1}),
        ]:
            response = MagicMock()
            response.status_code = status
            response.json.return_value = document
            responses.append(response)
        mock_request.side_effect = responses

        with self.assertRaisesRegex(RelayCampaignApiError, "preflight recipient count mismatch"):
            send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.PENDING)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertIn("preflight recipient count mismatch", self.campaign.relay_campaign_error)
        self.assertEqual(mock_alert.call_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_cancelled_relay_campaign_fails_without_changing_delivery(
        self, mock_request, mock_alert,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        responses = []
        for terminal_status in ("cancelled", "failed"):
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {"campaign": {"status": terminal_status}}
            responses.append(response)
        mock_request.side_effect = responses

        for terminal_status in ("cancelled", "failed"):
            self.campaign.relay_campaign_state = "pending"
            self.campaign.save(update_fields=["relay_campaign_state"])
            with self.subTest(status=terminal_status):
                with self.assertRaisesRegex(
                    RelayCampaignApiError,
                    f"terminal state '{terminal_status}'",
                ):
                    send_campaign_batch(
                        self.campaign.pk,
                        delivery_ids=[self.delivery.pk],
                        send_delay=0,
                    )
            self.delivery.refresh_from_db()
            self.assertEqual(self.delivery.state, CampaignDelivery.State.PENDING)
            self.campaign.refresh_from_db()
            self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertEqual(mock_alert.call_count, 2)

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
        for index in range(9):  # GET, reconcile, four member chunks, PUT, recount, queue
            response = MagicMock()
            response.status_code = 404 if index == 0 else 200
            response.json.return_value = (
                {"recipient_count": 200, "skipped_count": 0}
                if index in {7, 8}
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

    @patch("email_app.services.relay_campaigns.send_mail", side_effect=RuntimeError("alert unavailable"))
    def test_failure_alert_does_not_mask_original_error(self, _mock_send_mail):
        original = RelayCampaignApiError("Relay request timed out")

        alert_relay_campaign_failure(self.campaign, "queue", original)

        self.assertEqual(str(original), "Relay request timed out")

    @patch(
        "email_app.services.relay_campaigns.send_mail",
        side_effect=RuntimeError("alert unavailable"),
    )
    @patch(
        "email_app.tasks.send_campaign.queue_native_campaign",
        side_effect=RelayCampaignApiError("Relay request timed out"),
    )
    def test_queue_failure_propagates_when_alert_delivery_fails(
        self, _mock_queue, _mock_send_mail,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])

        with self.assertRaisesRegex(RelayCampaignApiError, "Relay request timed out"):
            send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk])

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertIn("Relay request timed out", self.campaign.relay_campaign_error)

    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    @patch(
        "email_app.tasks.send_campaign.queue_native_campaign",
        side_effect=RelayCampaignApiError("Relay request timed out after 30s"),
    )
    def test_native_queue_failure_marks_campaign_needs_attention_and_alerts(
        self, mock_queue, mock_alert,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])

        with self.assertRaisesRegex(RelayCampaignApiError, "timed out after 30s"):
            send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk])

        self.campaign.refresh_from_db()
        self.delivery.refresh_from_db()
        self.assertEqual(self.campaign.status, "needs_attention")
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertIn("timed out after 30s", self.campaign.relay_campaign_error)
        self.assertEqual(self.delivery.state, CampaignDelivery.State.PENDING)
        self.assertEqual(mock_queue.call_count, 1)
        self.assertEqual(mock_alert.call_count, 1)

    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    def test_first_stale_quarantined_task_persists_error_and_alerts_once(self, mock_alert):
        campaign = EmailCampaign.objects.create(
            subject="Legacy campaign",
            body="Old campaign body",
            status="needs_attention",
            audience_snapshotted_at=timezone.now(),
            relay_campaign_state="",
        )

        first = send_campaign_batch(campaign.pk, user_ids=[self.user.pk])
        second = send_campaign_batch(campaign.pk, user_ids=[self.user.pk])

        self.assertTrue(first["legacy_quarantined"])
        self.assertTrue(second["legacy_quarantined"])
        campaign.refresh_from_db()
        self.assertIn("pre-Relay send state cannot be reconstructed", campaign.relay_campaign_error)
        self.assertEqual(campaign.status, "needs_attention")
        self.assertEqual(mock_alert.call_count, 1)

    @override_settings(TESTING=False)
    @patch("email_app.tasks.send_campaign.alert_relay_campaign_failure")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_production_campaign_without_native_state_fails_without_transactional_fallback(
        self, mock_request, _mock_alert,
    ):
        with self.assertRaisesRegex(RelayCampaignApiError, "refusing transactional fallback"):
            send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk])

        mock_request.assert_not_called()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "failed")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_send_campaign_batch_success(self, mock_request):
        """Standard campaign batch sends via Relay transactional send API and updates ledger."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "id": "relay-msg-test-789",
                "status": "queued",
                "idempotency_key": f"campaign-{self.campaign.pk}-delivery-{self.delivery.pk}",
            },
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        result = send_campaign_batch(
            self.campaign.pk,
            delivery_ids=[self.delivery.pk],
            send_delay=0,
        )

        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["ambiguous_count"], 0)

        # Verify Relay request payload
        self.assertEqual(mock_request.call_count, 1)
        method, url = mock_request.call_args[0]
        kwargs = mock_request.call_args[1]

        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://relay.test.datatalks.club/api/transactional/send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-relay-key-123")

        payload = kwargs["json"]
        self.assertEqual(payload["email"], self.user.email)
        self.assertEqual(payload["template_key"], "campaign-broadcast")
        self.assertEqual(
            payload["idempotency_key"],
            f"campaign-{self.campaign.pk}-delivery-{self.delivery.pk}",
        )
        self.assertEqual(payload["category"], "newsletter")
        self.assertIn("headers", payload)
        self.assertIn("List-Unsubscribe", payload["headers"])
        self.assertIn("List-Unsubscribe-Post", payload["headers"])
        self.assertEqual(payload["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

        context = payload["context"]
        self.assertEqual(context["subject"], self.campaign.subject)
        self.assertEqual(context["body_markdown"], self.campaign.body)

        # Verify delivery ledger updated
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(self.delivery.ses_message_id, "relay-msg-test-789")
        self.assertIsNotNone(self.delivery.completed_at)
        self.assertIsNotNone(self.delivery.email_log)

        # Verify EmailLog created
        log = EmailLog.objects.filter(campaign=self.campaign, user=self.user).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.ses_message_id, "relay-msg-test-789")
        self.assertEqual(log.email_type, "campaign")

        # Verify campaign status updated to sent
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "sent")
        self.assertEqual(self.campaign.sent_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_deduplication_and_idempotency_prevents_double_send(self, mock_request):
        """Deliveries already sent or with existing logs are not sent again."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"id": "relay-msg-initial", "status": "queued"},
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        # First run succeeds
        send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)
        self.assertEqual(mock_request.call_count, 1)

        # Second run: delivery is already SENT
        result2 = send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)
        self.assertEqual(result2["sent_count"], 0)
        # Mock request was not called again
        self.assertEqual(mock_request.call_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_relay_rejection_fails_loud_with_error_log(self, mock_request):
        """Relay definitive rejection (409 suppression) marks delivery FAILED and logs ERROR."""
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {
            "error": {"code": "transactional_suppressed", "reason": "hard_bounce"},
            "message": {"status": "suppressed"},
        }
        mock_request.return_value = mock_response

        with self.assertLogs("email_app.tasks.send_campaign", level="ERROR") as logs:
            result = send_campaign_batch(
                self.campaign.pk,
                delivery_ids=[self.delivery.pk],
                send_delay=0,
            )

        self.assertEqual(result["sent_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("Relay definitively rejected", logs.output[0])

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.FAILED)
        self.assertIn("hard_bounce", self.delivery.last_error)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "needs_attention")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_relay_ambiguous_failure_fails_loud_with_error_log(self, mock_request):
        """Relay network timeout marks delivery AMBIGUOUS and suppresses automatic retry."""
        mock_request.side_effect = requests.exceptions.Timeout("Read timeout from Relay")

        with self.assertLogs("email_app.tasks.send_campaign", level="ERROR") as logs:
            result = send_campaign_batch(
                self.campaign.pk,
                delivery_ids=[self.delivery.pk],
                send_delay=0,
            )

        self.assertEqual(result["sent_count"], 0)
        self.assertEqual(result["ambiguous_count"], 1)
        self.assertIn("Relay transport outcome was indeterminate", logs.output[0])

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertIn("timed out", self.delivery.last_error)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "needs_attention")


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
