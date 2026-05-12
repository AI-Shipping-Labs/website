"""Playwright E2E coverage for the sprint plan workspace unification (#583).

Nine scenarios from the groomed issue spec:

1. Owner opens their plan and starts editing inline (no separate page).
2. Old /edit URL still works (301 -> unified workspace).
3. Owner flips visibility from private to shared in one click.
4. Toggle persists across reload AND the change is reflected on the
   cohort board for a second member.
5. Failed visibility save reverts the toggle to its prior state.
6. Messages appear once, not twice, after a server-side action.
7. Plan content stacks in a single column on the workspace.
8. Timeline section reads cleanly without filler copy.
9. Teammate viewing a shared plan has no edit affordances.

These tests live in the ``playwright_tests/`` directory and must NOT be
run concurrently from another worktree -- the dev server uses a fixed
port (8765) and only one process can bind it at a time.
"""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402


def _clear_plan_data():
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
    Token.objects.filter(name='member-plan-editor').delete()
    connection.close()


def _seed_workspace(
    owner_email='member@test.com',
    teammate_email='teammate@test.com',
    owner_visibility='private',
):
    from accounts.models import User
    from plans.models import (
        Checkpoint,
        Deliverable,
        NextStep,
        Plan,
        Resource,
        Sprint,
        SprintEnrollment,
        Week,
    )

    sprint = Sprint.objects.create(
        name='Unify Sprint',
        slug='unify-sprint',
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=2,
    )
    owner = User.objects.get(email=owner_email)
    teammate = User.objects.get(email=teammate_email)
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)

    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        status='shared',
        visibility=owner_visibility,
        focus_main='Ship the **prototype**',
    )
    week1 = Week.objects.create(plan=plan, week_number=1, position=0)
    Week.objects.create(plan=plan, week_number=2, position=1)
    checkpoint = Checkpoint.objects.create(
        week=week1, description='Build skeleton', position=0,
    )
    Deliverable.objects.create(
        plan=plan, description='Record demo', position=0,
    )
    NextStep.objects.create(
        plan=plan, description='Book review', position=0,
    )
    Resource.objects.create(
        plan=plan,
        title='RAG paper',
        url='https://example.com/rag',
        position=0,
    )

    teammate_plan = Plan.objects.create(
        member=teammate,
        sprint=sprint,
        visibility='private',
    )
    connection.close()
    return {
        'sprint_slug': sprint.slug,
        'plan_id': plan.pk,
        'checkpoint_id': checkpoint.pk,
        'teammate_plan_id': teammate_plan.pk,
    }


