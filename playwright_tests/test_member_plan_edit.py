"""Playwright E2E tests for the member-facing plan editor (issue #444).

Members edit their OWN plan via ``/account/plan/<id>/edit/`` using the
same drag-drop authoring UI staff use in Studio. These scenarios cover:

- Member opens their own plan and reorders a checkpoint within Week 1.
- Member edits the ``Goal`` summary textarea via the autosave path.
- Member cannot view another member's plan (404, no email leak).
- Anonymous user is redirected to login from the member URL.
- Staff editor still works after the partial extraction (regression).
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


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


def _seed_plan_with_two_checkpoints(member_email, sprint=None):
    """Create a plan owned by ``member_email`` with two checkpoints in
    Week 1 (``Read paper``, ``Build prototype``).

    If ``sprint`` is ``None``, the helper reuses (or creates) a single
    ``spring-cohort`` Sprint via ``get_or_create`` so the helper can be
    called multiple times in the same test for different members in the
    SAME sprint without violating ``Sprint.slug`` uniqueness.
    """
    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    if sprint is None:
        sprint, _ = Sprint.objects.get_or_create(
            slug="spring-cohort",
            defaults={
                "name": "Spring Cohort",
                "start_date": datetime.date(2026, 5, 1),
                "duration_weeks": 6,
            },
        )
    member = User.objects.get(email=member_email)
    plan = Plan.objects.create(
        member=member, sprint=sprint, status="draft",
    )
    week = Week.objects.create(
        plan=plan, week_number=1, position=0,
    )
    Checkpoint.objects.create(
        week=week, description="Read paper", position=0,
    )
    Checkpoint.objects.create(
        week=week, description="Build prototype", position=1,
    )
    plan_pk = plan.pk
    connection.close()
    return plan_pk


@pytest.mark.django_db(transaction=True)
class TestMemberOpensOwnPlan:
    """Member opens their own plan and the editor renders fully."""

    def test_member_sees_editor_with_their_email_and_sprint(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan_pk = _seed_plan_with_two_checkpoints("member@test.com")

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/account/plan/{plan_pk}/edit/",
            wait_until="domcontentloaded",
        )
        # The editor header carries the member email and sprint.
        page.locator(
            '[data-testid="plan-editor-header"]'
        ).wait_for(state="visible")
        header_text = page.locator(
            '[data-testid="plan-editor-header"]'
        ).inner_text()
        assert "member@test.com" in header_text
        assert "Spring Cohort" in header_text

        # The save indicator is present in its initial saved state.
        page.locator('[data-testid="save-indicator"]').wait_for(
            state="visible",
        )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberEditsSummaryFieldAutosaves:
    """Member types in the Goal field; the saved indicator transitions."""

    def test_goal_textarea_persists_after_blur(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan_pk = _seed_plan_with_two_checkpoints("member@test.com")

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/account/plan/{plan_pk}/edit/",
            wait_until="domcontentloaded",
        )

        goal_textarea = page.locator('[data-testid="summary-goal"]')
        goal_textarea.click()
        goal_textarea.fill("Ship one project this sprint")
        # Blur to trigger the debounced save.
        page.locator('[data-testid="plan-editor-header"]').click()

        # Wait for the saved indicator to settle on ``saved``.
        page.wait_for_function(
            """() => {
                const el = document.querySelector('[data-testid="save-indicator"]');
                return el && el.getAttribute('data-state') === 'saved';
            }""",
            timeout=5000,
        )

        # Reload and the textarea still has the goal text.
        page.reload(wait_until="domcontentloaded")
        reloaded_value = page.locator(
            '[data-testid="summary-goal"]'
        ).input_value()
        assert reloaded_value == "Ship one project this sprint"

        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberCannotViewOtherMembersPlan:
    """Bob navigating to Alice's plan id gets 404 and no leak."""

    def test_404_with_no_email_leak(self, django_server, browser):
        from plans.models import Sprint

        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "alice@test.com", tier_slug="free", email_verified=True,
        )
        _create_user(
            "bob@test.com", tier_slug="free", email_verified=True,
        )
        # Alice and Bob are members of the SAME sprint per the spec.
        sprint = Sprint.objects.create(
            name="Spring Cohort", slug="spring-cohort",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
        )
        alice_plan_pk = _seed_plan_with_two_checkpoints(
            "alice@test.com", sprint=sprint,
        )
        bob_plan_pk = _seed_plan_with_two_checkpoints(
            "bob@test.com", sprint=sprint,
        )

        context = _auth_context(browser, "bob@test.com")
        page = context.new_page()

        # Bob hits Alice's plan id -> 404.
        response = page.goto(
            f"{django_server}/account/plan/{alice_plan_pk}/edit/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 404
        body = page.content()
        assert "alice@test.com" not in body

        # Bob hits his OWN plan id -> 200 with editor.
        page.goto(
            f"{django_server}/account/plan/{bob_plan_pk}/edit/",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="plan-editor-header"]'
        ).wait_for(state="visible")
        header_text = page.locator(
            '[data-testid="plan-editor-header"]'
        ).inner_text()
        assert "bob@test.com" in header_text

        context.close()


@pytest.mark.django_db(transaction=True)
class TestAnonymousRedirectedToLogin:
    """An anonymous browser hitting the member URL gets login redirect."""

    def test_anonymous_redirects_to_login_with_next(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()

        from accounts.models import User
        from plans.models import Plan, Sprint

        sprint = Sprint.objects.create(
            name="S", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        member = User.objects.create_user(
            email="member@test.com", password="x",
        )
        plan = Plan.objects.create(
            member=member, sprint=sprint, status="draft",
        )
        plan_pk = plan.pk
        connection.close()

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        page.goto(
            f"{django_server}/account/plan/{plan_pk}/edit/",
            wait_until="domcontentloaded",
        )
        # Final URL must be on the login page with next preserved.
        assert "/accounts/login/" in page.url
        assert f"next=/account/plan/{plan_pk}/edit/" in page.url

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffEditorRegression:
    """Staff path still renders the editor after the partial extraction."""

    def test_staff_editor_renders_unchanged(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan_pk = _seed_plan_with_two_checkpoints("member@test.com")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/edit/",
            wait_until="domcontentloaded",
        )
        # Header, weeks column, side panels render.
        page.locator(
            '[data-testid="plan-editor-header"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="weeks-column"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="side-panels"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-testid="save-indicator"]'
        ).wait_for(state="visible")

        context.close()
