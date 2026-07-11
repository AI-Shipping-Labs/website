"""Sprint plan card lifecycle framing tests (issue #1129, Part 3).

The dashboard "Your sprint plan" card used to render
``{{ plan.get_status_display }}`` — a non-existent attribute that produced
an empty status line, so an ended sprint still read as the member's
current plan. The card now derives its status from the date-based
``Sprint.sprint_badge_current`` and reframes the heading to past tense when
the sprint has ended.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from plans.models import Plan, Sprint
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _today():
    return datetime.date.today()


@tag("core")
class DashboardSprintPlanLifecycleTest(TierSetupMixin, TestCase):
    def _login_with_plan(self, email, start_date, duration_weeks, status):
        user = User.objects.create_user(
            email=email, password="pw", tier=self.main_tier,
        )
        sprint = Sprint.objects.create(
            name="Accountability Sprint",
            slug=f"sprint-{email.split('@')[0]}",
            start_date=start_date,
            duration_weeks=duration_weeks,
            status=status,
        )
        plan = Plan.objects.create(
            member=user, sprint=sprint, shared_at=timezone.now(),
        )
        self.client.login(email=email, password="pw")
        return user, sprint, plan

    def test_ended_sprint_shows_ended_label_and_past_tense_heading(self):
        # Ends ~18 days ago: start 60 days ago, 6-week (42d) window.
        _, sprint, plan = self._login_with_plan(
            "ended@test.com",
            start_date=_today() - datetime.timedelta(days=60),
            duration_weeks=6,
            status="completed",
        )
        self.assertTrue(sprint.has_ended())

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        # Status line reflects the real lifecycle label, not an empty span.
        status_idx = content.index('data-testid="account-sprint-plan-status"')
        status_fragment = content[status_idx:status_idx + 400]
        self.assertIn("Ended", status_fragment)

        # Heading is reframed to past tense.
        heading_idx = content.index('data-testid="account-sprint-plan-heading"')
        heading_fragment = content[heading_idx:heading_idx + 300]
        self.assertIn("Your latest sprint plan", heading_fragment)
        self.assertNotIn("Your sprint plan", heading_fragment)

        # The Open my plan link is still available for the past plan.
        self.assertContains(
            response, 'data-testid="account-sprint-plan-open"',
        )
        # The duplicate current-cohort sidebar no longer renders.
        self.assertNotContains(response, "Current cohort")

    def test_active_sprint_keeps_present_heading_and_active_label(self):
        # start 14 days ago, 8-week (56d) window -> active (not ending soon).
        _, sprint, plan = self._login_with_plan(
            "active@test.com",
            start_date=_today() - datetime.timedelta(days=14),
            duration_weeks=8,
            status="active",
        )
        self.assertFalse(sprint.has_ended())

        response = self.client.get("/")
        content = response.content.decode()

        heading_idx = content.index('data-testid="account-sprint-plan-heading"')
        heading_fragment = content[heading_idx:heading_idx + 300]
        self.assertIn("Your sprint plan", heading_fragment)
        self.assertNotIn("Your latest sprint plan", heading_fragment)

        status_idx = content.index('data-testid="account-sprint-plan-status"')
        status_fragment = content[status_idx:status_idx + 400]
        self.assertIn("Active", status_fragment)

        self.assertContains(
            response, 'data-testid="account-sprint-plan-open"',
        )

    def test_no_get_status_display_placeholder(self):
        # Regression guard: the broken attribute call is gone.
        self._login_with_plan(
            "reg@test.com",
            start_date=_today() - datetime.timedelta(days=14),
            duration_weeks=8,
            status="active",
        )
        response = self.client.get("/")
        self.assertNotContains(response, "get_status_display")

    def test_no_plan_omits_card(self):
        User.objects.create_user(
            email="noplan@test.com", password="pw", tier=self.main_tier,
        )
        self.client.login(email="noplan@test.com", password="pw")
        response = self.client.get("/")
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
