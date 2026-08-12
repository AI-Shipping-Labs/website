from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride, User
from payments.models import MonthlyPaymentGrace as Grace
from payments.models import MonthlyPaymentGraceDelivery as Delivery
from payments.models import Tier


class PaymentGraceStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="grace-staff@test.com", password="x", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="grace-studio-member@test.com",
            tier=Tier.objects.get(slug="main"),
            stripe_customer_id="cus_studio_grace",
            subscription_id="sub_studio_grace",
        )
        now = timezone.now()
        cls.grace = Grace.objects.create(
            user=cls.member, base_tier_at_start=cls.member.tier,
            stripe_customer_id=cls.member.stripe_customer_id,
            stripe_subscription_id=cls.member.subscription_id,
            stripe_invoice_id="in_studio_grace", livemode=False,
            source=Grace.SOURCE_WEBHOOK, interval="month", interval_count=1,
            grace_started_at=now, grace_expires_at=now + timedelta(hours=168),
        )
        Delivery.objects.create(
            grace=cls.grace, kind=Delivery.KIND_FAILURE_MEMBER,
            recipient=cls.member.email, status=Delivery.STATUS_SENT,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_reconciliation_payment_grace_filter_shows_operator_truth(self):
        response = self.client.get(
            reverse("studio_subscription_reconciliation"),
            {"filter": "payment_grace"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "in_studio_grace")
        self.assertContains(response, "Base: main")
        self.assertContains(response, "Effective: main")
        self.assertContains(response, "Initial member failure:")
        self.assertContains(response, "Sent")
        self.assertNotContains(response, "Expire now")
        self.assertNotContains(response, "Extend grace")

    def test_member_detail_shows_read_only_grace_summary(self):
        response = self.client.get(reverse("studio_user_detail", args=[self.member.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-payment-grace-section"')
        self.assertContains(response, "in_studio_grace")
        self.assertContains(response, "Effective tier")
        self.assertContains(response, "Audit activity")

    def test_nonstaff_cannot_open_report(self):
        plain = User.objects.create_user(email="grace-nonstaff@test.com", password="x")
        self.client.force_login(plain)
        response = self.client.get(
            reverse("studio_subscription_reconciliation"),
            {"filter": "payment_grace"},
        )
        self.assertIn(response.status_code, {302, 403})

    def test_report_and_member_detail_use_strongest_override_and_owned_badges(self):
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
        report = self.client.get(
            reverse("studio_subscription_reconciliation"),
            {"filter": "payment_grace"},
        )
        detail = self.client.get(
            reverse("studio_user_detail", args=[self.member.pk]),
        )
        self.assertContains(report, "Effective: premium")
        self.assertContains(detail, "Effective tier")
        self.assertContains(detail, ">premium<", html=False)
        self.assertContains(report, "studio-status-badge")
        self.assertContains(detail, "studio-status-badge")

    def test_changed_grace_links_have_focus_rings_and_statuses_use_owner(self):
        report_source = Path(
            settings.BASE_DIR,
            "templates/studio/payments/subscription_reconciliation.html",
        ).read_text()
        detail_source = Path(
            settings.BASE_DIR,
            "templates/studio/users/detail.html",
        ).read_text()
        for source, marker in [
            (report_source, "studio_user_detail' grace.user_id"),
            (report_source, 'href="{{ row.invoice_url }}"'),
            (report_source, 'href="{{ row.subscription_url }}"'),
            (detail_source, "?filter=payment_grace"),
            (detail_source, "/invoices/{{ grace.stripe_invoice_id }}"),
            (detail_source, "/subscriptions/{{ grace.stripe_subscription_id }}"),
            (detail_source, 'href="#payment-grace-audit"'),
        ]:
            anchor = source[source.index(marker):].split("</a>", 1)[0]
            self.assertIn("focus-visible:ring-2", anchor, marker)
        self.assertIn("studio_status_badge grace.status", report_source)
        self.assertIn("studio_status_badge delivery.status", report_source)
        self.assertIn("studio_status_badge grace.status", detail_source)
        self.assertIn("studio_status_badge delivery.status", detail_source)
