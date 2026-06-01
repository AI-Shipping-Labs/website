"""Tests for the aggregate SES events list endpoint (issue #829).

``GET /api/ses-events`` lists ``SesEvent`` rows across ALL recipients --
including events whose ``user`` FK is ``None`` -- so an operator can
reconcile a campaign's bounces by window / campaign / recipient.

These tests live in a dedicated module (not ``test_ses_events.py``) to
keep the GET-list coverage separate from the webhook coverage. The
webhook-still-works scenario reuses the ``test_ses_events`` payload
builder + validator-patch pattern.
"""

import json
from datetime import timedelta
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from api.tests.test_ses_events import VALIDATOR_PATH, _bounce_payload
from email_app.models import EmailCampaign, EmailLog, SesEvent

User = get_user_model()

URL = "/api/ses-events"


def _set_received_at(event, when):
    # ``received_at`` is ``auto_now_add`` so it cannot be set on create;
    # stamp it via a queryset update to make window tests deterministic.
    SesEvent.objects.filter(pk=event.pk).update(received_at=when)


class SesEventsListTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-ses-list@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-ses-list@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="ses-list")
        cls.non_staff_token = Token(
            key="non-staff-ses-list-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        # Window: [start, end]. "now" sits inside it; the OUT-of-window
        # bounce is older than start.
        cls.now = timezone.now()
        cls.window_start = cls.now - timedelta(hours=1)
        cls.window_end = cls.now + timedelta(hours=1)
        cls.out_of_window = cls.now - timedelta(days=2)

        # A user whose address WILL match a SesEvent.recipient_email.
        cls.bounced_user = User.objects.create_user(
            email="hardbounce@acme.example", password="pw",
        )

        cls.campaign = EmailCampaign.objects.create(
            subject="Reconcile me", body="Hi", target_min_level=0,
        )
        cls.campaign_log = EmailLog.objects.create(
            user=cls.bounced_user,
            email_type="campaign",
            campaign=cls.campaign,
            ses_message_id="ses-campaign-1",
        )

        # 1. Permanent bounce, recipient WITH a User, in window, on campaign.
        cls.ev_perm = SesEvent.objects.create(
            message_id="ev-perm",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={},
            recipient_email="hardbounce@acme.example",
            user=cls.bounced_user,
            email_log=cls.campaign_log,
            bounce_type="Permanent",
            diagnostic_code="smtp; 550 user unknown",
        )
        _set_received_at(cls.ev_perm, cls.now)

        # 2. Transient bounce, recipient with user=None, in window, on campaign.
        cls.ev_transient = SesEvent.objects.create(
            message_id="ev-transient",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
            raw_payload={},
            recipient_email="lead@newsletter.example",
            user=None,
            email_log=cls.campaign_log,
            bounce_type="Transient",
        )
        _set_received_at(cls.ev_transient, cls.now - timedelta(minutes=10))

        # 3. Bounce (other), in window, no email_log.
        cls.ev_other = SesEvent.objects.create(
            message_id="ev-other",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_OTHER,
            raw_payload={},
            recipient_email="weird@acme.example",
            user=None,
        )
        _set_received_at(cls.ev_other, cls.now - timedelta(minutes=20))

        # 4. Complaint, in window.
        cls.ev_complaint = SesEvent.objects.create(
            message_id="ev-complaint",
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            raw_payload={},
            recipient_email="angry@acme.example",
            user=None,
        )
        _set_received_at(cls.ev_complaint, cls.now - timedelta(minutes=30))

        # 5. Delivery, in window.
        cls.ev_delivery = SesEvent.objects.create(
            message_id="ev-delivery",
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            raw_payload={},
            recipient_email="ok@acme.example",
            user=None,
        )
        _set_received_at(cls.ev_delivery, cls.now - timedelta(minutes=40))

        # 6. Permanent bounce OUTSIDE the window (older).
        cls.ev_old_bounce = SesEvent.objects.create(
            message_id="ev-old-bounce",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={},
            recipient_email="old@other.example",
            user=None,
        )
        _set_received_at(cls.ev_old_bounce, cls.out_of_window)

        cls.total_events = 6
        # In-window bounce rows (perm + transient + other).
        cls.in_window_bounce_ids = {"ev-perm", "ev-transient", "ev-other"}

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _get(self, query="", *, token=None):
        return self.client.get(URL + query, **self._auth(token))

    def _iso(self, dt):
        # URL-encode so the ``+HH:MM`` offset is not decoded to a space
        # in the query string.
        return quote(dt.isoformat())


class SesEventsListBasicTest(SesEventsListTestBase):
    def test_lists_all_recipients_including_user_none(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body.keys()), {"ses_events", "count", "limit", "offset"})
        self.assertEqual(body["count"], self.total_events)
        message_ids = {row["message_id"] for row in body["ses_events"]}
        # The user=None transient-bounce recipient is present.
        self.assertIn("ev-transient", message_ids)
        # And it really has no User attached.
        transient = next(
            r for r in body["ses_events"] if r["message_id"] == "ev-transient"
        )
        self.assertEqual(transient["event_type"], "bounce_transient")

    def test_default_ordering_is_newest_first(self):
        body = self._get().json()
        received = [row["received_at"] for row in body["ses_events"]]
        self.assertEqual(received, sorted(received, reverse=True))

    def test_serializer_exposes_bounce_type_and_diagnostic(self):
        body = self._get().json()
        perm = next(r for r in body["ses_events"] if r["message_id"] == "ev-perm")
        self.assertEqual(perm["bounce_type"], "Permanent")
        self.assertEqual(perm["diagnostic_code"], "smtp; 550 user unknown")


