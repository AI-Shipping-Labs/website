import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from accounts.models import Token
from booking.models import (
    STATUS_APPROVED,
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    STATUS_DECLINED,
    STATUS_PENDING,
    Booking,
    BookingHost,
    CallRequest,
)
from booking.services.allowance import get_member_allowance
from questionnaires.models import Questionnaire, Response

User = get_user_model()


class ApiCallRequestsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="api-staff@test.com",
            password="pw",
            first_name="Staff",
            last_name="Admin",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="api-member@test.com",
            password="pw",
            first_name="Member",
            last_name="User",
        )
        cls.member2 = User.objects.create_user(
            email="api-member2@test.com",
            password="pw",
            first_name="MemberTwo",
            last_name="User",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="api-test")
        cls.member_token = Token(
            key="non-staff-member-api-token",
            user=cls.member,
            name="member-api-test",
        )
        Token.objects.bulk_create([cls.member_token])

        cls.q, _ = Questionnaire.objects.get_or_create(
            slug="generic-onboarding",
            defaults={"title": "Generic Onboarding", "purpose": "onboarding"},
        )
        Response.objects.create(
            questionnaire=cls.q,
            respondent=cls.member,
            status="submitted",
        )
        cls.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@datatalks.club",
                "timezone": "Europe/Berlin",
                "is_active": True,
            },
        )

    def _auth(self, token=None):
        tok = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {tok.key}"}

    def test_auth_gating(self):
        # Unauthenticated receives 401
        resp_no_auth = self.client.get("/api/booking/call-requests")
        self.assertEqual(resp_no_auth.status_code, 401)

        # Invalid token receives 401
        resp_invalid = self.client.get(
            "/api/booking/call-requests",
            HTTP_AUTHORIZATION="Token invalid-token-key",
        )
        self.assertEqual(resp_invalid.status_code, 401)

        # Non-staff member token receives 403 or 401
        resp_member = self.client.get(
            "/api/booking/call-requests",
            **self._auth(self.member_token),
        )
        self.assertIn(resp_member.status_code, [401, 403])

    def test_api_call_requests_list_and_filter(self):
        r1 = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Topic One",
            message="Message One",
            status=STATUS_PENDING,
        )
        r2 = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Topic Two",
            message="Message Two",
            status=STATUS_APPROVED,
        )
        r3 = CallRequest.objects.create(
            member=self.member2,
            host=self.host,
            topic="Topic Three",
            message="Message Three",
            status=STATUS_DECLINED,
        )

        # List all
        resp_all = self.client.get("/api/booking/call-requests", **self._auth())
        data = resp_all.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["call_requests"]), 3)

        # Filter by status=pending
        resp_pending = self.client.get("/api/booking/call-requests?status=pending", **self._auth())
        data_p = resp_pending.json()
        self.assertEqual(data_p["count"], 1)
        self.assertEqual(data_p["call_requests"][0]["id"], r1.id)

        # Filter by status=approved
        resp_app = self.client.get("/api/booking/call-requests?status=approved", **self._auth())
        data_a = resp_app.json()
        self.assertEqual(data_a["count"], 1)
        self.assertEqual(data_a["call_requests"][0]["id"], r2.id)

        # Filter by member id
        resp_m1 = self.client.get(f"/api/booking/call-requests?member={self.member.id}", **self._auth())
        data_m1 = resp_m1.json()
        self.assertEqual(data_m1["count"], 2)

        # Filter by member email
        resp_m2 = self.client.get(f"/api/booking/call-requests?member={self.member2.email}", **self._auth())
        data_m2 = resp_m2.json()
        self.assertEqual(data_m2["count"], 1)
        self.assertEqual(data_m2["call_requests"][0]["id"], r3.id)

    def test_api_call_request_detail(self):
        r = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Architecture Review",
            message="Detailed design inquiry.",
            status=STATUS_PENDING,
        )

        resp = self.client.get(f"/api/booking/call-requests/{r.id}", **self._auth())
        data = resp.json()
        self.assertEqual(data["id"], r.id)
        self.assertEqual(data["topic"], "Architecture Review")
        self.assertEqual(data["member_email"], self.member.email)
        self.assertEqual(data["status"], STATUS_PENDING)

        resp_404 = self.client.get("/api/booking/call-requests/999999", **self._auth())
        self.assertEqual(resp_404.status_code, 404)

    def test_api_call_request_approve_and_decline(self):
        r_app = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Second call please",
            message="Follow up call requested.",
            status=STATUS_PENDING,
        )

        # Approve
        resp_app = self.client.post(
            f"/api/booking/call-requests/{r_app.id}/approve",
            data=json.dumps({"review_notes": "Approved via API"}),
            content_type="application/json",
            **self._auth(),
        )
        data_app = resp_app.json()
        self.assertEqual(data_app["status"], STATUS_APPROVED)
        self.assertEqual(data_app["review_notes"], "Approved via API")
        self.assertEqual(data_app["reviewed_by_id"], self.staff.id)

        r_app.refresh_from_db()
        self.assertEqual(r_app.status, STATUS_APPROVED)
        self.assertEqual(r_app.reviewed_by, self.staff)

        # Allowance increased
        allowance = get_member_allowance(self.member)
        self.assertEqual(allowance.approved_requests_count, 1)
        self.assertEqual(allowance.total_allowance, 2)

        # Decline
        r_dec = CallRequest.objects.create(
            member=self.member2,
            host=self.host,
            topic="Not eligible",
            message="Another question.",
            status=STATUS_PENDING,
        )
        resp_dec = self.client.post(
            f"/api/booking/call-requests/{r_dec.id}/decline",
            data=json.dumps({"review_notes": "Declined via API"}),
            content_type="application/json",
            **self._auth(),
        )
        data_dec = resp_dec.json()
        self.assertEqual(data_dec["status"], STATUS_DECLINED)
        self.assertEqual(data_dec["review_notes"], "Declined via API")

        r_dec.refresh_from_db()
        self.assertEqual(r_dec.status, STATUS_DECLINED)
        self.assertEqual(r_dec.reviewed_by, self.staff)

    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    @patch("booking.services.booking_service.delete_calendar_event")
    def test_api_bookings_list_and_cancel_without_member_email(
        self, mock_del_cal, mock_del_zoom
    ):
        mail.outbox = []
        booking = Booking.objects.create(
            host=self.host,
            member=self.member,
            invitee_name="Member User",
            invitee_email=self.member.email,
            start_datetime=datetime(2026, 10, 29, 16, 30, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc),
            status=STATUS_CONFIRMED,
            zoom_meeting_id="zm-cancel-1",
            google_event_id="gcal-cancel-1",
        )
        resp_list = self.client.get("/api/booking/bookings?status=confirmed", **self._auth())
        self.assertEqual(resp_list.json()["count"], 1)

        resp_cancel = self.client.post(
            f"/api/booking/bookings/{booking.id}/cancel",
            **self._auth(),
        )
        payload = resp_cancel.json()
        self.assertEqual(payload["previous_status"], STATUS_CONFIRMED)
        self.assertEqual(payload["status"], STATUS_CANCELED)
        self.assertFalse(payload["email_sent"])
        mock_del_zoom.assert_called_once_with("zm-cancel-1")
        mock_del_cal.assert_called_once_with("gcal-cancel-1")
        self.assertEqual(len(mail.outbox), 0)
