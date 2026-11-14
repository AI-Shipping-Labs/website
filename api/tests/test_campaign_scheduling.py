"""Tests for the campaign dispatch API (schedule/unschedule/send/test-send).

``api/views/campaign_scheduling.py`` is the one sanctioned API send surface;
the draft module (``api/views/campaigns.py``) keeps a sentinel forbidding
send references there. These tests pin the action contracts: auth, draft-only
schedule state changes, exactly-once claim, and the Studio-mirroring test
send.
"""

import json
from datetime import timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from email_app.models import EmailCampaign
from email_app.testing_campaign_submit import stub_relay_submit

User = get_user_model()


def _future_berlin_inputs(days_ahead, hour):
    """Naive Europe/Berlin datetime ~days_ahead out and its UTC instant.

    The schedule endpoint validates "in the future" against the real wall
    clock, so the fixture datetimes are computed at import time instead of
    being hard-coded. Hour 10 local never lands in a DST transition.
    """
    berlin = ZoneInfo("Europe/Berlin")
    local = (
        timezone.now().astimezone(berlin) + timedelta(days=days_ahead)
    ).replace(hour=hour, minute=0, second=0, microsecond=0)
    instant = local.replace(tzinfo=berlin).astimezone(dt_timezone.utc)
    return local.strftime("%Y-%m-%dT%H:%M:%S"), instant


def _future_utc_inputs(days_ahead, hour):
    """Naive UTC datetime ~days_ahead out and its aware UTC instant."""
    instant = (
        timezone.now() + timedelta(days=days_ahead)
    ).astimezone(dt_timezone.utc).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    return instant.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), instant


FUTURE_LOCAL, FUTURE_UTC = _future_berlin_inputs(days_ahead=1, hour=10)
RESCHEDULE_LOCAL, RESCHEDULE_UTC = _future_utc_inputs(days_ahead=2, hour=8)


class CampaignSchedulingApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-scheduling@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-scheduling@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="scheduling")
        cls.non_staff_token = Token(
            key="non-staff-scheduling-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.draft = EmailCampaign.objects.create(
            subject="Scheduling Draft",
            body="# hello draft",
            status="draft",
        )
        cls.sent = EmailCampaign.objects.create(
            subject="Already Sent",
            body="# sent body",
            status="sent",
            sent_count=42,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, campaign_id, payload=None, *, token=None):
        kwargs = self._auth(token)
        if payload is not None:
            kwargs["data"] = json.dumps(payload)
            kwargs["content_type"] = "application/json"
        return self.client.post(
            f"/api/campaigns/{campaign_id}/schedule", **kwargs,
        )


