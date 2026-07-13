"""Focused regressions for public Events presentation polish (issue #1232)."""

import datetime
import re
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.models import Workshop
from events.models import Event, EventSeries
from payments.models import Tier

ROOT = Path(__file__).resolve().parents[2]
NO_RECORDING_COPY = "This event has ended. No recording is available."
User = get_user_model()


def _event(*, slug, start_datetime=None, status="completed", **kwargs):
    return Event.objects.create(
        title=slug.replace("-", " ").title(),
        slug=slug,
        start_datetime=start_datetime or timezone.now() - timedelta(days=2),
        status=status,
        **kwargs,
    )


class CalendarAccessibilityMarkupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first = _event(
            slug="calendar-first-week",
            start_datetime=timezone.make_aware(datetime.datetime(2026, 3, 2, 14)),
            status="upcoming",
        )
        cls.second = _event(
            slug="calendar-second-week",
            start_datetime=timezone.make_aware(datetime.datetime(2026, 3, 10, 14)),
            status="upcoming",
            required_level=20,
        )

    def test_only_event_days_have_button_semantics_and_aria_wiring(self):
        response = self.client.get("/events/calendar/2026/3")
        html = response.content.decode()

        for day in (2, 10):
            self.assertRegex(
                html,
                rf'data-calendar-day="{day}"[^>]+role="button"[^>]+'
                rf'tabindex="0"[^>]+aria-expanded="false"[^>]+'
                rf'aria-controls="day-events-2026-3-{day}"',
            )
            self.assertIn(f'id="day-events-2026-3-{day}"', html)
            self.assertIn(
                f'aria-labelledby="calendar-day-2026-3-{day}"',
                html,
            )

        self.assertEqual(html.count('data-calendar-day="'), 2)
        self.assertEqual(html.count('role="button"'), 2)
        self.assertEqual(html.count('tabindex="0"'), 2)

    def test_each_panel_is_between_its_week_row_and_the_next_week(self):
        html = self.client.get("/events/calendar/2026/3").content.decode()

        week_two = html.index('class="grid grid-cols-7 border-b border-border/50" data-calendar-week="2"')
        panel_two = html.index('id="day-events-2026-3-2"')
        week_three = html.index(
            'class="grid grid-cols-7 border-b border-border/50" data-calendar-week="3"',
            week_two + 1,
        )
        panel_ten = html.index('id="day-events-2026-3-10"')
        week_four = html.index(
            'class="grid grid-cols-7 border-b border-border/50" data-calendar-week="4"',
            week_three + 1,
        )

        self.assertLess(week_two, panel_two)
        self.assertLess(panel_two, week_three)
        self.assertLess(week_three, panel_ten)
        self.assertLess(panel_ten, week_four)
        self.assertIn(self.first.get_absolute_url(), html)
        self.assertIn(self.second.get_absolute_url(), html)
        self.assertIn("Main or above", html)

    def test_mobile_agenda_keeps_canonical_links_without_button_cells(self):
        response = self.client.get("/events/calendar/2026/3")

        self.assertContains(response, '<div class="sm:hidden">')
        self.assertContains(response, self.first.get_absolute_url())
        self.assertNotContains(response, "data-mobile-calendar-day")


@tag("visual_regression")
class EventsPresentationClassTest(TestCase):
    """Tailwind/layout contracts are opt-in visual regressions by policy."""

    def test_calendar_controls_use_pill_and_minimum_target_classes(self):
        html = self.client.get("/events/calendar/2026/3").content.decode()

        for label in ("List", "Calendar"):
            match = re.search(rf'<a[^>]+class="([^"]+)"[^>]*>{label}</a>', html)
            self.assertIsNotNone(match)
            classes = match.group(1)
            for expected in (
                "inline-flex",
                "min-h-[44px]",
                "rounded-full",
                "px-4",
                "py-2",
                "text-sm",
                "font-medium",
            ):
                self.assertIn(expected, classes)

        for label in ("Previous month", "Next month"):
            match = re.search(
                rf'<a[^>]+class="([^"]+)"[^>]+aria-label="{label}"',
                html,
            )
            self.assertIsNotNone(match)
            self.assertIn("min-h-[44px]", match.group(1))
        today = re.search(r'<a[^>]+class="([^"]+)"[^>]*>\s*Today\s*</a>', html)
        self.assertIsNotNone(today)
        self.assertIn("min-h-[44px]", today.group(1))

    def test_interactive_days_have_inset_focus_ring_classes(self):
        _event(
            slug="focusable-calendar-day",
            start_datetime=timezone.make_aware(datetime.datetime(2026, 3, 15, 14)),
            status="upcoming",
        )
        html = self.client.get("/events/calendar/2026/3").content.decode()
        tag_match = re.search(r'<div class="([^"]+)"[^>]+data-calendar-day="15"', html)

        self.assertIsNotNone(tag_match)
        for expected in (
            "focus-visible:outline-none",
            "focus-visible:ring-2",
            "focus-visible:ring-inset",
            "focus-visible:ring-accent",
        ):
            self.assertIn(expected, tag_match.group(1))

    def test_event_card_partials_and_all_stacks_use_compact_rhythm(self):
        single = (ROOT / "templates/events/_upcoming_event_card.html").read_text()
        series = (ROOT / "templates/events/_upcoming_series_card.html").read_text()
        listing = (ROOT / "templates/events/events_list.html").read_text()

        for source in (single, series):
            self.assertIn("p-4", source)
            self.assertIn("sm:p-5", source)
            self.assertNotIn("bg-card p-6", source)
        for testid in (
            "upcoming-events-stack",
            "past-events-stack",
            "past-recordings-stack",
        ):
            self.assertRegex(
                listing,
                rf'class="space-y-4" data-testid="{testid}"',
            )


