"""Playwright E2E tests for carry-forward checkpoints (issue #458).

Each week card except the final one renders a ``Move incomplete to
Week N+1`` button. Clicking it walks the source week's incomplete
checkpoints in display order and fires sequential
``POST /api/checkpoints/<id>/move`` calls so the destination week
ends with those chips at the top in the same relative order.

Why Playwright: the click handler runs in JS, mutates the DOM
optimistically, calls the API, and reconciles from the response
envelope. None of that is reproducible in a Django ``TestCase``; the
template render contract (button-on-non-final-week-only) lives in
``studio/tests/test_plan_editor_carry_forward.py``.

We reuse helpers from the parent suite (``_clear_plans_data``,
``_seed_plan``, ``_checkpoint_descriptions``) to keep the fixture
shape identical across editor scenarios.
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


def _seed_plan(member_email, weeks_with_checkpoints):
    """Create a plan with the given week / checkpoint structure.

    ``weeks_with_checkpoints`` is a list whose i-th entry is a list of
    ``(description, done)`` tuples for week i+1, in checkpoint
    position order.
    """
    from django.utils import timezone

    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    sprint = Sprint.objects.create(
        name="May 2026 sprint", slug="may-2026",
        start_date=datetime.date(2026, 5, 1),
    )
    member = User.objects.get(email=member_email)
    plan = Plan.objects.create(member=member, sprint=sprint, status="draft")
    for week_idx, items in enumerate(weeks_with_checkpoints, start=1):
        week = Week.objects.create(
            plan=plan, week_number=week_idx, position=week_idx - 1,
        )
        for cp_idx, item in enumerate(items):
            description, done = item
            Checkpoint.objects.create(
                week=week, description=description, position=cp_idx,
                done_at=timezone.now() if done else None,
            )
    connection.close()
    return plan


def _checkpoint_descriptions(page, week_number):
    """Read the inner checkpoint-text spans for a week, in display order."""
    return (
        page.locator(
            f'[data-week-number="{week_number}"] '
            f'[data-testid="checkpoint-chip"] '
            f'[data-testid="checkpoint-text"]'
        )
        .all_text_contents()
    )


@pytest.mark.django_db(transaction=True)
class TestCarryForwardMovesIncompleteCheckpointsToNextWeek:
    """The headline scenario from the issue body.

    Three Week 1 checkpoints, middle one complete; click ``Move
    incomplete``; the first and third land at the top of Week 2 in
    that order, the completed middle stays in Week 1, and a reload
    confirms the same order persisted to the API.
    """

    def test_move_incomplete_persists_and_preserves_order(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                # Week 1: A (open), B (done), C (open)
                [("A", False), ("B", True), ("C", False)],
                # Week 2: empty
                [],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        # Click the carry-forward button on Week 1.
        btn = page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        )
        btn.wait_for(state="visible")
        btn.click()

        # Wait for the indicator to settle (sequential POSTs).
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # A and C are now at the TOP of Week 2 in source order; B
        # stays in Week 1.
        assert _checkpoint_descriptions(page, 1) == ["B"]
        assert _checkpoint_descriptions(page, 2) == ["A", "C"]

        # Reload — the order survived the round trip.
        page.reload(wait_until="domcontentloaded")
        assert _checkpoint_descriptions(page, 1) == ["B"]
        assert _checkpoint_descriptions(page, 2) == ["A", "C"]


@pytest.mark.django_db(transaction=True)
class TestCarryForwardPrependsAboveExistingDestinationItems:
    """When the destination already has chips, moves go ABOVE them.

    The AC says ``Moved checkpoints are prepended to the beginning of
    the next week, preserving their relative order from the source
    week.`` so an existing Week 2 checkpoint must remain after the
    incoming chips, not before.
    """

    def test_moves_prepended_above_existing_destination_items(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                # Week 1: open, open
                [("first", False), ("second", False)],
                # Week 2: existing item
                [("existing", False)],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        ).click()
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # Reload to read the persisted order from the API.
        page.reload(wait_until="domcontentloaded")
        assert _checkpoint_descriptions(page, 1) == []
        assert _checkpoint_descriptions(page, 2) == [
            "first", "second", "existing",
        ]


@pytest.mark.django_db(transaction=True)
class TestCarryForwardRevertsOnApiFailure:
    """A 422 from the move endpoint reverts the optimistic UI.

    AC: ``The UI reverts and shows the existing save-failure feedback
    if any API move fails.``
    """

    def test_revert_on_422(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                [("alpha", False), ("beta", False)],
                [],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Reject the very first move call so the batch aborts immediately.
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
            wait_until="domcontentloaded",
        )

        page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        ).click()

        # Indicator flips to failed and the existing toast shows.
        page.locator(
            '[data-testid="save-indicator"][data-state="failed"]'
        ).wait_for()
        toast = page.locator('[data-testid="plan-editor-toast"]')
        toast.wait_for(state="visible")
        assert "Couldn't save change" in toast.text_content()

        # And the optimistic move was reverted: chips back in Week 1.
        assert _checkpoint_descriptions(page, 1) == ["alpha", "beta"]
        assert _checkpoint_descriptions(page, 2) == []


@pytest.mark.django_db(transaction=True)
class TestCarryForwardEmptyWeekHintsUpdate:
    """The empty-week hint toggles correctly after a carry-forward.

    AC: ``Empty-week hints update after items move out of or into a
    week.`` The hint is a server-rendered ``<p>`` with ``hidden``
    toggled by the editor JS; we verify the post-move visibility.
    """

    def test_hint_toggles_for_source_and_destination(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                # Week 1 has a single open item.
                [("only", False)],
                # Week 2 starts empty.
                [],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        # Before: Week 2 hint visible, Week 1 hint hidden.
        w1_hint = page.locator(
            '[data-week-number="1"] [data-testid="empty-week-hint"]'
        )
        w2_hint = page.locator(
            '[data-week-number="2"] [data-testid="empty-week-hint"]'
        )
        assert "hidden" in (w1_hint.get_attribute("class") or "")
        assert "hidden" not in (w2_hint.get_attribute("class") or "")

        page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        ).click()
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        # After: Week 1 now empty (hint visible), Week 2 has the item
        # (hint hidden).
        assert "hidden" not in (w1_hint.get_attribute("class") or "")
        assert "hidden" in (w2_hint.get_attribute("class") or "")


@pytest.mark.django_db(transaction=True)
class TestCarryForwardThenDragBackPreservesEditing:
    """Regression: drag still works on a chip that was carried forward.

    The issue's Playwright scenario asks for: drag one carried
    checkpoint back to Week 1 and verify the API persists the change.
    This guards against the carry-forward handler accidentally
    detaching SortableJS bindings from the chips it moved.
    """

    def test_drag_carried_chip_back_persists(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                [("X", False)],
                [],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        ).click()
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()
        # X is now in Week 2.
        assert _checkpoint_descriptions(page, 2) == ["X"]

        # Move X back to Week 1 via the keyboard cross-week shortcut
        # (Alt+ArrowUp). Drag-via-mouse is exercised in the parent
        # editor suite; here we just confirm the chip's keydown
        # handler still fires after the carry-forward, i.e. the
        # binding survived.
        chip = page.locator(
            '[data-week-number="2"] [data-testid="checkpoint-chip"]'
        ).filter(has_text="X")
        chip.scroll_into_view_if_needed()
        chip.focus()
        page.keyboard.down("Alt")
        page.keyboard.press("ArrowUp")
        page.keyboard.up("Alt")
        page.locator(
            '[data-testid="save-indicator"][data-state="saved"]'
        ).wait_for()

        page.reload(wait_until="domcontentloaded")
        assert _checkpoint_descriptions(page, 1) == ["X"]
        assert _checkpoint_descriptions(page, 2) == []


@pytest.mark.django_db(transaction=True)
class TestCarryForwardNoButtonOnFinalWeek:
    """The final week card has no carry-forward button in the live DOM.

    The template-render test asserts the same thing server-side, but
    a Playwright check guards against a JS bootstrap step accidentally
    injecting a button after the page loads.
    """

    def test_final_week_has_no_button(self, django_server, browser):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user(
            "member@test.com", tier_slug="free", email_verified=True,
        )
        plan = _seed_plan(
            "member@test.com",
            [
                [("a", False)],
                [("b", False)],
            ],
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{plan.pk}/edit/",
            wait_until="domcontentloaded",
        )

        # Wait for week cards to attach so a count of zero on the
        # final week isn't a "page hasn't loaded yet" false negative.
        page.locator('[data-week-number="2"]').wait_for(state="visible")
        final_buttons = page.locator(
            '[data-week-number="2"] '
            '[data-testid="move-incomplete-to-next-week"]'
        )
        assert final_buttons.count() == 0
        # And week 1 (the non-final) does have one.
        first_buttons = page.locator(
            '[data-week-number="1"] '
            '[data-testid="move-incomplete-to-next-week"]'
        )
        assert first_buttons.count() == 1
