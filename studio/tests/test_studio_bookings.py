"""Studio Bookings list/detail/cancel views (issue #2018)."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from booking.models import STATUS_CANCELED, STATUS_CONFIRMED, Booking, BookingHost
from community.models import BookedCall, CallHost
from crm.models import CRMRecord
from integrations.config import site_base_url
from tests.fixtures import StaffUserMixin

User = get_user_model()

CANCEL_URL_NAME = "studio_booking_cancel"


class StudioBookingsTestBase(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email="rzaaeeff@gmail.com",
            password="testpass",
            first_name="Heydar",
            last_name="Rzayev",
        )
        cls.crm_record = CRMRecord.objects.create(user=cls.member)
        cls.regular_user = User.objects.create_user(
            email="regular@test.com",
            password="testpass",
            first_name="Bob",
            last_name="Regular",
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
        cls.call_host = CallHost.objects.create(
            name="Alexey Grigorev",
            slug="alexey-crm",
            is_active=True,
        )
        cls.booked_call = BookedCall.objects.create(
            host=cls.call_host,
            member=cls.member,
            invitee_email="rzaaeeff@gmail.com",
            invitee_name="Heydar Rzayev",
            scheduled_at=datetime(2026, 10, 29, 16, 30, tzinfo=UTC),
            status="booked",
            calendly_event_uri="aisl:booking:test-token-2018",
        )
        cls.booking = Booking.objects.create(
            host=cls.host,
            member=cls.member,
            booked_call=cls.booked_call,
            invitee_name="Heydar Rzayev",
            invitee_email="rzaaeeff@gmail.com",
            invitee_timezone="UTC",
            start_datetime=datetime(2026, 10, 29, 16, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 10, 29, 17, 0, tzinfo=UTC),
            duration_minutes=30,
            status=STATUS_CONFIRMED,
            discussion_topic="Sprint planning help",
            discussion_notes="Working on a RAG pipeline.",
            company="Example Corp",
            zoom_meeting_id="zm-123",
            zoom_join_url="https://zoom.test/j/123",
            google_event_id="gcal-123",
        )

    def _cancel_url(self):
        return reverse(CANCEL_URL_NAME, args=[self.booking.pk])

    def _cancel(self, **payload):
        return self.client.post(self._cancel_url(), payload)


class StudioBookingsAccessTest(StudioBookingsTestBase):
    def test_booking_list_requires_staff(self):
        resp_anon = self.client.get("/studio/bookings/")
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn("/accounts/login/", resp_anon.url)

        self.client.force_login(self.regular_user)
        resp_non_staff = self.client.get("/studio/bookings/")
        self.assertEqual(resp_non_staff.status_code, 403)

        self.client.force_login(self.staff)
        resp_staff = self.client.get("/studio/bookings/")
        self.assertContains(resp_staff, f'data-booking-id="{self.booking.pk}"')

    def test_booking_detail_requires_staff(self):
        detail_url = f"/studio/bookings/{self.booking.pk}/"

        resp_anon = self.client.get(detail_url)
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn("/accounts/login/", resp_anon.url)

        self.client.force_login(self.regular_user)
        resp_non_staff = self.client.get(detail_url)
        self.assertEqual(resp_non_staff.status_code, 403)

        self.client.force_login(self.staff)
        resp_staff = self.client.get(detail_url)
        self.assertContains(resp_staff, 'data-testid="cancel-booking-button"')

    def test_non_staff_cannot_cancel_and_booking_is_unchanged(self):
        self.client.force_login(self.regular_user)
        resp = self._cancel()
        self.assertEqual(resp.status_code, 403)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CONFIRMED)
        self.assertIsNone(self.booking.canceled_at)

    def test_anonymous_cancel_redirects_to_login(self):
        resp = self._cancel()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CONFIRMED)

    def test_cancel_is_post_only(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self._cancel_url())
        self.assertEqual(resp.status_code, 405)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CONFIRMED)
        self.assertIsNone(self.booking.canceled_at)


class StudioBookingsListTest(StudioBookingsTestBase):
    def test_list_filters_by_status_and_links_rows(self):
        canceled = Booking.objects.create(
            host=self.host,
            invitee_name="Past Invitee",
            invitee_email="past@example.com",
            start_datetime=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
            status=STATUS_CANCELED,
        )

        self.client.force_login(self.staff)

        resp_all = self.client.get("/studio/bookings/")
        self.assertContains(resp_all, f'href="/studio/bookings/{self.booking.pk}/"')
        self.assertContains(resp_all, f'href="/studio/bookings/{canceled.pk}/"')
        self.assertContains(resp_all, f'href="/studio/users/{self.member.pk}/"')
        self.assertContains(resp_all, 'data-testid="booking-status-badge"', count=2)

        resp_confirmed = self.client.get("/studio/bookings/?status=confirmed")
        self.assertContains(resp_confirmed, f'href="/studio/bookings/{self.booking.pk}/"')
        self.assertNotContains(resp_confirmed, f'href="/studio/bookings/{canceled.pk}/"')

        resp_canceled = self.client.get("/studio/bookings/?status=canceled")
        self.assertNotContains(resp_canceled, f'href="/studio/bookings/{self.booking.pk}/"')
        self.assertContains(resp_canceled, f'href="/studio/bookings/{canceled.pk}/"')

        resp_rescheduled = self.client.get("/studio/bookings/?status=rescheduled")
        self.assertNotContains(resp_rescheduled, f'href="/studio/bookings/{self.booking.pk}/"')
        self.assertNotContains(resp_rescheduled, f'href="/studio/bookings/{canceled.pk}/"')

    def test_list_empty_state_when_no_bookings(self):
        Booking.objects.all().delete()
        self.client.force_login(self.staff)
        resp = self.client.get("/studio/bookings/")
        self.assertContains(resp, "No bookings yet.")


class StudioBookingDetailTest(StudioBookingsTestBase):
    def test_detail_shows_booking_fields_and_member_context(self):
        self.client.force_login(self.staff)
        resp = self.client.get(f"/studio/bookings/{self.booking.pk}/")

        self.assertContains(resp, "Heydar Rzayev")
        self.assertContains(resp, "rzaaeeff@gmail.com")
        self.assertContains(resp, "Sprint planning help")
        self.assertContains(resp, "Working on a RAG pipeline.")
        self.assertContains(resp, "Example Corp")
        self.assertContains(resp, "https://zoom.test/j/123")
        self.assertContains(resp, "gcal-123")
        self.assertContains(resp, "2026-10-29 16:30")
        self.assertContains(resp, f'href="/studio/users/{self.member.pk}/"')
        self.assertContains(resp, f'href="/studio/crm/{self.crm_record.pk}/"')

    def test_detail_hides_manage_token_and_member_links(self):
        self.client.force_login(self.staff)
        resp = self.client.get(f"/studio/bookings/{self.booking.pk}/")

        self.assertNotContains(resp, self.booking.manage_token)
        self.assertNotContains(resp, f"/book/cancel/{self.booking.manage_token}")
        self.assertNotContains(resp, f"/book/reschedule/{self.booking.manage_token}")

    def test_detail_shows_cancel_form_only_for_confirmed(self):
        self.client.force_login(self.staff)

        resp_confirmed = self.client.get(f"/studio/bookings/{self.booking.pk}/")
        self.assertContains(resp_confirmed, 'data-testid="cancel-booking-button"')
        self.assertContains(resp_confirmed, 'data-testid="notify-member-checkbox"')
        self.assertContains(resp_confirmed, f'action="{self._cancel_url()}"')

        self.booking.status = STATUS_CANCELED
        self.booking.save(update_fields=["status"])
        resp_canceled = self.client.get(f"/studio/bookings/{self.booking.pk}/")
        self.assertNotContains(resp_canceled, 'data-testid="cancel-booking-button"')


class StudioBookingCancelTest(StudioBookingsTestBase):
    @patch("booking.services.booking_service.delete_calendar_event")
    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    def test_cancel_confirmed_booking_without_notify_sends_no_email(
        self, mock_del_zoom, mock_del_cal,
    ):
        self.client.force_login(self.staff)
        resp = self._cancel()

        self.assertRedirects(resp, f"/studio/bookings/{self.booking.pk}/")

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CANCELED)
        self.assertIsNotNone(self.booking.canceled_at)

        mock_del_zoom.assert_called_once_with("zm-123")
        mock_del_cal.assert_called_once_with("gcal-123")

        self.booked_call.refresh_from_db()
        self.assertEqual(self.booked_call.status, "canceled")
        self.assertIsNotNone(self.booked_call.canceled_at)

        self.assertEqual(len(mail.outbox), 0)

    @patch("booking.services.booking_service.delete_calendar_event")
    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    def test_cancel_with_notify_sends_exactly_one_email(
        self, mock_del_zoom, mock_del_cal,
    ):
        self.client.force_login(self.staff)
        resp = self._cancel(notify_member="on")

        self.assertRedirects(resp, f"/studio/bookings/{self.booking.pk}/")

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CANCELED)

        mock_del_zoom.assert_called_once_with("zm-123")
        mock_del_cal.assert_called_once_with("gcal-123")

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["rzaaeeff@gmail.com"])
        self.assertIn("Alexey Grigorev", email.subject)
        self.assertIn("2026-10-29", email.subject)
        self.assertIn("2026-10-29 16:30 UTC", email.body)
        self.assertIn(f"{site_base_url()}/book/{self.host.slug}", email.body)
        self.assertEqual(email.content_subtype, "plain")

    @patch("studio.views.bookings.cancel_booking")
    def test_cancel_already_canceled_booking_is_a_noop(self, mock_cancel):
        canceled_at = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
        self.booking.status = STATUS_CANCELED
        self.booking.canceled_at = canceled_at
        self.booking.save(update_fields=["status", "canceled_at"])

        self.client.force_login(self.staff)
        resp = self._cancel(notify_member="on")

        self.assertRedirects(resp, f"/studio/bookings/{self.booking.pk}/")

        mock_cancel.assert_not_called()

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CANCELED)
        self.assertEqual(self.booking.canceled_at, canceled_at)

        self.assertEqual(len(mail.outbox), 0)

    @patch("studio.views.bookings.send_mail", side_effect=Exception("relay down"))
    @patch("booking.services.booking_service.delete_calendar_event")
    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    def test_email_failure_does_not_block_cancellation(
        self, mock_del_zoom, mock_del_cal, mock_send,
    ):
        self.client.force_login(self.staff)
        resp = self._cancel(notify_member="on")

        self.assertRedirects(resp, f"/studio/bookings/{self.booking.pk}/")

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, STATUS_CANCELED)
        self.assertIsNotNone(self.booking.canceled_at)
        self.assertEqual(mock_send.call_args.kwargs["recipient_list"], ["rzaaeeff@gmail.com"])
        stored = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn(
            "Booking canceled, but the notification email could not be sent.",
            stored,
        )

    def test_cancel_success_message_is_shown(self):
        self.client.force_login(self.staff)
        resp = self._cancel()

        self.assertRedirects(resp, f"/studio/bookings/{self.booking.pk}/")

        stored = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn(f"Booking for {self.booking.invitee_email} canceled.", stored)

    def test_cancel_already_canceled_shows_info_message(self):
        self.booking.status = STATUS_CANCELED
        self.booking.canceled_at = dj_timezone.now()
        self.booking.save(update_fields=["status", "canceled_at"])

        self.client.force_login(self.staff)
        resp = self._cancel()

        stored = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("This booking is already canceled.", stored)