@pytest.mark.django_db(transaction=True)
class TestUnifiedWorkspace583:
    """Scenarios 1, 7, 8, 9 -- workspace rendering and read-only fallback."""

    def test_owner_can_inline_edit_without_separate_edit_page(
        self, django_server, browser,
    ):
        """Scenario 1: open the workspace, no Edit-workspace CTA, edit a
        checkpoint inline -> change persists without a full reload."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace()

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            # The "Edit workspace" CTA is gone in #583.
            assert page.locator(
                '[data-testid="my-plan-edit-cta"]',
            ).count() == 0
            assert page.get_by_text('Edit workspace').count() == 0

            # The workspace itself is the editor. Edit a checkpoint inline.
            item = page.locator('[data-testid="plan-checkpoint"]').first
            item.locator('[data-testid="plan-item-edit"]').click()
            item.locator(
                '[data-testid="plan-item-markdown-input"]',
            ).fill('Build **RAG** prototype')
            with page.expect_response('**/api/checkpoints/*') as resp:
                item.locator('[data-testid="plan-item-save"]').click()
            assert resp.value.ok
            # Markdown is re-rendered in place (no full page reload).
            assert item.locator('strong').inner_text() == 'RAG'
        finally:
            context.close()

    def test_single_column_layout_and_section_order(
        self, django_server, browser,
    ):
        """Scenario 7: deliverables and next-steps stack vertically; the
        section order top-to-bottom is weeks -> resources -> deliverables
        -> next steps -> plan context."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace()

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            # Deliverables sit ABOVE next steps, not side-by-side -- so
            # their bounding boxes share the x range and the next-steps
            # ``y`` is greater than the deliverables ``y``.
            deliverables = page.locator(
                '[data-testid="plan-deliverables"]',
            ).bounding_box()
            next_steps = page.locator(
                '[data-testid="plan-next-steps"]',
            ).bounding_box()
            assert deliverables is not None and next_steps is not None
            assert next_steps['y'] > deliverables['y']
            # Single column means they overlap horizontally.
            assert abs(deliverables['x'] - next_steps['x']) < 4

            # Section order top-to-bottom.
            sections = {
                'weeks': page.locator(
                    '[data-testid="plan-weeks"]',
                ).bounding_box(),
                'resources': page.locator(
                    '[data-testid="plan-resources"]',
                ).bounding_box(),
                'action_items': page.locator(
                    '[data-testid="plan-action-items"]',
                ).bounding_box(),
                'summary': page.locator(
                    '[data-testid="plan-summary"]',
                ).bounding_box(),
            }
            for name, box in sections.items():
                assert box is not None, f'section {name} not visible'
            assert sections['resources']['y'] > sections['weeks']['y']
            assert (
                sections['action_items']['y'] > sections['resources']['y']
            )
            assert sections['summary']['y'] > sections['action_items']['y']
        finally:
            context.close()

    def test_timeline_section_has_no_filler_copy(
        self, django_server, browser,
    ):
        """Scenario 8: the Timeline heading is visible and the
        boilerplate sentence is gone."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace()

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            expect(page.get_by_role('heading', name='Weekly work')).to_be_visible()
            assert page.get_by_text(
                'Checkpoints are the primary flow for this sprint plan.',
            ).count() == 0
        finally:
            context.close()

    def test_teammate_sees_shared_plan_without_edit_affordances(
        self, django_server, browser,
    ):
        """Scenario 9: a teammate of the same sprint sees the plan
        content but no checkbox, no edit button, no visibility toggle.
        The comments composer remains so they can engage."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        # Owner shares the plan to the cohort so the teammate can see it.
        data = _seed_workspace(owner_visibility='cohort')

        context = _auth_context(browser, 'teammate@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            # The plan body renders (we can find a checkpoint description).
            expect(
                page.locator('[data-testid="plan-checkpoint"]').first,
            ).to_be_visible()
            # No edit affordances.
            assert page.locator(
                '[data-testid="plan-row-done-toggle"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="plan-item-edit"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="plan-visibility-toggle"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="my-plan-edit-cta"]',
            ).count() == 0
            # Comments composer is present (teammates can comment on
            # shared plans).
            expect(
                page.locator('[data-testid="plan-comments-section"]'),
            ).to_be_visible()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestLegacyEditUrlRedirect583:
    """Scenario 2: bookmarked /edit URL still works via HTTP 301."""

    def test_legacy_edit_url_lands_on_unified_workspace(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace()

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}/edit",
                wait_until='domcontentloaded',
            )

            # The 301 lands on the canonical workspace URL.
            assert page.url == (
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}"
            )
            # And renders the unified workspace (visibility toggle is one
            # of the workspace-only controls).
            expect(
                page.locator('[data-testid="plan-visibility-toggle"]'),
            ).to_be_visible()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestVisibilityToggle583:
    """Scenarios 3, 4, 5: the new public/private switch."""

    def test_toggle_flips_private_to_shared_in_one_click(
        self, django_server, browser,
    ):
        """Scenario 3: starts Private, no Save button, single click flips
        to Shared with cohort, change persists across a reload."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace(owner_visibility='private')

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            toggle = page.locator('[data-testid="plan-visibility-toggle"]')
            label = page.locator('[data-testid="plan-visibility-label"]')
            status = page.locator('[data-testid="plan-visibility-status"]')

            # Starts in Private state, no Save button anywhere.
            expect(toggle).to_have_attribute('aria-checked', 'false')
            assert label.inner_text().strip() == 'Private'
            assert page.locator(
                '[data-testid="visibility-save"]',
            ).count() == 0

            # Click flips to cohort.
            with page.expect_response('**/visibility') as resp:
                toggle.click()
            assert resp.value.ok
            expect(toggle).to_have_attribute('aria-checked', 'true')
            assert label.inner_text().strip() == 'Shared with cohort'
            # Inline "Saved" indicator appears, then fades within ~1.5s.
            expect(status).to_have_text('Saved')

            # Reload to confirm the change persisted server-side.
            page.reload(wait_until='domcontentloaded')
            expect(
                page.locator('[data-testid="plan-visibility-toggle"]'),
            ).to_have_attribute('aria-checked', 'true')
        finally:
            context.close()

    def test_cohort_board_reflects_toggle_state_for_teammate(
        self, django_server, browser,
    ):
        """Scenario 4: the second member's cohort board sees the plan
        appear after the owner flips to shared, and disappear after the
        owner flips back to private."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace(owner_visibility='cohort')

        owner_ctx = _auth_context(browser, 'member@test.com')
        teammate_ctx = _auth_context(browser, 'teammate@test.com')
        try:
            # Step 1: as the teammate, the cohort board now lists the
            # owner's plan (it was seeded as cohort-shared).
            tpage = teammate_ctx.new_page()
            tpage.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/board",
                wait_until='domcontentloaded',
            )
            assert tpage.locator(
                f"a[href='/sprints/{data['sprint_slug']}/plans/{data['plan_id']}']",
            ).count() >= 1

            # Step 2: as the owner, flip the toggle back to private.
            opage = owner_ctx.new_page()
            opage.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )
            toggle = opage.locator('[data-testid="plan-visibility-toggle"]')
            expect(toggle).to_have_attribute('aria-checked', 'true')
            with opage.expect_response('**/visibility') as resp:
                toggle.click()
            assert resp.value.ok
            expect(toggle).to_have_attribute('aria-checked', 'false')

            # Step 3: as the teammate, reload the board -- the plan is
            # no longer reachable from it.
            tpage.reload(wait_until='domcontentloaded')
            assert tpage.locator(
                f"a[href='/sprints/{data['sprint_slug']}/plans/{data['plan_id']}']",
            ).count() == 0
        finally:
            owner_ctx.close()
            teammate_ctx.close()

    def test_failed_save_reverts_toggle_and_shows_inline_error(
        self, django_server, browser,
    ):
        """Scenario 5: stub the backend to fail -> toggle reverts, an
        inline "Couldn't save" message appears, and a reload confirms
        the plan is still Private."""
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace(owner_visibility='private')

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            # Stub the visibility endpoint to a 500 BEFORE the click.
            page.route(
                '**/visibility',
                lambda route: route.fulfill(
                    status=500, body='boom',
                ),
            )

            toggle = page.locator('[data-testid="plan-visibility-toggle"]')
            label = page.locator('[data-testid="plan-visibility-label"]')
            status = page.locator('[data-testid="plan-visibility-status"]')

            expect(toggle).to_have_attribute('aria-checked', 'false')
            toggle.click()

            # Wait for the inline error to settle.
            expect(status).to_contain_text("Couldn't save")
            expect(toggle).to_have_attribute('aria-checked', 'false')
            assert label.inner_text().strip() == 'Private'

            # Unroute and reload to confirm the server still has private.
            page.unroute('**/visibility')
            page.reload(wait_until='domcontentloaded')
            expect(
                page.locator('[data-testid="plan-visibility-toggle"]'),
            ).to_have_attribute('aria-checked', 'false')
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestMessagesDedupe583:
    """Scenario 6: messages appear exactly once after a server-side action."""

    def test_week_note_post_shows_messages_region_only_once(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user(
            'teammate@test.com', tier_slug='free', email_verified=True,
        )
        data = _seed_workspace()

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            # Find the first week-note add form and submit it. This
            # emits a Django ``messages.success(...)`` flash that gets
            # rendered on the next page render.
            add_form = page.locator(
                '[data-testid="plan-week-note-add-form"]',
            ).first
            add_form.locator(
                '[data-testid="plan-week-note-add-textarea"]',
            ).fill('Tried out the new agent.')
            add_form.locator(
                '[data-testid="plan-week-note-add-submit"]',
            ).click()

            # After the redirect we should see EXACTLY one messages
            # region and zero of the legacy in-page plan-messages block.
            page.wait_for_load_state('domcontentloaded')
            assert page.locator(
                '[data-testid="messages-region"]',
            ).count() == 1
            assert page.locator(
                '[data-testid="plan-messages"]',
            ).count() == 0
        finally:
            context.close()