class SesEventsListWindowFilterTest(SesEventsListTestBase):
    def test_bounce_alias_with_window_returns_only_in_window_bounces(self):
        query = (
            f"?type=bounce&since={self._iso(self.window_start)}"
            f"&until={self._iso(self.window_end)}"
        )
        body = self._get(query).json()
        ids = {row["message_id"] for row in body["ses_events"]}
        self.assertEqual(ids, self.in_window_bounce_ids)
        self.assertEqual(body["count"], len(self.in_window_bounce_ids))
        # complaint / delivery / out-of-window bounce excluded.
        self.assertNotIn("ev-complaint", ids)
        self.assertNotIn("ev-delivery", ids)
        self.assertNotIn("ev-old-bounce", ids)
        types = {row["event_type"] for row in body["ses_events"]}
        self.assertEqual(
            types, {"bounce_permanent", "bounce_transient", "bounce_other"},
        )

    def test_since_excludes_out_of_window_bounce(self):
        body = self._get(f"?since={self._iso(self.window_start)}").json()
        ids = {row["message_id"] for row in body["ses_events"]}
        self.assertNotIn("ev-old-bounce", ids)
        self.assertEqual(body["count"], self.total_events - 1)

    def test_until_excludes_newer_rows(self):
        # until = just after the oldest in-window row's neighbour; only the
        # out-of-window bounce is <= a point before the window starts.
        cutoff = self.window_start
        body = self._get(f"?until={self._iso(cutoff)}").json()
        ids = {row["message_id"] for row in body["ses_events"]}
        self.assertEqual(ids, {"ev-old-bounce"})
        self.assertEqual(body["count"], 1)

    def test_exact_event_type_filter(self):
        body = self._get("?type=bounce_permanent").json()
        ids = {row["message_id"] for row in body["ses_events"]}
        self.assertEqual(ids, {"ev-perm", "ev-old-bounce"})
        self.assertEqual(body["count"], 2)


class SesEventsListEmailLogFilterTest(SesEventsListTestBase):
    def test_email_log_filter_returns_only_campaign_events(self):
        body = self._get(f"?email_log={self.campaign_log.id}").json()
        ids = {row["message_id"] for row in body["ses_events"]}
        self.assertEqual(ids, {"ev-perm", "ev-transient"})
        for row in body["ses_events"]:
            self.assertEqual(row["email_log_id"], self.campaign_log.id)
        self.assertEqual(body["count"], 2)


