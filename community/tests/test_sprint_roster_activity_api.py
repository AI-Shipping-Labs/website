"""Relocated staff-token sprint roster-activity API owner (#1479)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

User = get_user_model()


def _seed_api_roster():
    staff = User.objects.create_user(
        email="pw-roster-api-staff@test.com",
        password="pw",
        is_staff=True,
    )
    token = Token.objects.create(user=staff, name="pw-roster-activity")
    sprint = Sprint.objects.create(
        name="Roster API Sprint",
        slug="pw-roster-api",
        start_date=timezone.localdate() - datetime.timedelta(days=2),
        duration_weeks=4,
        status="active",
    )
    updated = User.objects.create_user(email="pw-api-updated@test.com", password="pw")
    no_plan = User.objects.create_user(email="pw-api-no-plan@test.com", password="pw")
    SprintEnrollment.objects.create(sprint=sprint, user=updated)
    SprintEnrollment.objects.create(sprint=sprint, user=no_plan)
    plan = Plan.objects.create(sprint=sprint, member=updated)
    week = Week.objects.create(plan=plan, week_number=1)
    Checkpoint.objects.create(
        week=week,
        description="Done",
        done_at=timezone.now() - datetime.timedelta(hours=1),
    )
    Checkpoint.objects.create(week=week, description="Open")
    return token.key, sprint.slug


class SprintRosterActivityApiTest(TestCase):
    """Owns staff-token filtered roster-activity reads.

    Relocated from Playwright ``test_staff_token_reads_roster_activity_api``.
    """

    def test_staff_token_reads_roster_activity_api(self):
        token_key, sprint_slug = _seed_api_roster()

        response = self.client.get(
            f"/api/sprints/{sprint_slug}/roster-activity"
            "?activity=no_update_this_week",
            HTTP_AUTHORIZATION=f"Token {token_key}",
        )
        body = response.json()
        self.assertEqual(body["sprint"]["slug"], sprint_slug)
        self.assertTrue(body["current_week"]["active"])
        self.assertEqual(body["totals"]["members"], 2)
        self.assertEqual(body["totals"]["no_update_this_week"], 1)
        self.assertEqual(
            [row["member"]["email"] for row in body["members"]],
            ["pw-api-no-plan@test.com"],
        )
        self.assertEqual(body["members"][0]["progress"]["label"], "No plan")
