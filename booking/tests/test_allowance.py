from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    Booking,
    BookingHost,
)
from booking.services.allowance import get_member_allowance
from questionnaires.models import Questionnaire, Response


class BookingAllowanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="onboarded@example.com",
            password="password123",
            first_name="Onboarded",
            last_name="Member",
        )
        self.non_onboarded_user = User.objects.create_user(
            email="newbie@example.com",
            password="password123",
            first_name="Newbie",
            last_name="Member",
        )
        self.questionnaire, _ = Questionnaire.objects.get_or_create(
            slug="generic-onboarding",
            defaults={"title": "Generic Onboarding", "purpose": "onboarding"},
        )
        Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=self.user,
            status="submitted",
        )
        self.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Test",
                "email": "alexey.test@example.com",
                "timezone": "UTC",
                "is_active": True,
            },
        )

    def test_anonymous_user_cannot_access_booking_views(self):
        resp_index = self.client.get("/book")
        self.assertRedirects(resp_index, "/accounts/login/?next=/book", fetch_redirect_response=False)

        resp_host = self.client.get("/book/alexey")
        self.assertRedirects(resp_host, "/accounts/login/?next=/book/alexey", fetch_redirect_response=False)

        resp_post = self.client.post("/book/alexey/confirm", {})
        self.assertRedirects(resp_post, "/accounts/login/?next=/book/alexey", fetch_redirect_response=False)

        # JSON / Ajax request receives 401
        resp_ajax = self.client.post(
            "/book/alexey/confirm",
            {},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_ajax.status_code, 401)

    def test_non_onboarded_member_blocked_from_booking_get_and_post(self):
        self.client.force_login(self.non_onboarded_user)

        resp_get = self.client.get("/book/alexey")
        self.assertContains(resp_get, 'data-testid="request-call-onboarding-gate"')
        self.assertNotContains(resp_get, 'data-testid="booking-container"')

        resp_post = self.client.post(
            "/book/alexey/confirm",
            {
                "start_iso": "2026-10-19T10:00:00+00:00",
                "invitee_name": "Newbie Member",
                "invitee_email": "newbie@example.com",
            },
        )
        self.assertRedirects(resp_post, "/book/alexey")
        self.assertEqual(Booking.objects.filter(invitee_email="newbie@example.com").count(), 0)

    def test_onboarded_member_gets_initial_allowance_of_one(self):
        allowance = get_member_allowance(self.user)
        self.assertTrue(allowance.is_authenticated)
        self.assertTrue(allowance.is_onboarded)
        self.assertEqual(allowance.base_allowance, 1)
        self.assertEqual(allowance.approved_requests_count, 0)
        self.assertEqual(allowance.total_allowance, 1)
        self.assertEqual(allowance.confirmed_bookings_count, 0)
        self.assertEqual(allowance.remaining_allowance, 1)
        self.assertTrue(allowance.can_book)
        self.assertFalse(allowance.has_active_booking)

    def test_confirmed_booking_decrements_available_allowance(self):
        now = timezone.now()
        Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now - timedelta(days=2),
            end_datetime=now - timedelta(days=2, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        allowance = get_member_allowance(self.user, now=now)
        self.assertEqual(allowance.total_allowance, 1)
        self.assertEqual(allowance.confirmed_bookings_count, 1)
        self.assertEqual(allowance.remaining_allowance, 0)
        self.assertFalse(allowance.can_book)
        self.assertFalse(allowance.has_active_booking)

    def test_canceled_booking_restores_available_allowance(self):
        now = timezone.now()
        Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now + timedelta(days=2),
            end_datetime=now + timedelta(days=2, minutes=30),
            status=STATUS_CANCELED,
        )

        allowance = get_member_allowance(self.user, now=now)
        self.assertEqual(allowance.total_allowance, 1)
        self.assertEqual(allowance.confirmed_bookings_count, 0)
        self.assertEqual(allowance.remaining_allowance, 1)
        self.assertTrue(allowance.can_book)
        self.assertFalse(allowance.has_active_booking)

    def test_member_cannot_book_second_call_when_active_booking_exists(self):
        now = timezone.now()
        active = Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now + timedelta(days=2),
            end_datetime=now + timedelta(days=2, minutes=30),
            status=STATUS_CONFIRMED,
        )

        allowance = get_member_allowance(self.user, now=now)
        self.assertTrue(allowance.has_active_booking)
        self.assertEqual(allowance.active_booking, active)
        self.assertFalse(allowance.can_book)

        self.client.force_login(self.user)
        resp = self.client.get("/book/alexey")
        self.assertContains(resp, 'data-testid="active-booking-banner"')

    @patch("booking.views.public.reschedule_booking")
    def test_reschedule_preserves_single_active_booking(self, mock_resched):
        now = timezone.now()
        booking = Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now + timedelta(days=2),
            end_datetime=now + timedelta(days=2, minutes=30),
            status=STATUS_CONFIRMED,
        )

        new_start = now + timedelta(days=3)
        resp = self.client.post(
            f"/book/reschedule/{booking.manage_token}",
            {"start_iso": new_start.isoformat()},
        )
        self.assertRedirects(resp, f"/book/confirmed/{booking.manage_token}")
        self.assertEqual(mock_resched.call_count, 1)

        self.assertEqual(
            Booking.objects.filter(member=self.user, status=STATUS_CONFIRMED).count(),
            1,
        )
        allowance = get_member_allowance(self.user, now=now)
        self.assertTrue(allowance.has_active_booking)
        self.assertEqual(allowance.confirmed_bookings_count, 1)

    def test_post_confirm_rejects_with_error_when_allowance_zero(self):
        now = timezone.now()
        # Consumed past booking
        Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now - timedelta(days=5),
            end_datetime=now - timedelta(days=5, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        self.client.force_login(self.user)
        resp = self.client.post(
            "/book/alexey/confirm",
            {
                "start_iso": (now + timedelta(days=2)).isoformat(),
                "invitee_name": "Onboarded Member",
                "invitee_email": self.user.email,
            },
        )
        self.assertRedirects(resp, "/book/alexey")
        # Ensure no new booking was created
        self.assertEqual(Booking.objects.filter(member=self.user).count(), 1)

    def test_post_confirm_rejects_with_error_when_active_booking_present(self):
        now = timezone.now()
        Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Onboarded Member",
            invitee_email=self.user.email,
            start_datetime=now + timedelta(days=2),
            end_datetime=now + timedelta(days=2, minutes=30),
            status=STATUS_CONFIRMED,
        )

        self.client.force_login(self.user)
        resp = self.client.post(
            "/book/alexey/confirm",
            {
                "start_iso": (now + timedelta(days=4)).isoformat(),
                "invitee_name": "Onboarded Member",
                "invitee_email": self.user.email,
            },
        )
        self.assertRedirects(resp, "/book/alexey")
        # Ensure no second booking was created
        self.assertEqual(Booking.objects.filter(member=self.user).count(), 1)
