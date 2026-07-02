from django.test import TestCase

from accounts.models import User
from community.models import CommunityAuditLog
from payments.models import PaymentAccountMismatch


class StudioPaymentMismatchTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="studio-mismatch-staff@test.com",
            password="testpass123",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="studio-paid@test.com",
            password="testpass123",
        )
        cls.candidate = User.objects.create_user(
            email="studio-candidate@test.com",
            password="testpass123",
        )
        cls.mismatch = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_studio_1105",
            stripe_customer_id="cus_studio_1105",
            stripe_subscription_id="sub_studio_1105",
            stripe_email=cls.candidate.email,
            paid_user=cls.member,
            candidate_user=cls.candidate,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
        )

    def setUp(self):
        self.client.login(
            email="studio-mismatch-staff@test.com",
            password="testpass123",
        )

    def test_queue_lists_open_mismatch_with_links(self):
        response = self.client.get("/studio/users/payment-mismatches/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cs_studio_1105")
        self.assertContains(response, "studio-paid@test.com")
        self.assertContains(response, "studio-candidate@test.com")
        self.assertContains(response, "Merge preview")
        self.assertContains(response, "/studio/users/merge/")

    def test_user_detail_shows_open_warning_and_hides_after_terminal(self):
        paid_response = self.client.get(f"/studio/users/{self.member.pk}/")
        candidate_response = self.client.get(f"/studio/users/{self.candidate.pk}/")

        self.assertContains(paid_response, 'data-testid="payment-mismatch-warning"')
        self.assertContains(
            candidate_response,
            'data-testid="payment-mismatch-warning"',
        )

        self.mismatch.mark_terminal(
            status=PaymentAccountMismatch.STATUS_IGNORED,
            note="Not the same person.",
            actor=self.staff,
        )
        self.mismatch.save()

        resolved_response = self.client.get(f"/studio/users/{self.member.pk}/")
        self.assertNotContains(
            resolved_response,
            'data-testid="payment-mismatch-warning"',
        )

    def test_mark_resolved_requires_note_and_records_actor(self):
        response = self.client.post(
            f"/studio/users/payment-mismatches/{self.mismatch.pk}/resolve",
            {"resolution_note": ""},
        )

        self.assertEqual(response.status_code, 302)
        self.mismatch.refresh_from_db()
        self.assertEqual(self.mismatch.status, PaymentAccountMismatch.STATUS_OPEN)

        response = self.client.post(
            f"/studio/users/payment-mismatches/{self.mismatch.pk}/resolve",
            {"resolution_note": "Merged through preview."},
        )

        self.assertEqual(response.status_code, 302)
        self.mismatch.refresh_from_db()
        self.assertEqual(self.mismatch.status, PaymentAccountMismatch.STATUS_RESOLVED)
        self.assertEqual(self.mismatch.resolved_by, self.staff)
        self.assertEqual(self.mismatch.resolution_note, "Merged through preview.")
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=self.member,
                action="payment_mismatch_updated",
            ).count(),
            1,
        )

    def test_non_staff_cannot_access_queue(self):
        self.client.logout()
        User.objects.create_user(
            email="studio-mismatch-plain@test.com",
            password="testpass123",
            is_staff=False,
        )
        self.client.login(
            email="studio-mismatch-plain@test.com",
            password="testpass123",
        )

        response = self.client.get("/studio/users/payment-mismatches/")

        self.assertEqual(response.status_code, 403)
