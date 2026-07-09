"""Playwright E2E for the carry-over action (issue #808).

A returning member carries their unfinished tasks from a prior sprint into
a new sprint plan, finished tasks are left behind, the action is idempotent
on a second click, and a first-time member sees no panel.
"""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_plans_data():
    from accounts.models import Token
    from plans.models import (
        Checkpoint,
        Deliverable,
        InterviewNote,
        NextStep,
        Plan,
        Resource,
        Sprint,
        SprintEnrollment,
        Week,
    )

    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(
        name__in=["studio-plan-editor", "member-plan-editor"]).delete()
    connection.close()


def _make_plan(member, sprint, weeks):
    from plans.models import Plan, Week

    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _seed_sprint_a_and_b(member_email):
    """Seed Sprint A with finished early weeks and unfinished later weeks.

    Returns the Sprint B plan pk.
    """
    from django.utils import timezone

    from accounts.models import User
    from plans.models import Checkpoint, Sprint

    member = User.objects.get(email=member_email)
    sprint_a = Sprint.objects.create(
        name="Sprint A", slug="sprint-a",
        # date-rot-ok: fixed ordering fixture for carry-over source sprint.
        start_date=datetime.date(2026, 1, 1), duration_weeks=6)
    sprint_b = Sprint.objects.create(
        name="Sprint B", slug="sprint-b",
        # date-rot-ok: fixed ordering fixture for carry-over target sprint.
        start_date=datetime.date(2026, 5, 1), duration_weeks=6)
    plan_a = _make_plan(member, sprint_a, 6)
    week1 = plan_a.weeks.get(week_number=1)
    Checkpoint.objects.create(
        week=week1, description="done one", position=0,
        done_at=timezone.now())
    Checkpoint.objects.create(
        week=plan_a.weeks.get(week_number=2), description="done two", position=0,
        done_at=timezone.now())
    Checkpoint.objects.create(
        week=plan_a.weeks.get(week_number=3),
        description="unfinished alpha", position=0)
    Checkpoint.objects.create(
        week=plan_a.weeks.get(week_number=3),
        description="unfinished beta", position=1)
    Checkpoint.objects.create(
        week=plan_a.weeks.get(week_number=4),
        description="unfinished gamma", position=0)
    plan_b = _make_plan(member, sprint_b, 6)
    plan_b_pk = plan_b.pk
    connection.close()
    return plan_b_pk


@pytest.mark.django_db(transaction=True)
class TestCarryOverFlow:
    @pytest.mark.core
    def test_member_carries_unfinished_work_then_idempotent(
            self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "main@test.com", tier_slug="main", email_verified=True)
        plan_b_pk = _seed_sprint_a_and_b("main@test.com")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/sprint-b/plan/{plan_b_pk}",
            wait_until="domcontentloaded")

        panel = page.locator('[data-testid="plan-carry-over-panel"]')
        panel.wait_for(state="visible")
        panel_text = panel.inner_text()
        assert "Sprint A" in panel_text
        assert "3 unfinished tasks available" in panel_text

        page.locator('[data-testid="plan-carry-over-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success message names Sprint A and the count of 3.
        messages = page.locator('[data-testid="messages-region"]').inner_text()
        assert "Carried over 3 tasks" in messages
        assert "Sprint A" in messages

        # The 3 unfinished checkpoints appear; the 2 finished do not.
        weeks_text = page.locator('[data-testid="plan-weeks"]').inner_text()
        assert "unfinished alpha" in weeks_text
        assert "unfinished beta" in weeks_text
        assert "unfinished gamma" in weeks_text
        assert "done one" not in weeks_text
        assert "done two" not in weeks_text

        # Idempotent from the member's point of view: once everything is
        # copied the panel switches to the "all caught up" state and offers
        # no further carry-over button, so a second click is impossible and
        # no duplicates can be created. Each unfinished item appears exactly
        # once in the weeks list. (Server-side re-run idempotency is
        # covered authoritatively by the Django service/view tests.)
        caught_up = page.locator('[data-testid="plan-carry-over-caught-up"]')
        caught_up.wait_for(state="visible")
        assert "All caught up" in caught_up.inner_text()
        assert page.locator(
            '[data-testid="plan-carry-over-submit"]').count() == 0
        assert weeks_text.count("unfinished alpha") == 1
        assert weeks_text.count("unfinished beta") == 1
        assert weeks_text.count("unfinished gamma") == 1

        from plans.models import Plan

        plan_b = Plan.objects.get(pk=plan_b_pk)
        assert [
            c.description
            for c in plan_b.weeks.get(week_number=1).checkpoints.all()
        ] == ["unfinished alpha", "unfinished beta"]
        assert [
            c.description
            for c in plan_b.weeks.get(week_number=2).checkpoints.all()
        ] == ["unfinished gamma"]
        assert plan_b.weeks.get(week_number=3).checkpoints.count() == 0

        context.close()

    @pytest.mark.core
    def test_first_time_member_sees_no_panel(
            self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "newmain@test.com", tier_slug="main", email_verified=True)

        from accounts.models import User
        from plans.models import Sprint

        member = User.objects.get(email="newmain@test.com")
        sprint = Sprint.objects.create(
            name="First Sprint", slug="first-sprint",
            # date-rot-ok: no-prior-plan fixture; current sprint state is not under test.
            start_date=datetime.date(2026, 5, 1), duration_weeks=4)
        plan = _make_plan(member, sprint, 4)
        plan_pk = plan.pk
        connection.close()

        context = _auth_context(browser, "newmain@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/first-sprint/plan/{plan_pk}",
            wait_until="domcontentloaded")
        page.locator('[data-testid="plan-weeks"]').wait_for(state="visible")
        assert page.locator(
            '[data-testid="plan-carry-over-panel"]').count() == 0

        context.close()
