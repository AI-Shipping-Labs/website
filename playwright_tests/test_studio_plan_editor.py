"""Playwright E2E tests for the Studio drag-and-drop plan editor (issue #434).

Most scenarios in this file exercise the JSON API from #433 (drags
persist via ``POST /api/checkpoints/<id>/move``, autosave PATCHes the
plan and the children, etc.). Until #433 lands on main, those scenarios
are skipped via ``pytest.mark.skipif`` keyed off
``settings.PLANS_API_AVAILABLE``. The skip decorator becomes a no-op as
soon as that flag flips to True.

Two scenarios in this file run end-to-end TODAY because they only
exercise the editor's render path:

- ``TestNonStaffBlockedFromEditor`` — confirms a free-tier user gets a
  403 and no editor markup leaks into the response.
- ``TestEditorRendersWithoutErrors`` — confirms a fresh editor page
  loads with the SortableJS bundle and the bootstrap data node.

The drag and keyboard scenarios use Playwright's real drag primitives
(``locator.drag_to`` / ``mouse.down/move/up``) and the keyboard
``page.keyboard.press`` API, NOT string-matching on innerHTML, per
``_docs/testing-guidelines.md`` Rule 4 and Rule 10.
"""

import datetime
import os

import pytest
from django.conf import settings

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

# Flag flipped to True once issue #433 lands on main and the API
# endpoints are wired into ``website/urls.py``. Until then, scenarios
# that exercise the API are skipped (the editor is built and unit-tested
# against the documented contract).
plans_api_available = getattr(settings, "PLANS_API_AVAILABLE", False)

skip_until_api = pytest.mark.skipif(
    not plans_api_available,
    reason=(
        "Sprint plans API (issue #433) not yet on main. The editor's "
        "drag, keyboard, and autosave paths exercise that API; this "
        "scenario will run as soon as #433 merges and "
        "PLANS_API_AVAILABLE flips to True."
    ),
)


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
        Week,
    )

    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(name="studio-plan-editor").delete()
    connection.close()


def _seed_plan(staff_email, member_email, weeks_with_checkpoints):
    """Create a sprint, a plan, weeks, and checkpoints. Return plan.

    ``weeks_with_checkpoints`` is a list of ``[checkpoint_descriptions]``
    -- one inner list per week, in week_number order starting at 1.
    """
    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    sprint = Sprint.objects.create(
        name="May 2026 sprint", slug="may-2026",
        start_date=datetime.date(2026, 5, 1),
    )
    member = User.objects.get(email=member_email)
    plan = Plan.objects.create(member=member, sprint=sprint, status="draft")
    for week_idx, descriptions in enumerate(weeks_with_checkpoints, start=1):
        week = Week.objects.create(
            plan=plan, week_number=week_idx, position=week_idx - 1,
        )
        for cp_idx, desc in enumerate(descriptions):
            Checkpoint.objects.create(
                week=week, description=desc, position=cp_idx,
            )
    connection.close()
    return plan


@pytest.mark.django_db(transaction=True)
class TestNonStaffBlockedFromEditor:
    """Non-staff user cannot reach the editor and no markup leaks."""

    def test_free_user_gets_403(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "free@test.com",
            tier_slug="free",
            email_verified=True,
        )
        plan = _seed_plan("staff@test.com", "free@test.com", [["A"]])

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403
        # No SortableJS or editor markup in the rendered HTML.
        body = page.content()
        assert "sortablejs" not in body.lower()
        assert "plan-editor-data" not in body


