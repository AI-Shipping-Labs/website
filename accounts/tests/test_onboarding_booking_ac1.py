"""Unit tests for onboarding completion screen founder booking CTAs (#2006, AC1)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.tests.test_onboarding_booking_ctas import (
    _basic_tier,
    _submit_onboarding,
)
from booking.models import BookingHost
from community.models import CallHost
from tests.fixtures import set_membership

User = get_user_model()


class OnboardingFounderBookingAC1Test(TestCase):
    """AC1: Onboarding completion links to native booking when host active, else fallback."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="ac1_member@test.com", password="pw"
        )
        set_membership(cls.member, tier=_basic_tier())

    def setUp(self):
        BookingHost.objects.filter(slug__in=["valeria", "alexey"]).update(
            is_active=False
        )
        self.client.force_login(self.member)
        _submit_onboarding(self.client)

    def test_native_alexey_host_links_to_native_booking_url(self):
        BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={"is_active": True, "name": "Alexey Grigorev"},
        )
        CallHost.objects.filter(slug="alexey").update(
            is_active=True,
            booking_url="https://calendly.com/dtc-alexey/ai-shipping-labs-call",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(resp.context["alexey_booking_url"], "/book/alexey")
        self.assertContains(resp, 'href="/book/alexey"')
        self.assertNotContains(resp, "https://calendly.com/dtc-alexey")

    def test_no_native_alexey_host_falls_back_to_calendly(self):
        BookingHost.objects.filter(slug="alexey").update(is_active=False)
        CallHost.objects.filter(slug="alexey").update(
            is_active=True,
            booking_url="https://calendly.com/dtc-alexey/ai-shipping-labs-call",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(
            resp.context["alexey_booking_url"],
            "https://calendly.com/dtc-alexey/ai-shipping-labs-call",
        )
        self.assertContains(
            resp, "https://calendly.com/dtc-alexey/ai-shipping-labs-call"
        )

    def test_neither_native_nor_calendly_alexey_hides_cta(self):
        BookingHost.objects.filter(slug="alexey").update(is_active=False)
        CallHost.objects.filter(slug="alexey").update(
            is_active=True,
            booking_url="",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(resp.context["alexey_booking_url"], "")
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-alexey"'
        )

    def test_native_valeria_host_links_to_native_booking_url(self):
        BookingHost.objects.update_or_create(
            slug="valeria",
            defaults={"is_active": True, "name": "Valeriia Kuka"},
        )
        CallHost.objects.filter(slug="valeria").update(
            is_active=True,
            booking_url="https://calendar.app.google/Rh5oWPU9ZAuuDLPt9",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(resp.context["valeria_booking_url"], "/book/valeria")
        self.assertContains(resp, 'href="/book/valeria"')
        self.assertNotContains(resp, "calendar.app.google")

    def test_no_native_valeria_host_falls_back_to_callhost(self):
        BookingHost.objects.filter(slug="valeria").update(is_active=False)
        CallHost.objects.filter(slug="valeria").update(
            is_active=True,
            booking_url="https://calendar.app.google/Rh5oWPU9ZAuuDLPt9",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(
            resp.context["valeria_booking_url"],
            "https://calendar.app.google/Rh5oWPU9ZAuuDLPt9",
        )
        self.assertContains(resp, "https://calendar.app.google/Rh5oWPU9ZAuuDLPt9")

    def test_neither_native_nor_callhost_valeria_hides_cta(self):
        BookingHost.objects.filter(slug="valeria").update(is_active=False)
        CallHost.objects.filter(slug="valeria").update(
            is_active=True,
            booking_url="",
        )
        resp = self.client.get(reverse("onboarding_start"))
        self.assertEqual(resp.context["valeria_booking_url"], "")
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-valeria"'
        )
