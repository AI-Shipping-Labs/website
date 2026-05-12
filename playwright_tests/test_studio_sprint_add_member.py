"""Playwright E2E tests for the sprint Add member flow (issue #444).

Covers the BDD scenarios from the spec that are best exercised in a
real browser session:

- Operator enrolls a new member and lands in the editor ready to author.
- Re-adding the same member is a silent no-op (idempotent flash).
- Operator sees a clear error when no member is picked.
- Non-staff cannot reach the Add member URL.

Server-side artefact assertions (week count, status, blank theme) live
in the Django ``TestCase`` suite in
``studio/tests/test_sprint_add_member.py`` -- per Rule 15 we don't
duplicate them here.
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


def _create_sprint(name, slug, duration_weeks=6):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name, slug=slug,
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=duration_weeks,
    )
    connection.close()
    return sprint


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestOperatorEnrollsNewMember:
    """Operator clicks Add member, picks a user, lands in the editor."""

    def test_add_member_lands_in_editor_with_empty_weeks(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "new@test.com", tier_slug="free", email_verified=True,
        )
        sprint = _create_sprint("Spring Cohort", "spring-cohort", 6)

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Step 1: navigate to the sprint detail page.
        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        # Step 2: the Add member button is visible to the LEFT of Edit sprint.
        add_btn = page.locator('[data-testid="sprint-add-member-link"]')
        add_btn.wait_for(state="visible")
        # Step 3: click Add member.
        add_btn.click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/add-member",
        )

        # The form page shows the sprint locked.
        page.locator(
            '[data-testid="add-member-sprint-locked"]'
        ).wait_for(state="visible")

        # Step 4: pick the member and submit.
        new_member_id = page.evaluate(
            """() => {
                const sel = document.querySelector('[data-testid="add-member-select"]');
                for (const o of sel.options) {
                    if (o.textContent.includes('new@test.com')) {
                        return o.value;
                    }
                }
                return null;
            }"""
        )
        assert new_member_id is not None
        page.locator(
            '[data-testid="add-member-select"]'
        ).select_option(new_member_id)
        page.locator('button[type="submit"]').click()

        # Step 5: lands on the editor URL (contains /edit/).
        page.wait_for_url(
            lambda url: "/studio/plans/" in url and url.endswith("/edit/"),
        )

        # Editor header shows new@test.com and Spring Cohort.
        page.locator(
            '[data-testid="plan-editor-header"]'
        ).wait_for(state="visible")
        header_text = page.locator(
            '[data-testid="plan-editor-header"]'
        ).inner_text()
        assert "new@test.com" in header_text
        assert "Spring Cohort" in header_text

        # 6 week cards rendered, each with empty theme placeholder.
        week_cards = page.locator('[data-testid="week-card"]')
        assert week_cards.count() == 6

        # Step 6: reload -- the 6 empty weeks persist.
        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-testid="week-card"]').count() == 6

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestReAddSameMemberIsSilentNoOp:
    """Re-adding ``member@test.com`` is idempotent."""

    def test_re_add_same_member_redirects_to_existing_plan(
        self, django_server, browser,
    ):
        from plans.models import Checkpoint, Plan, Week
        from plans.services import create_plan_for_enrollment

        _ensure_tiers()
        _clear_plans_data()
        staff = _create_staff_user("staff@test.com")
        member = _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        sprint = _create_sprint("Spring Cohort", "spring-cohort", 6)

        # Enroll the member and seed one checkpoint in Week 1.
        plan, _, _ = create_plan_for_enrollment(
            sprint=sprint, user=member, enrolled_by=staff,
        )
        week_1 = Week.objects.get(plan=plan, week_number=1)
        Checkpoint.objects.create(
            week=week_1, description="Read paper", position=0,
        )
        existing_plan_id = plan.pk
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="sprint-add-member-link"]').click()
        page.wait_for_url(
            f"{django_server}/studio/sprints/{sprint.pk}/add-member",
        )

        member_id_value = page.evaluate(
            """() => {
                const sel = document.querySelector('[data-testid="add-member-select"]');
                for (const o of sel.options) {
                    if (o.textContent.includes('member@test.com')) {
                        return o.value;
                    }
                }
                return null;
            }"""
        )
        assert member_id_value is not None
        page.locator(
            '[data-testid="add-member-select"]'
        ).select_option(member_id_value)
        page.locator('button[type="submit"]').click()

        # Lands on the SAME existing plan editor URL.
        page.wait_for_url(
            f"{django_server}/studio/plans/{existing_plan_id}/edit/",
        )

        # Week 1 still contains "Read paper" (no wipe).
        page.locator(
            '[data-testid="checkpoint-text"]:has-text("Read paper")'
        ).wait_for(state="visible")

        # Only one Plan and one SprintEnrollment exist for the pair.
        from plans.models import SprintEnrollment
        assert Plan.objects.filter(
            sprint=sprint, member=member,
        ).count() == 1
        assert SprintEnrollment.objects.filter(
            sprint=sprint, user=member,
        ).count() == 1
        connection.close()

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestOperatorMissingMemberError:
    """Submitting with no member shows a clear inline error."""

    def test_missing_member_shows_pick_a_member(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        sprint = _create_sprint("Spring Cohort", "spring-cohort", 6)

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/add-member",
            wait_until="domcontentloaded",
        )

        # Submit without picking a member. The browser would normally
        # block this via ``required``; remove the attribute so the
        # POST reaches the server and we exercise the server-side
        # error branch.
        page.evaluate(
            """() => {
                const sel = document.querySelector('[data-testid="add-member-select"]');
                if (sel) sel.removeAttribute('required');
            }"""
        )
        page.locator('button[type="submit"]').click()

        # The error banner renders with the documented copy.
        page.locator(
            '[data-testid="plan-form-error"]'
        ).wait_for(state="visible")
        error_text = page.locator(
            '[data-testid="plan-form-error"]'
        ).inner_text()
        assert "Pick a member" in error_text
        # The sprint is still locked (the form did NOT regress to
        # the standalone create-plan UI).
        page.locator(
            '[data-testid="add-member-sprint-locked"]'
        ).wait_for(state="visible")

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNonStaffCannotReachAddMember:
    """A free member cannot reach the Studio Add member URL."""

    def test_non_staff_gets_403(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        sprint = _create_sprint("Spring Cohort", "spring-cohort", 6)

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/sprints/{sprint.pk}/add-member",
            wait_until="domcontentloaded",
        )

        # Either 403 directly, or the request is bounced elsewhere
        # (e.g. a future redirect to the access-denied page); the
        # critical invariant is that the form is NOT rendered.
        assert response is not None
        # The form's distinctive testid must not be present.
        # ``count()`` returns 0 when the locator does not match.
        assert page.locator(
            '[data-testid="add-member-sprint-locked"]'
        ).count() == 0
        # Status is 4xx (403 expected, but tolerate 401/302 fallbacks).
        assert response.status >= 400 or response.status == 302
        # Specifically: 403 is the documented behaviour.
        assert response.status == 403

        context.close()
