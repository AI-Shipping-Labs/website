"""Playwright E2E tests for the Studio drag-and-drop plan editor (issue #434).

Every scenario runs end-to-end against the live sprint plans API
shipped in #433 (drags persist via ``POST /api/checkpoints/<id>/move``,
autosave PATCHes the plan and its children, etc.).

The drag and keyboard scenarios use Playwright's real drag primitives
(``locator.drag_to`` / ``mouse.down/move/up``) and the keyboard
``page.keyboard.press`` API, NOT string-matching on innerHTML, per
``_docs/testing-guidelines.md`` Rule 4 and Rule 10.
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


def _drag_chip(page, source_chip, target_chip, drop="before"):
    """Drag ``source_chip`` onto ``target_chip`` via SortableJS.

    The editor's SortableJS instance only listens for mousedown on the
    ``.plan-editor-drag-handle`` glyph; mousedown anywhere else on the
    chip (text span, checkbox, delete button) is ignored. We therefore
    grab the handle's bounding box and synthesize a real
    ``mouse.down/move/up`` sequence.

    SortableJS reorders the list progressively as the cursor crosses
    each intermediate chip's midline -- a too-coarse mousemove jumps
    over those midlines and the dragged chip lands in the wrong slot,
    while a too-fine mousemove crosses the TARGET's midline as well
    and SortableJS commits the wrong before/after slot.

    The compromise is two phases:

    1. ``approach``: walk from source to a point just OUTSIDE the
       target chip's bounding box, using ~1 step per pixel so every
       intermediate chip's midline is crossed. This handles same-list
       reorders that need to pass through chips between source and
       target.
    2. ``commit``: a fast 2-step move into the target's near edge.
       This enters the target's bounding box without crossing its
       midline, so SortableJS commits the requested before/after
       slot.

    Both source and target are scrolled into view first so that
    ``bounding_box()`` returns coordinates inside the viewport (the
    page is taller than 720 px when there are 2+ weeks).
    """
    if drop not in ("before", "after"):
        raise ValueError(f"unknown drop position: {drop!r}")

    source_chip.scroll_into_view_if_needed()
    handle = source_chip.locator('.plan-editor-drag-handle')
    sb = handle.bounding_box()
    sx = sb["x"] + sb["width"] / 2
    sy = sb["y"] + sb["height"] / 2

    page.mouse.move(sx, sy)
    page.mouse.down()
    # Two short nudges so SortableJS leaves its idle state before the
    # long traverse to the target.
    page.mouse.move(sx + 2, sy + 2, steps=3)
    page.mouse.move(sx + 10, sy + 10, steps=5)

    target_chip.scroll_into_view_if_needed()
    tb = target_chip.bounding_box()
    tx = tb["x"] + tb["width"] / 2
    if drop == "before":
        approach_y = tb["y"] - 2
        commit_y = tb["y"] + 5
    else:
        approach_y = tb["y"] + tb["height"] + 2
        commit_y = tb["y"] + tb["height"] - 5

    # Phase 1: dense traversal up to (but not into) the target.
    distance = max(abs(approach_y - sy), abs(tx - sx))
    steps = max(int(distance), 30)
    page.mouse.move(tx, approach_y, steps=steps)
    # Phase 2: discrete move into the target's near edge, committing
    # the before/after slot.
    page.mouse.move(tx, commit_y, steps=2)
    page.mouse.up()


def _checkpoint_descriptions(page, week_number):
    """Return the description text from each checkpoint chip in a week.

    Reads the inner ``[data-testid="checkpoint-text"]`` element
    explicitly so the assertion is not polluted by the drag handle's
    ``::`` glyph or the delete button's ``x`` label.
    """
    return (
        page.locator(
            f'[data-week-number="{week_number}"] '
            f'[data-testid="checkpoint-chip"] '
            f'[data-testid="checkpoint-text"]'
        )
        .all_text_contents()
    )


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
# Scenarios below exercise issue #433's plans API end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffDragsCheckpointAcrossWeeks:
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
        _drag_chip(page, source, target)

        # The drag triggers POST /api/checkpoints/<id>/move; wait for
        # the indicator to settle on saved before asserting on the DOM.
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # The chip is now in Week 2 above ``Write blog post``.
        week_2_descriptions = _checkpoint_descriptions(page, 2)
        assert week_2_descriptions == ["Build prototype", "Write blog post"]
        assert "Build prototype" not in _checkpoint_descriptions(page, 1)

        # And the move persisted -- reload and re-check from the server.
        page.reload(wait_until="networkidle")
        assert _checkpoint_descriptions(page, 1) == ["Read paper"]
        assert _checkpoint_descriptions(page, 2) == [
            "Build prototype", "Write blog post",
        ]


@pytest.mark.django_db(transaction=True)
class TestStaffReordersWithinWeekViaDrag:
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
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="C")
        a_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        _drag_chip(page, c_chip, a_chip)

        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()
        page.reload(wait_until="networkidle")

        # Read the inner ``checkpoint-text`` rather than the whole chip
        # so the assertion isn't polluted by ``::`` (drag handle) or
        # ``x`` (delete button) glyphs.
        assert _checkpoint_descriptions(page, 1) == ["C", "A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffReordersByKeyboard:
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

        # Use ``focus()`` rather than ``click()``: a click lands on the
        # inner ``checkpoint-text`` span, which has its own click
        # handler that enters inline-edit mode (sets
        # ``chip.dataset.editing = 'true'``). Once editing is true the
        # chip's keydown handler returns early, so ArrowUp does
        # nothing. The chip's ``tabindex="0"`` makes ``focus()`` a
        # supported and direct way to give it keyboard focus without
        # entering edit mode.
        c_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="C")
        c_chip.scroll_into_view_if_needed()
        c_chip.focus()
        page.keyboard.press("ArrowUp")
        # Wait for the indicator to settle so the optimistic DOM
        # reorder is reconciled with the server's
        # ``destination_week.checkpoint_ids`` envelope before we send
        # the next keypress. Without this, two presses can race and
        # the second one finds the chip already at the target index.
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()
        page.keyboard.press("ArrowUp")
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # The same chip retains focus across both reorders -- the
        # editor's ``renumberCheckpointsFromIds`` rebinder is supposed
        # to preserve ``document.activeElement``.
        focused_id = page.evaluate(
            "document.activeElement && document.activeElement.dataset"
            " ? document.activeElement.dataset.checkpointId : null"
        )
        c_id = c_chip.get_attribute("data-checkpoint-id")
        assert focused_id == c_id, (
            f"focus drifted off the moved chip: {focused_id!r} vs {c_id!r}"
        )

        page.reload(wait_until="networkidle")
        assert _checkpoint_descriptions(page, 1) == ["C", "A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffMovesCheckpointAcrossWeeksByKeyboard:
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

        # ``focus()`` instead of ``click()`` -- click on the chip lands
        # on the ``checkpoint-text`` span and enters inline-edit mode,
        # which would short-circuit the keydown handler.
        a_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        a_chip.scroll_into_view_if_needed()
        a_chip.focus()
        page.keyboard.down("Alt")
        page.keyboard.press("ArrowDown")
        page.keyboard.up("Alt")

        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()
        page.reload(wait_until="networkidle")

        # Week 1 is empty; week 2 now has [A, B] in that order.
        week_1_count = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).count()
        assert week_1_count == 0
        assert _checkpoint_descriptions(page, 2) == ["A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffEditsSummaryInline:
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

        # Add a checkpoint: click ``+ Add checkpoint``, fill the inline
        # textarea that auto-opens, blur to commit. Two API calls run
        # under the hood -- POST /api/weeks/<id>/checkpoints (create
        # empty), then PATCH /api/checkpoints/<id> with the text.
        page.locator(
            '[data-week-number="1"] [data-testid="add-checkpoint"]'
        ).click()
        edit_ta = page.locator('[data-testid="checkpoint-edit-textarea"]')
        edit_ta.wait_for(state="visible")
        edit_ta.fill("New checkpoint")
        page.locator('[data-testid="summary-goal"]').click()  # blur
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # Both chips now exist with the right text content.
        assert _checkpoint_descriptions(page, 1) == ["A", "New checkpoint"]

        # Delete chip A. The delete button has ``opacity-0`` until
        # hover, so ``force=True`` is required.
        a_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        a_chip.locator('[data-testid="checkpoint-delete"]').click(force=True)
        page.locator('[data-testid="checkpoint-delete-confirm"]').click()
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # Reload and verify the survivor is the new checkpoint, not A.
        page.reload(wait_until="networkidle")
        assert _checkpoint_descriptions(page, 1) == ["New checkpoint"]


@pytest.mark.django_db(transaction=True)
class TestStaffSeesRevertOnApiFailure:
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
        # Intercept the move endpoint and return 422 so the editor's
        # ``moveCheckpoint`` revert path runs end-to-end.
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
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="A")
        b_chip = page.locator(
            '[data-week-number="1"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="B")
        # Drag A onto B via the drag handle so SortableJS actually
        # picks up the gesture (the handle filter rejects mousedown
        # outside ``.plan-editor-drag-handle``).
        _drag_chip(page, a_chip, b_chip)

        # The editor flips the indicator to ``failed`` and surfaces a
        # toast. We assert on the indicator first because the toast is
        # only shown briefly (autohide).
        page.locator(
            '[data-testid="save-indicator"][data-state="failed"]'
        ).wait_for()
        toast = page.locator('[data-testid="plan-editor-toast"]')
        toast.wait_for(state="visible")
        assert "Couldn't save change" in toast.text_content()
        # And the optimistic reorder was reverted: A is back in front
        # of B. The snapshot is restored from
        # ``restoreCheckpointSnapshot`` once the 422 returns.
        assert _checkpoint_descriptions(page, 1) == ["A", "B"]


@pytest.mark.django_db(transaction=True)
class TestStaffTogglesCheckpointDone:
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
