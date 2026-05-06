"""Tests for events mobile responsive fixes - issue #178.

Covers:
- Event list: "Registered" badge inline with badges row, not in separate div
- Event list: arrow icon hidden on mobile (has `hidden sm:block` classes)
- Event list: past event "Watch recording" link has adequate tap target
- Event detail: registration buttons are full-width on mobile (`w-full sm:w-auto`)
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.access import LEVEL_OPEN
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


class EventListArrowHiddenOnMobileTest(TestCase):
    """Arrow icons on event list cards should be hidden on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title="Arrow Test Event",
            slug="arrow-test-event",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
        )

    def test_upcoming_event_arrow_hidden_on_mobile(self):
        response = self.client.get("/events")
        content = response.content.decode()
        self.assertIn('data-lucide="arrow-right"', content)
        self.assertIn("hidden sm:block", content)

    def test_upcoming_event_arrow_has_flex_shrink_0(self):
        response = self.client.get("/events")
        content = response.content.decode()
        self.assertIn("flex-shrink-0", content)


class EventListPastArrowHiddenOnMobileTest(TestCase):
    """Past event arrow icons should also be hidden on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title="Past Arrow Test",
            slug="past-arrow-test",
            start_datetime=timezone.now() - timedelta(days=7),
            status="completed",
        )

    def test_past_event_arrow_hidden_on_mobile(self):
        response = self.client.get("/events")
        content = response.content.decode()
        # All arrow-right icons should use hidden sm:block
        arrow_count = content.count('data-lucide="arrow-right"')
        hidden_arrow_count = content.count("hidden sm:block")
        # Every arrow-right should be paired with hidden sm:block
        self.assertGreaterEqual(hidden_arrow_count, arrow_count)


class EventListRegisteredBadgeInlineTest(TierSetupMixin, TestCase):
    """The 'Registered' badge should be inline with other badges, not in a separate div."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = Event.objects.create(
            title="Reg Badge Event",
            slug="reg-badge-event",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
            required_level=LEVEL_OPEN,
        )
        cls.user = User.objects.create_user(
            email="reg@test.com",
            password="testpass",
            email_verified=True,
        )
        EventRegistration.objects.create(user=cls.user, event=cls.event)

    def test_registered_badge_in_badges_row(self):
        self.client.login(email="reg@test.com", password="testpass")
        response = self.client.get("/events")
        content = response.content.decode()
        # The "Registered" badge should appear in the flex-wrap badges div
        # (same div as event type and tier badges), not in a separate trailing div
        registered_pos = content.index("Registered")
        # Find the enclosing flex-wrap div
        flex_wrap_start = content.rfind("flex-wrap", 0, registered_pos)
        self.assertGreater(flex_wrap_start, 0)
        # The flex-wrap should be within reasonable distance (badges row)
        self.assertLess(registered_pos - flex_wrap_start, 600)


class EventListPastRecordingTapTargetTest(TestCase):
    """The past-recordings cards on /events?filter=past should have a
    tap-friendly tag chip (min-h-[44px]).
    """

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title="Recorded Event",
            slug="recorded-event",
            start_datetime=timezone.now() - timedelta(days=7),
            status="completed",
            recording_url="https://youtube.com/watch?v=test",
            tags=["python"],
            published=True,
        )

    def test_past_tag_chip_has_min_height(self):
        response = self.client.get("/events?filter=past")
        content = response.content.decode()
        # Tag chip links must be tap-target-sized on mobile.
        import re
        match = re.search(
            r'<a[^>]*href="/events\?filter=past&amp;tag=python"[^>]*>',
            content,
        )
        self.assertIsNotNone(match, "Past tag chip not found")
        self.assertIn("min-h-[44px]", match.group(0))


class EventDetailRegisterButtonFullWidthMobileTest(TestCase):
    """Registration buttons should be full-width on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title="Register Button Event",
            slug="register-btn-event",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
            required_level=LEVEL_OPEN,
        )
        cls.user = User.objects.create_user(
            email="btn@test.com",
            password="testpass",
            email_verified=True,
        )

    def test_register_button_full_width_on_mobile(self):
        self.client.login(email="btn@test.com", password="testpass")
        response = self.client.get(f"/events/{self.event.slug}")
        content = response.content.decode()
        # The register button should have w-full sm:w-auto
        btn_pos = content.index('id="register-btn"')
        btn_start = content.rfind("<button", 0, btn_pos)
        btn_tag = content[btn_start : btn_pos + 20]
        self.assertIn("w-full", btn_tag)
        self.assertIn("sm:w-auto", btn_tag)

    def test_unregister_button_full_width_on_mobile(self):
        EventRegistration.objects.create(user=self.user, event=self.event)
        self.client.login(email="btn@test.com", password="testpass")
        response = self.client.get(f"/events/{self.event.slug}")
        content = response.content.decode()
        # The unregister button should have w-full sm:w-auto
        btn_pos = content.index('id="unregister-btn"')
        btn_start = content.rfind("<button", 0, btn_pos)
        btn_tag = content[btn_start : btn_pos + 20]
        self.assertIn("w-full", btn_tag)
        self.assertIn("sm:w-auto", btn_tag)