class PastEventClosureStateTest(TestCase):
    def test_plain_past_event_without_followup_renders_closure(self):
        event = _event(slug="past-without-followup")

        response = self.client.get(event.get_absolute_url())

        self.assertContains(response, NO_RECORDING_COPY, count=1)
        self.assertContains(response, 'data-testid="event-no-recording-closure"')

    def test_upcoming_event_does_not_render_closure(self):
        event = _event(
            slug="upcoming-without-recording",
            start_datetime=timezone.now() + timedelta(days=2),
            status="upcoming",
        )

        self.assertNotContains(self.client.get(event.get_absolute_url()), NO_RECORDING_COPY)

    def test_any_supported_recording_field_suppresses_closure(self):
        field_values = {
            "recording_url": "https://example.com/watch",
            "recording_s3_url": "https://example.com/raw.mp4",
            "recording_embed_url": "https://example.com/embed",
        }

        for index, (field, value) in enumerate(field_values.items()):
            with self.subTest(field=field):
                event = _event(slug=f"recording-field-{index}", **{field: value})
                self.assertNotContains(
                    self.client.get(event.get_absolute_url()),
                    NO_RECORDING_COPY,
                )

    def test_recap_suppresses_closure(self):
        event = _event(slug="past-with-recap", recap_html="<h2>Session recap</h2>")

        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, "Session recap")
        self.assertNotContains(response, NO_RECORDING_COPY)

    def test_linked_workshop_suppresses_closure_and_keeps_handoff(self):
        event = _event(slug="past-with-workshop", kind="workshop")
        workshop = Workshop.objects.create(
            slug="past-workshop-handoff",
            title="Past Workshop Handoff",
            date=datetime.date(2026, 3, 1),
            status="published",
            event=event,
        )

        response = self.client.get(event.get_absolute_url())
        self.assertNotContains(response, NO_RECORDING_COPY)
        self.assertContains(response, 'data-testid="event-workshop-writeup"')
        self.assertContains(response, workshop.get_absolute_url())

    def test_under_tier_member_with_hidden_recording_never_gets_false_closure(self):
        free = Tier.objects.get(slug="free")
        user = User.objects.create_user(
            email="free-1232@example.com",
            password="test-password",
            tier=free,
        )
        event = _event(
            slug="gated-existing-recording",
            required_level=20,
            recording_s3_url="https://protected.example.com/recording.mp4",
        )
        self.client.force_login(user)

        response = self.client.get(event.get_absolute_url())

        self.assertNotContains(response, NO_RECORDING_COPY)
        self.assertNotContains(response, "https://protected.example.com/recording.mp4")
        self.assertFalse(response.context["post_event_resources"]["has_resources"])


class UpcomingCardBehaviorPreservationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.standalone = _event(
            slug="standalone-upcoming-card",
            start_datetime=now + timedelta(days=1),
            status="upcoming",
            external_host="Luma",
            tags=["agents"],
        )
        cls.series = EventSeries.objects.create(
            name="Weekly Build Club",
            slug="weekly-build-club",
            start_time=time(18),
        )
        for position, days in enumerate((2, 9), start=1):
            _event(
                slug=f"weekly-build-club-{position}",
                start_datetime=now + timedelta(days=days),
                status="upcoming",
                event_series=cls.series,
                series_position=position,
            )

    def test_compaction_keeps_single_and_grouped_canonical_links_and_metadata(self):
        response = self.client.get("/events")

        self.assertContains(response, 'data-testid="upcoming-event-card"')
        self.assertContains(response, self.standalone.get_absolute_url())
        self.assertContains(response, "Hosted on Luma")
        self.assertContains(response, "agents")
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, self.series.get_absolute_url())
        self.assertContains(response, "2 upcoming sessions")