class ScheduleEndpointTest(CampaignSchedulingApiTestBase):
    def test_requires_staff_token(self):
        cases = [
            ("schedule", None),
            ("schedule", {"scheduled_at": "2026-11-05T10:00:00", "scheduled_timezone": "UTC"}),
            ("unschedule", None),
            ("send", None),
            ("test-send", {"email": "probe@example.com"}),
        ]
        for action, payload in cases:
            with self.subTest(action=action, auth="missing"):
                kwargs = {}
                if payload is not None:
                    kwargs = {
                        "data": json.dumps(payload),
                        "content_type": "application/json",
                    }
                response = self.client.post(
                    f"/api/campaigns/{self.draft.pk}/{action}", **kwargs,
                )
                self.assertEqual(response.status_code, 401)
            with self.subTest(action=action, auth="non-staff"):
                kwargs["HTTP_AUTHORIZATION"] = f"Token {self.non_staff_token.key}"
                response = self.client.post(
                    f"/api/campaigns/{self.draft.pk}/{action}", **kwargs,
                )
                self.assertEqual(response.status_code, 401)

    def test_schedule_naive_datetime_with_timezone(self):
        response = self._post(
            self.draft.pk,
            {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "Europe/Berlin"},
        )
        body = response.json()
        self.assertEqual(body["scheduled_at"], FUTURE_UTC.isoformat())
        self.assertEqual(body["scheduled_timezone"], "Europe/Berlin")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.scheduled_at, FUTURE_UTC)
        self.assertEqual(self.draft.scheduled_timezone, "Europe/Berlin")

    def test_reschedule_overwrites_fields(self):
        self._post(
            self.draft.pk,
            {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "Europe/Berlin"},
        )
        response = self._post(
            self.draft.pk,
            {"scheduled_at": RESCHEDULE_LOCAL, "scheduled_timezone": "UTC"},
        )
        self.assertEqual(response.json()["scheduled_timezone"], "UTC")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.scheduled_at, RESCHEDULE_UTC)
        self.assertEqual(self.draft.scheduled_timezone, "UTC")

    def test_schedule_unknown_campaign_404(self):
        response = self._post(
            99999,
            {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "UTC"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_campaign")

    def test_schedule_rejections(self):
        cases = [
            (
                "past_time",
                {"scheduled_at": "2020-01-01T00:00:00", "scheduled_timezone": "UTC"},
                {"scheduled_at": "Must be in the future."},
            ),
            (
                "naive_without_timezone",
                {"scheduled_at": FUTURE_LOCAL},
                {"scheduled_timezone": None},
            ),
            (
                "unknown_timezone",
                {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "Mars/Olympus"},
                {"scheduled_timezone": None},
            ),
            (
                "offset_mismatch",
                {"scheduled_at": "2026-11-05T10:00:00+02:00", "scheduled_timezone": "Europe/Berlin"},
                {"scheduled_timezone": None},
            ),
            (
                "bad_datetime",
                {"scheduled_at": "next week", "scheduled_timezone": "UTC"},
                {"scheduled_at": None},
            ),
            (
                "missing_field",
                {"scheduled_timezone": "UTC"},
                {"scheduled_at": None},
            ),
        ]
        for label, payload, expected_details in cases:
            with self.subTest(case=label):
                response = self._post(self.draft.pk, payload)
                self.assertEqual(response.status_code, 422)
                body = response.json()
                self.assertEqual(body["code"], "validation_error")
                for field in expected_details:
                    self.assertIn(field, body["details"])

    def test_schedule_non_object_body_400(self):
        response = self.client.post(
            f"/api/campaigns/{self.draft.pk}/schedule",
            data=json.dumps([1, 2]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_schedule_invalid_json_400(self):
        response = self.client.post(
            f"/api/campaigns/{self.draft.pk}/schedule",
            data="{not json",
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)

    def test_schedule_sent_campaign_409(self):
        response = self._post(
            self.sent.pk,
            {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "UTC"},
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "campaign_not_draft")
        self.assertIn("sent", body["error"])

    def test_schedule_fields_rejected_on_draft_patch(self):
        response = self.client.patch(
            f"/api/campaigns/{self.draft.pk}",
            data=json.dumps({"scheduled_at": FUTURE_LOCAL}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "read_only_field")
        self.assertEqual(body["details"]["field"], "scheduled_at")
        self.assertIn("schedule", body["details"]["hint"])

    def test_get_serializes_schedule_fields(self):
        self.draft.scheduled_at = FUTURE_UTC
        self.draft.scheduled_timezone = "Europe/Berlin"
        self.draft.save(update_fields=["scheduled_at", "scheduled_timezone"])
        response = self.client.get(
            f"/api/campaigns/{self.draft.pk}", **self._auth(),
        )
        body = response.json()
        self.assertEqual(body["scheduled_at"], FUTURE_UTC.isoformat())
        self.assertEqual(body["scheduled_timezone"], "Europe/Berlin")


class UnscheduleEndpointTest(CampaignSchedulingApiTestBase):
    def test_unschedule_clears_fields(self):
        self._post(
            self.draft.pk,
            {"scheduled_at": FUTURE_LOCAL, "scheduled_timezone": "Europe/Berlin"},
        )
        response = self.client.post(
            f"/api/campaigns/{self.draft.pk}/unschedule", **self._auth(),
        )
        self.assertIsNone(response.json()["scheduled_at"])
        self.assertIsNone(response.json()["scheduled_timezone"])
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.scheduled_at)

    def test_unschedule_not_scheduled_409(self):
        response = self.client.post(
            f"/api/campaigns/{self.draft.pk}/unschedule", **self._auth(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "campaign_not_draft")

    def test_unschedule_sent_campaign_409(self):
        self.sent.scheduled_at = FUTURE_UTC
        self.sent.save(update_fields=["scheduled_at"])
        response = self.client.post(
            f"/api/campaigns/{self.sent.pk}/unschedule", **self._auth(),
        )
        self.assertEqual(response.status_code, 409)

    def test_unschedule_unknown_campaign_404(self):
        response = self.client.post(
            "/api/campaigns/99999/unschedule", **self._auth(),
        )
        self.assertEqual(response.status_code, 404)


class SendEndpointTest(CampaignSchedulingApiTestBase):
    def test_send_claims_draft_once(self):
        self.draft.scheduled_at = FUTURE_UTC
        self.draft.scheduled_timezone = "Europe/Berlin"
        self.draft.save(update_fields=["scheduled_at", "scheduled_timezone"])
        with stub_relay_submit() as mock_async:
            response = self.client.post(
                f"/api/campaigns/{self.draft.pk}/send", **self._auth(),
            )
        body = response.json()
        self.assertTrue(body["claimed"])
        self.assertEqual(body["status"], "sending")
        self.assertEqual(
            mock_async.call_args.kwargs["external_key"], f"aishl-campaign-{self.draft.pk}",
        )
        self.assertEqual(body["relay_campaign_key"], f"aishl-campaign-{self.draft.pk}")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "sending")
        # Claiming consumes the schedule.
        self.assertIsNone(self.draft.scheduled_at)
        self.assertIsNone(self.draft.scheduled_timezone)

    def test_send_non_draft_409(self):
        with stub_relay_submit():
            response = self.client.post(
                f"/api/campaigns/{self.sent.pk}/send", **self._auth(),
            )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "campaign_not_draft")
        self.assertEqual(body["details"]["status"], "sent")

    def test_send_monitored_campaign_submits_once(self):
        monitored = EmailCampaign.objects.create(
            subject="Re-permission Send",
            body="body",
            status="draft",
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
            ),
        )
        with stub_relay_submit() as mock_async:
            response = self.client.post(
                f"/api/campaigns/{monitored.pk}/send", **self._auth(),
            )
        self.assertEqual(response.json()["status"], "sending")
        self.assertEqual(mock_async.call_count, 1)

    def test_send_unknown_campaign_404(self):
        response = self.client.post(
            "/api/campaigns/99999/send", **self._auth(),
        )
        self.assertEqual(response.status_code, 404)


class TestSendEndpointTest(CampaignSchedulingApiTestBase):
    def test_test_send_happy_path(self):
        with patch(
            "api.views.campaign_scheduling.send_test_campaign_delivery",
            return_value="relay-msg-id",
        ) as mock_send:
            response = self.client.post(
                f"/api/campaigns/{self.draft.pk}/test-send",
                data=json.dumps({"email": "Probe@Example.com"}),
                content_type="application/json",
                **self._auth(),
            )
        body = response.json()
        self.assertEqual(body["relay_message_id"], "relay-msg-id")
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(mock_send.call_args.kwargs["campaign_id"], self.draft.pk)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "draft")

    def test_test_send_rejections(self):
        cases = [
            ("missing_email", {}),
            ("empty_email", {"email": "   "}),
            ("invalid_email", {"email": "not-an-email"}),
            ("non_string_email", {"email": 42}),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                response = self.client.post(
                    f"/api/campaigns/{self.draft.pk}/test-send",
                    data=json.dumps(payload),
                    content_type="application/json",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("email", response.json()["details"])

    def test_test_send_policy_failure_502(self):
        # A controlled recipient for a re-permission campaign that is
        # already verified trips CampaignTestRecipientError -> 502.
        from email_app.services.campaign_test_send import CampaignTestRecipientError

        monitored = EmailCampaign.objects.create(
            subject="Re-permission", body="body", status="draft",
            audience_verification=EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY,
        )
        with patch(
            "api.views.campaign_scheduling.send_campaign_test_recipient",
            side_effect=CampaignTestRecipientError("not eligible"),
        ):
            response = self.client.post(
                f"/api/campaigns/{monitored.pk}/test-send",
                data=json.dumps({"email": "probe@example.com"}),
                content_type="application/json",
                **self._auth(),
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "campaign_test_send_failed")

    def test_test_send_transport_failure_502(self):
        with self.assertLogs("api.views.campaign_scheduling", level="ERROR"):
            with patch(
                "api.views.campaign_scheduling.send_campaign_test_recipient",
                side_effect=RuntimeError("relay exploded"),
            ):
                response = self.client.post(
                    f"/api/campaigns/{self.draft.pk}/test-send",
                    data=json.dumps({"email": "probe@example.com"}),
                    content_type="application/json",
                    **self._auth(),
                )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("relay exploded", response.json()["error"])

    def test_test_send_unknown_campaign_404(self):
        response = self.client.post(
            "/api/campaigns/99999/test-send",
            data=json.dumps({"email": "probe@example.com"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