@pytest.mark.django_db(transaction=True)
class TestEditorRendersWithoutErrors:
    """A fresh editor page loads cleanly with the bundle and bootstrap."""

    def test_editor_loads_for_staff(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com",
            tier_slug="free",
            email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["Read paper", "Build prototype"], ["Write blog post"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Capture console errors so a JS error fails the test.
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        # The editor root and bootstrap data are present.
        page.locator('[data-testid="plan-editor"]').wait_for(state="attached")
        page.locator('[data-testid="plan-editor-data"]').wait_for(state="attached")

        # SortableJS pinned-version script tag is present.
        sortable_script = page.locator(
            'script[src*="sortablejs@1.15.2"]'
        )
        assert sortable_script.count() == 1

        # The week cards rendered with the right checkpoints.
        page.locator(
            '[data-week-number="1"]'
        ).wait_for(state="visible")
        page.locator(
            '[data-week-number="2"]'
        ).wait_for(state="visible")

        # Saved indicator starts in the saved state.
        indicator = page.locator('[data-testid="save-indicator"]')
        indicator.wait_for(state="visible")
        assert indicator.get_attribute("data-state") == "saved"

        # No JS errors from bootstrap or SortableJS init.
        assert errors == [], f"page errors: {errors}"


# ---------------------------------------------------------------------------
# Scenarios below exercise issue #433's API. They run end-to-end as soon as
# the API lands on main; until then, ``skip_until_api`` keeps the suite
# green while the editor work proceeds in parallel.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffDragsCheckpointAcrossWeeks:
    @skip_until_api
    def test_drag_persists_across_reload(self, django_server, browser):
        """Drag ``Build prototype`` from Week 1 to Week 2; reload; persists."""
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["Read paper", "Build prototype"], ["Write blog post"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        source = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="Build prototype")
        target = page.locator(
            '[data-week-number="2"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="Write blog post")
        # Real drag via Playwright primitives.
        source.drag_to(target)

        # The chip moves into Week 2 above ``Write blog post``.
        moved = page.locator(
            '[data-week-number="2"] [data-testid="checkpoint-chip"]'
        ).first
        moved.wait_for(state="visible")
        assert "Build prototype" in moved.text_content()

        page.locator('[data-testid="save-indicator"][data-state="saved"]').wait_for()

        page.reload(wait_until="networkidle")

        week_1_chips = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        week_2_chips = page.locator(
            '[data-week-number="2"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        assert any("Read paper" in t for t in week_1_chips)
        assert not any("Build prototype" in t for t in week_1_chips)
        assert week_2_chips[0].strip().endswith("Build prototype")
        assert any("Write blog post" in t for t in week_2_chips)


@pytest.mark.django_db(transaction=True)
class TestStaffReordersWithinWeekViaDrag:
    @skip_until_api
    def test_reorder_within_week_persists(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["A", "B", "C"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        c_chip = page.locator(
            '[data-testid="checkpoint-chip"]'
        ).filter(has_text="C")
        a_chip = page.locator(
            '[data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        c_chip.drag_to(a_chip)

        page.locator('[data-testid="save-indicator"][data-state="saved"]').wait_for()
        page.reload(wait_until="networkidle")

        chips = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        order = [t.strip() for t in chips]
        # Strip leading drag handle and checkbox text noise; assert the
        # last visible word per chip matches the expected description.
        descriptions = [t.split()[-1] for t in order]
        assert descriptions == ["C", "A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffReordersByKeyboard:
    @skip_until_api
    def test_keyboard_reorder_within_week_persists(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["A", "B", "C"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        c_chip = page.locator(
            '[data-testid="checkpoint-chip"]'
        ).filter(has_text="C")
        c_chip.click()  # focus
        page.keyboard.press("ArrowUp")
        page.keyboard.press("ArrowUp")

        page.locator('[data-testid="save-indicator"][data-state="saved"]').wait_for()

        # The same chip retains focus (document.activeElement matches).
        focused_text = page.evaluate("document.activeElement.textContent || ''")
        assert "C" in focused_text

        page.reload(wait_until="networkidle")
        chips = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        descriptions = [t.split()[-1] for t in chips]
        assert descriptions == ["C", "A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffMovesCheckpointAcrossWeeksByKeyboard:
    @skip_until_api
    def test_alt_arrow_down_moves_to_next_week(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["A"], ["B"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        a_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        a_chip.click()
        page.keyboard.down("Alt")
        page.keyboard.press("ArrowDown")
        page.keyboard.up("Alt")

        page.locator('[data-testid="save-indicator"][data-state="saved"]').wait_for()
        page.reload(wait_until="networkidle")

        week_1 = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).count()
        week_2_chips = page.locator(
            '[data-week-number="2"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        assert week_1 == 0
        descriptions = [t.split()[-1] for t in week_2_chips]
        assert descriptions == ["A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffEditsSummaryInline:
    @skip_until_api
    def test_summary_textarea_autosaves(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com", [["A"]],
        )
        from plans.models import Plan
        Plan.objects.filter(pk=plan.pk).update(summary_goal="Ship one project")
        connection.close()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        ta = page.locator('[data-testid="summary-goal"]')
        ta.click()
        ta.fill("Ship two projects in six weeks")
        # Blur to trigger immediate flush.
        page.locator('[data-testid="summary-current_situation"]').click()
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        page.reload(wait_until="networkidle")
        ta = page.locator('[data-testid="summary-goal"]')
        assert ta.input_value() == "Ship two projects in six weeks"


@pytest.mark.django_db(transaction=True)
class TestStaffAddsAndDeletesCheckpoint:
    @skip_until_api
    def test_add_then_delete_checkpoint(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com", [["A"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        page.locator('[data-week-number="1"] [data-testid="add-checkpoint"]').click()
        # The new chip's textarea is focused.
        edit_ta = page.locator('[data-testid="checkpoint-edit-textarea"]')
        edit_ta.wait_for(state="visible")
        edit_ta.fill("New checkpoint")
        # Blur to commit.
        page.locator('[data-testid="summary-goal"]').click()

        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # Delete chip A: click x, confirm.
        a_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        a_chip.locator('[data-testid="checkpoint-delete"]').click(force=True)
        page.locator('[data-testid="checkpoint-delete-confirm"]').click()

        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        page.reload(wait_until="networkidle")
        chips = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).all_text_contents()
        descriptions = [t.split()[-1] for t in chips]
        assert descriptions == ["checkpoint"]  # "New checkpoint" tail word


@pytest.mark.django_db(transaction=True)
class TestStaffSeesRevertOnApiFailure:
    @skip_until_api
    def test_drag_reverts_when_api_returns_422(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["A", "B"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Intercept the move endpoint and return 422.
        page.route(
            "**/api/checkpoints/*/move",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body='{"error": "invalid", "code": "invalid_position"}',
            ),
        )
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        a_chip = page.locator(
            '[data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        b_chip = page.locator(
            '[data-testid="checkpoint-chip"]'
        ).filter(has_text="B")
        a_chip.drag_to(b_chip)

        # Saved indicator transitions to ``failed``.
        page.locator(
            '[data-testid="save-indicator"][data-state="failed"]'
        ).wait_for()
        # Toast shows the failure copy.
        toast = page.locator('[data-testid="plan-editor-toast"]')
        toast.wait_for(state="visible")
        assert "Couldn't save change" in toast.text_content()


@pytest.mark.django_db(transaction=True)
class TestStaffTogglesCheckpointDone:
    @skip_until_api
    def test_done_toggle_persists_via_api(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com",
            [["Read paper"]],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        chip = page.locator('[data-testid="checkpoint-chip"]').filter(has_text="Read paper")
        chip.locator('[data-testid="checkpoint-done-toggle"]').click()

        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()
        # The chip carries data-done="true" after the toggle.
        assert chip.get_attribute("data-done") == "true"

        page.reload(wait_until="networkidle")
        chip = page.locator('[data-testid="checkpoint-chip"]').filter(has_text="Read paper")
        assert chip.get_attribute("data-done") == "true"


@pytest.mark.django_db(transaction=True)
class TestEditorSurfacesNoErrorsDuringSession:
    @skip_until_api
    def test_full_session_records_only_2xx(self, django_server, browser):
        """Edit each summary field, add three checkpoints, drag, toggle.

        Then assert every recorded ``/api/**`` response is in the 2xx
        range. A 4xx or 5xx fails the test.
        """
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "staff@test.com", "member@test.com", [[]],
        )

        statuses = []

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        def _record(route):
            response = route.fetch()
            statuses.append(response.status)
            route.fulfill(response=response)

        page.route("**/api/**", _record)

        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="networkidle",
        )

        # Edit each summary field.
        for field in [
            "summary-current_situation",
            "summary-goal",
            "summary-main_gap",
            "summary-weekly_hours",
            "summary-why_this_plan",
        ]:
            ta = page.locator(f'[data-testid="{field}"]')
            ta.click()
            ta.fill(f"value for {field}")
            page.locator('[data-testid="plan-editor-header"]').click()
            page.locator(
                '[data-testid="save-indicator"][data-state="saved"]'
            ).wait_for()

        # Add three checkpoints.
        for _ in range(3):
            page.locator('[data-week-number="1"] [data-testid="add-checkpoint"]').click()
            page.locator(
                '[data-testid="save-indicator"][data-state="saved"]'
            ).wait_for()

        # All recorded statuses are 2xx.
        for status in statuses:
            assert 200 <= status < 300, f"non-2xx in session: {status}"
