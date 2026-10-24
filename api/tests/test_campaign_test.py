"""Staff token tests share the existing Studio campaign test-send path."""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from accounts.models import Token, User
from email_app.models import EmailCampaign


@override_settings(RELAY_BASE_URL="https://relay.example.com", RELAY_API_KEY="test-key")
class CampaignTestTokenTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="operator@example.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name="campaign-test")
        cls.campaign = EmailCampaign.objects.create(subject="Proof", body="A controlled test.")
        cls.url = f"/studio/campaigns/{cls.campaign.pk}/test-send"

    def post_test(self, recipients="controlled@example.com", **extra):
        return self.client.post(self.url, {"test_recipients": recipients},
            HTTP_AUTHORIZATION=f"Token {self.token.key}", **extra)

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay", return_value="relay-json-42")
    def test_token_json_uses_existing_studio_send(self, send):
        self.client = Client(enforce_csrf_checks=True)
        result = self.client.post(self.url,
            json.dumps({"test_recipients": "controlled@example.com"}),
            content_type="application/json", HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["relay_message_id"], "relay-json-42")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["recipient_email"], "controlled@example.com")

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_token_json_rejects_malformed_and_wrong_types_without_sending(self, send):
        bodies = ("[", "[]", "null", '"address@example.com"', "{}")
        for value in (None, True, 1, [], {}):
            bodies += (json.dumps({"test_recipients": value}),)
        for body in bodies:
            with self.subTest(body=body):
                result = self.client.post(self.url, body, content_type="application/json",
                    HTTP_AUTHORIZATION=f"Token {self.token.key}")
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json(), {
                    "error": "Provide exactly one valid test recipient.", "code": "validation_error",
                })
        send.assert_not_called()

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_token_sends_one_existing_studio_test_and_returns_relay_id(self, request):
        response = MagicMock(status_code=200)
        response.json.return_value = {"message": {"id": "relay-test-42", "status": "queued"}}
        request.return_value = response
        self.client = Client(enforce_csrf_checks=True)
        result = self.post_test()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {
            "campaign_id": self.campaign.pk, "relay_message_id": "relay-test-42", "status": "accepted",
        })
        self.assertEqual(request.call_count, 1)
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["email"], "controlled@example.com")
        self.assertEqual(payload["context"]["subject"], "[TEST] Proof")
        self.assertIn("A controlled test.", payload["context"]["html_body"])
        self.assertTrue(payload["metadata"]["is_test_send"])
        self.campaign.refresh_from_db()
        self.assertEqual((self.campaign.status, self.campaign.sent_count), ("draft", 0))

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_token_rejects_missing_invalid_or_multiple_recipients(self, send):
        for recipients in ("", "bad-address", "one@example.com,two@example.com"):
            with self.subTest(recipients=recipients):
                result = self.post_test(recipients)
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json()["code"], "validation_error")
        send.assert_not_called()

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_session_requires_csrf_and_invalid_token_never_falls_back(self, send):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff)
        result = self.client.post(self.url, {"test_recipients": "controlled@example.com"})
        self.assertEqual(result.status_code, 403)
        result = self.client.post(self.url, {"test_recipients": "controlled@example.com"},
            HTTP_AUTHORIZATION="Token invalid")
        self.assertEqual(result.status_code, 401)
        send.assert_not_called()

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_nonstaff_token_cannot_send(self, send):
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        self.assertEqual(self.post_test().status_code, 401)
        send.assert_not_called()

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_anonymous_browser_keeps_login_redirect(self, send):
        self.client = Client(enforce_csrf_checks=True)
        result = self.client.post(self.url, {"test_recipients": "controlled@example.com"})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], f"/accounts/login/?next={self.url}")
        send.assert_not_called()

    @patch("studio.views.campaigns.send_campaign_delivery_to_relay")
    def test_transport_failure_returns_no_provider_payload(self, send):
        send.side_effect = RuntimeError("PRIVATE-TOKEN private@example.com raw provider response")
        result = self.post_test()
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.json(), {
            "error": "Campaign test send failed.", "code": "campaign_test_send_failed",
        })
