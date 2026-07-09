"""Playwright coverage for structured sprint plan resources (#1043)."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
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
        name__in=["studio-plan-editor", "member-plan-editor"],
    ).delete()
    connection.close()


def _seed_resource_plan(member_email):
    from accounts.models import User
    from plans.models import Plan, Resource, Sprint, Week

    sprint = Sprint.objects.create(
        name="May 2026",
        slug="may-2026",
        # date-rot-ok: plan resource fixture; current sprint state is not under test.
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
        status="active",
    )
    member = User.objects.get(email=member_email)
    plan = Plan.objects.create(
        member=member,
        sprint=sprint,
        visibility="cohort",
    )
    Week.objects.create(plan=plan, week_number=1, position=0)
    Resource.objects.create(
        plan=plan,
        title="amr_ai repo",
        url="https://github.com/juanpprim/amr_ai",
        note="Use the **deploy** guide and [Logfire](https://logfire.pydantic.dev/)",
        position=0,
    )
    Resource.objects.create(
        plan=plan,
        title="[Deployment docs](https://docs.example.com/deploy)",
        position=1,
    )
    Resource.objects.create(
        plan=plan,
        title="Carlos's own project notes",
        position=2,
    )
    Resource.objects.create(
        plan=plan,
        title="Malicious note",
        note="Safe text <script>alert(1)</script> [bad](javascript:alert(1))",
        position=3,
    )
    connection.close()
    return plan.pk


@pytest.mark.django_db(transaction=True)
class TestSprintPlanStructuredResources:
    def test_member_uses_structured_resources_on_mobile(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        plan_pk = _seed_resource_plan("member@test.com")

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto(
            f"{django_server}/sprints/may-2026/plan/{plan_pk}",
            wait_until="domcontentloaded",
        )
        resources = page.locator('[data-testid="plan-resources"]')
        resources.wait_for(state="visible")

        repo = resources.locator('a[data-testid="plan-resource-link"]').filter(
            has_text="amr_ai repo",
        )
        assert repo.get_attribute("href") == "https://github.com/juanpprim/amr_ai"
        assert repo.get_attribute("target") == "_blank"

        body_text = resources.inner_text()
        assert "[Deployment docs](" not in body_text
        legacy = resources.locator('a[data-testid="plan-resource-link"]').filter(
            has_text="Deployment docs",
        )
        assert legacy.get_attribute("href") == "https://docs.example.com/deploy"

        assert resources.locator("strong", has_text="deploy").count() == 1
        assert (
            resources.locator('a[href="https://logfire.pydantic.dev/"]').count()
            == 1
        )
        assert "Carlos's own project notes" in body_text
        unlinked = resources.locator('[data-testid="plan-resource"]').filter(
            has_text="Carlos's own project notes",
        )
        assert unlinked.locator("a").count() == 0
        html = resources.inner_html()
        assert "<script" not in html.lower()
        assert "javascript:" not in html.lower()

        context.close()

    def test_staff_scans_structured_resources_in_studio(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user("member@test.com", tier_slug="main", email_verified=True)
        plan_pk = _seed_resource_plan("member@test.com")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/edit/",
            wait_until="domcontentloaded",
        )

        panel = page.locator('[data-testid="resources-panel"]')
        panel.wait_for(state="visible")
        assert panel.locator('[data-testid="resource-row"]').count() == 4
        assert (
            panel.locator('a[data-testid="resource-row-link"]')
            .filter(has_text="amr_ai repo")
            .get_attribute("href")
            == "https://github.com/juanpprim/amr_ai"
        )
        assert "[Deployment docs](" not in panel.inner_text()
        assert (
            panel.locator('a[data-testid="resource-row-link"]')
            .filter(has_text="Deployment docs")
            .get_attribute("href")
            == "https://docs.example.com/deploy"
        )
        unlinked = panel.locator('[data-testid="resource-row"]').filter(
            has_text="Carlos's own project notes",
        )
        assert unlinked.locator("a").count() == 0
        assert "<script" not in panel.inner_html().lower()
        assert "javascript:" not in panel.inner_html().lower()
        body_text = page.locator("body").inner_text()
        assert '"resources":' not in body_text
        assert '"note":' not in body_text
        assert "javascript:alert(1)" not in body_text
        assert page.locator("#plan-editor-data").count() == 1

        context.close()