class SesEventsListRecipientFilterTest(SesEventsListTestBase):
    def test_recipient_substring_case_insensitive(self):
        body = self._get("?recipient=ACME.example").json()
        ids = {row["message_id"] for row in body["ses_events"]}
        # All acme.example recipients, none of newsletter/other.
        self.assertEqual(
            ids, {"ev-perm", "ev-other", "ev-complaint", "ev-delivery"},
        )
        self.assertNotIn("ev-transient", ids)
        self.assertNotIn("ev-old-bounce", ids)


class SesEventsListPaginationTest(SesEventsListTestBase):
    def test_count_is_full_match_set_not_page_length(self):
        body = self._get("?type=bounce&limit=1").json()
        self.assertEqual(len(body["ses_events"]), 1)
        self.assertEqual(body["limit"], 1)
        # 3 in-window bounces + 1 out-of-window = 4 bounce rows total.
        self.assertEqual(body["count"], 4)
        self.assertGreater(body["count"], len(body["ses_events"]))

    def test_offset_pages_with_no_overlap(self):
        page1 = self._get("?type=bounce&limit=1&offset=0").json()
        page2 = self._get("?type=bounce&limit=1&offset=1").json()
        id1 = page1["ses_events"][0]["message_id"]
        id2 = page2["ses_events"][0]["message_id"]
        self.assertNotEqual(id1, id2)
        # Newest-first across pages.
        ts1 = page1["ses_events"][0]["received_at"]
        ts2 = page2["ses_events"][0]["received_at"]
        self.assertGreater(ts1, ts2)
        self.assertEqual(page1["offset"], 0)
        self.assertEqual(page2["offset"], 1)


class SesEventsListValidationTest(SesEventsListTestBase):
    def test_invalid_type_rejected_with_allowed_list(self):
        resp = self._get("?type=not-real")
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "type")
        self.assertIn("bounce_permanent", body["details"]["allowed"])

    def test_bounce_alias_is_accepted(self):
        self.assertEqual(self._get("?type=bounce").status_code, 200)

    def test_invalid_since_rejected(self):
        resp = self._get("?since=not-a-date")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "since")

    def test_invalid_until_rejected(self):
        resp = self._get("?until=not-a-date")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "until")

    def test_invalid_limit_rejected(self):
        resp = self._get("?limit=0")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "limit")

    def test_negative_offset_rejected(self):
        resp = self._get("?offset=-1")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "offset")

    def test_non_integer_email_log_rejected(self):
        resp = self._get("?email_log=abc")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["details"]["field"], "email_log")


class SesEventsListAuthTest(SesEventsListTestBase):
    def test_unauthenticated_returns_401_not_redirect(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Authentication token required")

    def test_non_staff_token_returns_401(self):
        resp = self._get(token=self.non_staff_token)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "Invalid token")

    def test_invalid_token_returns_401(self):
        resp = self.client.get(URL, HTTP_AUTHORIZATION="Token does-not-exist")
        self.assertEqual(resp.status_code, 401)


class SesEventsListWebhookCoexistenceTest(SesEventsListTestBase):
    """The GET dispatcher must not break the SNS POST webhook path."""

    def test_post_webhook_still_processes_a_bounce(self):
        payload = _bounce_payload("ev-webhook-coexist", "newbounce@acme.example")
        with mock.patch(VALIDATOR_PATH, return_value=True):
            resp = self.client.post(
                URL, data=json.dumps(payload), content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        self.assertTrue(
            SesEvent.objects.filter(message_id="ev-webhook-coexist").exists()
        )

    def test_post_webhook_without_signature_still_returns_403(self):
        payload = _bounce_payload("ev-webhook-bad", "x@acme.example")
        with mock.patch(VALIDATOR_PATH, return_value=False):
            resp = self.client.post(
                URL, data=json.dumps(payload), content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
