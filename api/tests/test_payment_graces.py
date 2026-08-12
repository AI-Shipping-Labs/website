from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import TierOverride, Token, User
from payments.models import MonthlyPaymentGrace as Grace
from payments.models import MonthlyPaymentGraceDelivery as Delivery
from payments.models import Tier


class PaymentGraceApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="grace-admin@test.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name="grace-api")
        cls.nonstaff = User.objects.create_user(email="plain@test.com")
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.member = User.objects.create_user(
            email="grace-member@test.com", tier=cls.main,
            stripe_customer_id="cus_api_grace", subscription_id="sub_api_grace",
        )
        now = timezone.now()
        cls.grace = Grace.objects.create(
            user=cls.member, base_tier_at_start=cls.main,
            stripe_customer_id="cus_api_grace",
            stripe_subscription_id="sub_api_grace",
            stripe_invoice_id="in_api_grace", livemode=False,
            source=Grace.SOURCE_WEBHOOK, interval="month", interval_count=1,
            grace_started_at=now, grace_expires_at=now + timedelta(hours=168),
        )
        Delivery.objects.create(
            grace=cls.grace, kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=cls.member.email, status=Delivery.STATUS_SENT,
            sent_at=now,
        )

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_authentication_happens_before_operational_read(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/api/payments/payment-graces")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(any("monthlypaymentgrace" in q["sql"].lower() for q in captured))

    def test_list_filters_and_exposes_base_effective_delivery_truth(self):
        response = self.client.get(
            "/api/payments/payment-graces?status=active&tier=main&interval=month&delivery_status=sent",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        row = response.json()["payment_graces"][0]
        self.assertEqual(row["base_tier"], "main")
        self.assertEqual(row["effective_tier"], "main")
        self.assertEqual(row["deliveries"][0]["status"], "sent")
        self.assertEqual(row["stripe_invoice_id"], "in_api_grace")

    def test_detail_is_read_only_and_has_no_mutation_actions(self):
        response = self.client.get(
            f"/api/payments/payment-graces/{self.grace.pk}", **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["payment_grace"]
        self.assertNotIn("expire", payload)
        self.assertNotIn("extend", payload)
        self.assertNotIn("mark_paid", payload)
        self.assertEqual(
            self.client.post(
                f"/api/payments/payment-graces/{self.grace.pk}", **self.auth(),
            ).status_code,
            405,
        )

    def test_invalid_filters_are_stable_422(self):
        response = self.client.get(
            "/api/payments/payment-graces?status=bogus", **self.auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["details"]["field"], "status")

    def test_nonstaff_token_is_401(self):
        # Token rows cannot normally be created for non-staff; use the
        # compatibility bulk path to prove the decorator's fail-closed gate.
        token = Token(key="plain-grace-token", user=self.nonstaff, name="legacy")
        Token.objects.bulk_create([token])
        response = self.client.get(
            "/api/payments/payment-graces",
            HTTP_AUTHORIZATION="Token plain-grace-token",
        )
        self.assertEqual(response.status_code, 401)

    def test_api_reports_strongest_active_override_not_newest(self):
        premium = Tier.objects.get(slug="premium")
        basic = Tier.objects.get(slug="basic")
        TierOverride.objects.create(
            user=self.member, override_tier=premium,
            expires_at=timezone.now() + timedelta(days=10),
            source="staff:premium",
        )
        TierOverride.objects.create(
            user=self.member, override_tier=basic,
            expires_at=timezone.now() + timedelta(days=20),
            source="maven:newer-basic",
        )
        response = self.client.get(
            f"/api/payments/payment-graces/{self.grace.pk}", **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["payment_grace"]["effective_tier"], "premium",
        )
