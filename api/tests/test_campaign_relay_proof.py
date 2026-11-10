"""Staff API for the guarded internal-only Relay bulk proof (issue #2072).

The routes parse input and map ``email_app.services.relay_campaigns`` outcomes
to responses. Guards (list source, 200-500 count, domain allowlist, HMAC key)
are covered at the service level in ``email_app.tests.test_relay_campaigns``;
these tests cover the HTTP contract, including that no recipient address ever
reaches a response body or a log line.
"""

import json
import logging
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token
from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    preview_native_test_campaign,
)

User = get_user_model()

ALLOWED_DOMAIN = "dtcdev.click"


def proof_list(count=200, domain=ALLOWED_DOMAIN):
    return ",".join(f"seed-{index:03d}@{domain}" for index in range(count))


class RelayProofApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-relay-proof@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-relay-proof@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="relay-proof")
        cls.non_staff_token = Token(
            key="non-staff-relay-proof-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.campaign = EmailCampaign.objects.create(
            subject="Proof Subject",
            body="Proof body.",
            status="draft",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post_json(self, path, payload=None, *, token=None):
        return self.client.post(
            path,
            data=json.dumps(payload if payload is not None else {}),
            content_type="application/json",
            **self._auth(token),
        )

    def _dry_run_key(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            return preview_native_test_campaign(self.campaign)["campaign_key"]


class RelayProofAuthTest(RelayProofApiTestBase):
    def test_all_three_routes_require_a_staff_token(self):
        key = "aishl-test-1-0" * 5
        cases = [
            ("post", f"/api/campaigns/{self.campaign.pk}/relay-proof/dry-run", {}),
            (
                "post",
                f"/api/campaigns/{self.campaign.pk}/relay-proof/confirm",
                {"campaign_key": key},
            ),
            ("get", f"/api/campaigns/{self.campaign.pk}/relay-proof/{key}", None),
        ]
        for method, path, body in cases:
            for headers in ({}, self._auth(self.non_staff_token)):
                with self.subTest(method=method, authenticated=bool(headers)):
                    fn = getattr(self.client, method)
                    if body is None:
                        response = fn(path, **headers)
                    else:
                        response = fn(
                            path,
                            data=json.dumps(body),
                            content_type="application/json",
                            **headers,
                        )
                    self.assertEqual(response.status_code, 401)

    def test_unknown_campaign_is_404_on_all_three_routes(self):
        missing = self.campaign.pk + 100
        key = self._dry_run_key()
        cases = [
            ("post", f"/api/campaigns/{missing}/relay-proof/dry-run", {}),
            (
                "post",
                f"/api/campaigns/{missing}/relay-proof/confirm",
                {"campaign_key": key},
            ),
            ("get", f"/api/campaigns/{missing}/relay-proof/{key}", None),
        ]
        for method, path, body in cases:
            with self.subTest(method=method):
                fn = getattr(self.client, method)
                if body is None:
                    response = fn(path, **self._auth())
                else:
                    response = fn(
                        path,
                        data=json.dumps(body),
                        content_type="application/json",
                        **self._auth(),
                    )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], "unknown_campaign")


class RelayProofDryRunTest(RelayProofApiTestBase):
    def test_dry_run_returns_only_key_count_and_domain_counts(self):
        with (
            override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()),
            patch(
                "email_app.services.relay_campaigns.requests.Session.request"
            ) as mock_request,
        ):
            response = self._post_json(
                f"/api/campaigns/{self.campaign.pk}/relay-proof/dry-run",
            )
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertEqual(set(body), {"campaign_key", "recipient_count", "domain_counts"})
        self.assertEqual(body["recipient_count"], 200)
        self.assertEqual(body["domain_counts"], {ALLOWED_DOMAIN: 200})
        self.assertTrue(body["campaign_key"].startswith(f"aishl-test-{self.campaign.pk}-"))
        mock_request.assert_not_called()

    def test_dry_run_rejects_lists_outside_the_guards_with_422(self):
        cases = {
            "empty": "",
            "too_few": ",".join(f"s{i}@{ALLOWED_DOMAIN}" for i in range(199)),
            "too_many": ",".join(f"s{i}@{ALLOWED_DOMAIN}" for i in range(501)),
            "disallowed_domain": ",".join(f"s{i}@gmail.com" for i in range(200)),
        }
        for name, recipients in cases.items():
            with (
                self.subTest(case=name),
                override_settings(CAMPAIGN_TEST_RECIPIENTS=recipients),
                patch(
                    "email_app.services.relay_campaigns.requests.Session.request"
                ) as mock_request,
            ):
                response = self._post_json(
                    f"/api/campaigns/{self.campaign.pk}/relay-proof/dry-run",
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "relay_proof_rejected")
                mock_request.assert_not_called()


