from django.contrib.auth import get_user_model
from django.test import TestCase

from booking.models import (
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_PENDING,
    BookingHost,
    CallRequest,
)
from booking.services.allowance import get_member_allowance
from crm.models import CRMRecord
from questionnaires.models import Questionnaire, Response
from tests.fixtures import StaffUserMixin

User = get_user_model()


class StudioCallRequestsTests(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email="member@test.com",
            password="testpass",
            first_name="Alice",
            last_name="Member",
        )
        cls.crm_record = CRMRecord.objects.create(user=cls.member)
        cls.regular_user = User.objects.create_user(
            email="regular@test.com",
            password="testpass",
            first_name="Bob",
            last_name="Regular",
        )
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

    def test_studio_call_requests_list_requires_staff(self):
        # Anonymous redirects
        resp_anon = self.client.get("/studio/call-requests/")
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn("/accounts/login/", resp_anon.url)

        # Regular non-staff user gets 403 or redirect
        self.client.force_login(self.regular_user)
        resp_non_staff = self.client.get("/studio/call-requests/")
        self.assertIn(resp_non_staff.status_code, [403, 302])

        # Staff user gets 200
        self.client.force_login(self.staff)
        resp_staff = self.client.get("/studio/call-requests/")
        self.assertContains(resp_staff, "Call requests")

    def test_studio_call_requests_list_filters_by_status(self):
        self.client.force_login(self.staff)

        r_pending = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Pending Strategy",
            message="Please review pending topic",
            status=STATUS_PENDING,
        )
        r_approved = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Approved Strategy",
            message="Please review approved topic",
            status=STATUS_APPROVED,
        )
        r_declined = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Declined Strategy",
            message="Please review declined topic",
            status=STATUS_DECLINED,
        )

        # All
        resp_all = self.client.get("/studio/call-requests/")
        self.assertContains(resp_all, r_pending.topic)
        self.assertContains(resp_all, r_approved.topic)
        self.assertContains(resp_all, r_declined.topic)

        # Pending filter
        resp_pending = self.client.get("/studio/call-requests/?status=pending")
        self.assertContains(resp_pending, r_pending.topic)
        self.assertNotContains(resp_pending, r_approved.topic)
        self.assertNotContains(resp_pending, r_declined.topic)

        # Approved filter
        resp_app = self.client.get("/studio/call-requests/?status=approved")
        self.assertNotContains(resp_app, r_pending.topic)
        self.assertContains(resp_app, r_approved.topic)
        self.assertNotContains(resp_app, r_declined.topic)

        # Declined filter
        resp_dec = self.client.get("/studio/call-requests/?status=declined")
        self.assertNotContains(resp_dec, r_pending.topic)
        self.assertNotContains(resp_dec, r_approved.topic)
        self.assertContains(resp_dec, r_declined.topic)

    def test_studio_call_requests_detail_view(self):
        self.client.force_login(self.staff)
        r = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Deep Learning Architecture",
            message="Detailed inquiry about transformer scaling.",
            status=STATUS_PENDING,
        )

        resp = self.client.get(f"/studio/call-requests/{r.pk}/")
        self.assertContains(resp, "Deep Learning Architecture")
        self.assertContains(resp, "Detailed inquiry about transformer scaling.")
        self.assertContains(resp, f"/studio/crm/{self.crm_record.pk}/")
        self.assertContains(resp, f"/studio/call-requests/{r.pk}/approve/")
        self.assertContains(resp, f"/studio/call-requests/{r.pk}/decline/")

    def test_studio_approve_grants_plus_one_allowance(self):
        self.client.force_login(self.staff)
        r = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Need second call",
            message="Great progress, need follow up.",
            status=STATUS_PENDING,
        )

        # Initial allowance: 1 base, 0 approved
        allowance_before = get_member_allowance(self.member)
        self.assertEqual(allowance_before.total_allowance, 1)

        resp = self.client.post(
            f"/studio/call-requests/{r.pk}/approve/",
            {"review_notes": "Great project, approved!"},
        )
        self.assertRedirects(resp, f"/studio/call-requests/{r.pk}/")

        r.refresh_from_db()
        self.assertEqual(r.status, STATUS_APPROVED)
        self.assertEqual(r.reviewed_by, self.staff)
        self.assertEqual(r.review_notes, "Great project, approved!")
        self.assertIsNotNone(r.reviewed_at)

        # Allowance after approval: 1 base + 1 approved = 2
        allowance_after = get_member_allowance(self.member)
        self.assertEqual(allowance_after.approved_requests_count, 1)
        self.assertEqual(allowance_after.total_allowance, 2)
        self.assertEqual(allowance_after.remaining_allowance, 2)

    def test_studio_decline_records_notes_without_allowance(self):
        self.client.force_login(self.staff)
        r = CallRequest.objects.create(
            member=self.member,
            host=self.host,
            topic="Off-topic question",
            message="Irrelevant inquiry.",
            status=STATUS_PENDING,
        )

        resp = self.client.post(
            f"/studio/call-requests/{r.pk}/decline/",
            {"review_notes": "Please ask this in Slack #questions instead."},
        )
        self.assertRedirects(resp, f"/studio/call-requests/{r.pk}/")

        r.refresh_from_db()
        self.assertEqual(r.status, STATUS_DECLINED)
        self.assertEqual(r.reviewed_by, self.staff)
        self.assertEqual(r.review_notes, "Please ask this in Slack #questions instead.")
        self.assertIsNotNone(r.reviewed_at)

        allowance_after = get_member_allowance(self.member)
        self.assertEqual(allowance_after.approved_requests_count, 0)
        self.assertEqual(allowance_after.total_allowance, 1)
        self.assertTrue(allowance_after.has_declined_request)
