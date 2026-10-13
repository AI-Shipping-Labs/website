from datetime import datetime, time, timezone

from django.test import TestCase

from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    Booking,
    BookingDateOverride,
    BookingHost,
    BookingWeeklyWindow,
)


class BookingModelsTest(TestCase):
    def test_host_and_windows_creation(self):
        host = BookingHost.objects.create(
            name="Alexey Test",
            slug="alexey-test",
            email="alexey.test@example.com",
            timezone="Europe/Berlin",
            slot_duration_minutes=30,
        )
        self.assertEqual(str(host), "Alexey Test (alexey-test)")

        window = BookingWeeklyWindow.objects.create(
            host=host,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(18, 0),
        )
        self.assertEqual(window.weekday, 0)
        self.assertTrue("Monday" in str(window))

        override = BookingDateOverride.objects.create(
            host=host,
            date=datetime(2026, 12, 25).date(),
            is_unavailable=True,
        )
        self.assertTrue(override.is_unavailable)
        self.assertIn("Closed", str(override))

    def test_booking_creation_and_tokens(self):
        host = BookingHost.objects.create(
            name="Alexey Test",
            slug="alexey-test-2",
            email="alexey.test@example.com",
        )
        start = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc)

        booking = Booking.objects.create(
            host=host,
            invitee_name="Test Invitee",
            invitee_email="invitee@example.com",
            start_datetime=start,
            end_datetime=end,
            status=STATUS_CONFIRMED,
        )

        self.assertTrue(booking.is_active)
        self.assertTrue(len(booking.manage_token) > 20)
        self.assertIn("Test Invitee", str(booking))

        booking.status = STATUS_CANCELED
        booking.save()
        self.assertFalse(booking.is_active)