class RelayProofConfirmTest(RelayProofApiTestBase):
    def _confirm(self, campaign_key, **extra):
        return self._post_json(
            f"/api/campaigns/{self.campaign.pk}/relay-proof/confirm",
            {"campaign_key": campaign_key, **extra},
        )

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    def test_confirm_queues_with_the_dry_run_key_and_reports_queued(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            key = preview_native_test_campaign(self.campaign)["campaign_key"]
            responses = []
            for index in range(9):  # GET, reconcile, four member chunks, PUT, recount, queue
                payload = MagicMock()
                payload.status_code = 404 if index == 0 else 200
                payload.json.return_value = (
                    {"recipient_count": 200, "skipped_count": 0}
                    if index in {7, 8}
                    else {"campaign": {"status": "queued"}}
                )
                responses.append(payload)
            with (
                patch(
                    "email_app.services.relay_campaigns.requests.Session.request",
                    side_effect=responses,
                ) as mock_request,
                patch(
                    "email_app.services.relay_campaigns.get_relay_template_key",
                    return_value="campaign-broadcast",
                ),
                patch(
                    "email_app.services.relay_campaigns.get_relay_from_email",
                    return_value="newsletter",
                ),
                patch.object(
                    EmailCampaign,
                    "get_eligible_recipients",
                    side_effect=AssertionError("audience resolver must never run"),
                ),
            ):
                response = self._confirm(key)

        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertEqual(
            set(body), {"campaign_key", "recipient_count", "domain_counts", "queued"},
        )
        self.assertEqual(body["campaign_key"], key)
        self.assertEqual(body["recipient_count"], 200)
        self.assertTrue(body["queued"])
        queue_call = mock_request.call_args_list[-1]
        self.assertTrue(queue_call.args[1].endswith(f"/api/campaigns/{key}/queue"))
        self.assertEqual(CampaignDelivery.objects.count(), 0)

    def test_confirm_rejects_tampered_foreign_and_stale_keys_before_relay(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            key = preview_native_test_campaign(self.campaign)["campaign_key"]
        tampered = key[:-1] + ("0" if key[-1] != "0" else "1")
        # Build a valid key bound to another campaign id from the same list.
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            other_key = preview_native_test_campaign(
                EmailCampaign(pk=self.campaign.pk + 1),
            )["campaign_key"]
        cases = {
            "tampered": tampered,
            "other_campaign": other_key,
            "audience_key": f"aishl-campaign-{self.campaign.pk}",
        }
        for name, campaign_key in cases.items():
            with (
                self.subTest(case=name),
                override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()),
                patch(
                    "email_app.services.relay_campaigns.requests.Session.request"
                ) as mock_request,
            ):
                response = self._confirm(campaign_key)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "relay_proof_rejected")
                mock_request.assert_not_called()

        # A key minted before CAMPAIGN_TEST_RECIPIENTS changed must not queue.
        with (
            override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list(300)),
            patch(
                "email_app.services.relay_campaigns.requests.Session.request"
            ) as mock_request,
        ):
            response = self._confirm(key)
            self.assertEqual(response.status_code, 422)
            self.assertIn("changed after preflight", response.json()["error"])
            mock_request.assert_not_called()

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    def test_confirm_reports_a_used_key_as_409(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            key = preview_native_test_campaign(self.campaign)["campaign_key"]
            existing = MagicMock()
            existing.status_code = 200
            existing.json.return_value = {"campaign": {"status": "queued"}}
            with patch(
                "email_app.services.relay_campaigns.requests.Session.request",
                return_value=existing,
            ):
                response = self._confirm(key)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "relay_proof_key_used")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    def test_confirm_maps_a_relay_failure_to_502(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            key = preview_native_test_campaign(self.campaign)["campaign_key"]
            with (
                patch(
                    "email_app.services.relay_campaigns.requests.Session.request",
                    side_effect=RelayCampaignApiError("Relay campaign API PUT /api/campaigns returned HTTP 500"),
                ),
            ):
                response = self._confirm(key)
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["code"], "relay_proof_queue_failed")
        self.assertEqual(CampaignDelivery.objects.count(), 0)

    def test_confirm_requires_a_string_key(self):
        for payload in ({}, {"campaign_key": 12}, {"campaign_key": None}, {"campaign_key": ""}):
            with self.subTest(payload=payload):
                response = self._post_json(
                    f"/api/campaigns/{self.campaign.pk}/relay-proof/confirm",
                    payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")


class RelayProofStatsTest(RelayProofApiTestBase):
    NATIVE = {
        "campaign_key": "unused",
        "status": "sent",
        "recipient_count": 200,
        "skipped_count": 0,
        "sent_count": 200,
        "delivered_count": 198,
        "unique_open_count": 7,
        "open_count": 9,
        "unique_click_count": 2,
        "click_count": 3,
        "unsubscribe_count": 0,
        "bounce_count": 2,
        "complaint_count": 0,
        "created_at": "2026-11-10T16:00:00Z",
        "updated_at": "2026-11-10T16:00:05Z",
        "scheduled_at": None,
        "sent_at": "2026-11-10T16:00:05Z",
        "send_halted": False,
        "send_halted_at": None,
        "send_halt_reason": "",
        "html_body": "<p>secret</p>",
        "text_body": "secret",
        "context": {"body_markdown": "secret"},
        "metadata": {"secret": True},
        "recipient_list_key": "aishl-test-1-secret",
    }
    ALLOWED = {
        "campaign_key", "status", "recipient_count", "skipped_count",
        "sent_count", "delivered_count", "unique_open_count", "open_count",
        "unique_click_count", "click_count", "unsubscribe_count",
        "bounce_count", "complaint_count", "created_at", "updated_at",
        "scheduled_at", "sent_at", "send_halted", "send_halted_at",
        "send_halt_reason",
    }

    def _stats(self, campaign_key):
        return self.client.get(
            f"/api/campaigns/{self.campaign.pk}/relay-proof/{campaign_key}",
            **self._auth(),
        )

    def test_stats_returns_only_the_allowlisted_fields(self):
        key = self._dry_run_key()
        with (
            override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()),
            patch(
                "api.views.campaign_relay_proof.get_native_campaign_stats",
                return_value=dict(self.NATIVE),
            ) as mock_stats,
        ):
            response = self._stats(key)
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertEqual(set(body), self.ALLOWED)
        self.assertEqual(body["campaign_key"], key)
        self.assertEqual(body["sent_count"], 200)
        self.assertEqual(body["sent_at"], "2026-11-10T16:00:05Z")
        mock_stats.assert_called_once_with(key, allow_not_found=True)

    def test_stats_rejects_non_proof_keys_without_calling_relay(self):
        with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
            other_key = preview_native_test_campaign(
                EmailCampaign(pk=self.campaign.pk + 1),
            )["campaign_key"]
        cases = [
            f"aishl-campaign-{self.campaign.pk}",
            other_key,
            f"aishl-test-{self.campaign.pk}-short",
        ]
        for campaign_key in cases:
            with (
                self.subTest(key=campaign_key),
                patch(
                    "email_app.services.relay_campaigns.requests.Session.request"
                ) as mock_request,
            ):
                response = self._stats(campaign_key)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "relay_proof_rejected")
                mock_request.assert_not_called()

    def test_stats_still_reads_a_valid_key_after_the_list_changed(self):
        key = self._dry_run_key()
        with (
            override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list(450)),
            patch(
                "api.views.campaign_relay_proof.get_native_campaign_stats",
                return_value={"status": "sending", "sent_count": 40},
            ) as mock_stats,
        ):
            response = self._stats(key)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["sent_count"], 40)
        self.assertEqual(mock_stats.call_count, 1)

    def test_stats_maps_missing_and_failing_relay_reads(self):
        key = self._dry_run_key()
        with patch(
            "api.views.campaign_relay_proof.get_native_campaign_stats",
            return_value=None,
        ):
            response = self._stats(key)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "relay_proof_not_found")

        with patch(
            "api.views.campaign_relay_proof.get_native_campaign_stats",
            side_effect=RelayCampaignApiError("Relay campaign API GET returned HTTP 503"),
        ):
            response = self._stats(key)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "relay_campaign_stats_unavailable")


class RelayProofLeakTest(RelayProofApiTestBase):
    """No configured recipient address may reach a body or a log line."""

    def test_no_recipient_address_appears_in_bodies_or_logs(self):
        addresses = proof_list().split(",")
        leak_records = []

        class LeakHandler(logging.Handler):
            def emit(self, record):
                leak_records.append(self.format(record))

        handler = LeakHandler(level=logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            scenarios = []
            with override_settings(CAMPAIGN_TEST_RECIPIENTS=proof_list()):
                key = preview_native_test_campaign(self.campaign)["campaign_key"]
                scenarios.append(self._post_json(
                    f"/api/campaigns/{self.campaign.pk}/relay-proof/dry-run",
                ))
                failure = MagicMock()
                failure.status_code = 500
                failure.json.side_effect = ValueError("no json")
                with patch(
                    "email_app.services.relay_campaigns.requests.Session.request",
                    return_value=failure,
                ):
                    scenarios.append(self._post_json(
                        f"/api/campaigns/{self.campaign.pk}/relay-proof/confirm",
                        {"campaign_key": key},
                    ))
                with patch(
                    "api.views.campaign_relay_proof.get_native_campaign_stats",
                    side_effect=RelayCampaignApiError("Relay campaign API GET returned HTTP 503"),
                ):
                    scenarios.append(self.client.get(
                        f"/api/campaigns/{self.campaign.pk}/relay-proof/{key}",
                        **self._auth(),
                    ))
            bodies = [response.content.decode() for response in scenarios]
        finally:
            root.removeHandler(handler)

        for body in bodies:
            for address in addresses:
                self.assertNotIn(address, body)
        for record in leak_records:
            for address in addresses:
                self.assertNotIn(address, record)


class RelayProofOpenApiTest(RelayProofApiTestBase):
    def test_all_three_routes_are_in_the_openapi_output(self):
        response = self.client.get("/api/openapi.json", **self._auth())
        self.assertEqual(response["Content-Type"], "application/json")
        paths = response.json()["paths"]
        for suffix in ("dry-run", "confirm"):
            self.assertIn(f"/api/campaigns/{{campaign_id}}/relay-proof/{suffix}", paths)
        self.assertIn("/api/campaigns/{campaign_id}/relay-proof/{campaign_key}", paths)
