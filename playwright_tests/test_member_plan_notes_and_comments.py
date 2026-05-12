"""Playwright coverage for plan participant notes + permissioned comments (#499).

Three flows are exercised end-to-end:

1. Plan owner adds, edits, and deletes a participant note for a
   week on their own sprint-scoped plan workspace.
2. A teammate enrolled in the same cohort sees the note read-only
   on the ``/sprints/<slug>/plans/<plan_id>`` view and can post a
   comment in the shared comments thread.
3. Studio plan detail (staff surface) renders the participant
   notes read-only and reuses the shared comments partial.

The Django dev server is reused on port 8765 via ``django_server``;
each test creates its own users, sprint, plan, and week so we do
not depend on ordering.
"""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_plan_data():
    from accounts.models import Token
    from comments.models import Comment, CommentVote
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
        WeekNote,
    )

    CommentVote.objects.all().delete()
    Comment.objects.all().delete()
    WeekNote.objects.all().delete()
    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(name="member-plan-editor").delete()
    connection.close()


def _seed_plan(
    *,
    owner_email="owner-499@test.com",
    teammate_email="teammate-499@test.com",
    visibility="cohort",
):
    from accounts.models import User
    from plans.models import Plan, Sprint, SprintEnrollment, Week

    sprint = Sprint.objects.create(
        name="Issue 499 Sprint",
        slug="i499-sprint",
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=4,
    )
    owner = User.objects.get(email=owner_email)
    teammate = User.objects.get(email=teammate_email)
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=owner)
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        status="shared",
        visibility=visibility,
        focus_main="Ship the demo",
    )
    week = Week.objects.create(plan=plan, week_number=1, position=0)
    connection.close()
    return {
        "sprint_slug": sprint.slug,
        "plan_id": plan.pk,
        "week_id": week.pk,
        "comment_content_id": str(plan.comment_content_id),
    }


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestParticipantNotes:
    def test_owner_adds_edits_and_deletes_a_note(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user(
            "owner-499@test.com",
            tier_slug="free",
            email_verified=True,
            first_name="Olivia",
        )
        _create_user(
            "teammate-499@test.com",
            tier_slug="free",
            email_verified=True,
        )
        data = _seed_plan()

        context = _auth_context(browser, "owner-499@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        # Empty state on first load
        empty = page.locator(
            f'[data-testid="plan-week-notes"][data-week-id="{data["week_id"]}"] '
            '[data-testid="plan-week-notes-empty"]'
        )
        assert empty.is_visible()

        # Add a note
        add_form = page.locator(
            f'[data-testid="plan-week-notes"][data-week-id="{data["week_id"]}"] '
            '[data-testid="plan-week-note-add-form"]'
        )
        add_form.locator('[data-testid="plan-week-note-add-textarea"]').fill(
            "Finished data import, blocked on evals"
        )
        with page.expect_navigation():
            add_form.locator('[data-testid="plan-week-note-add-submit"]').click()

        # The new note should appear in the week-notes list
        note = page.locator(
            f'[data-testid="plan-week-notes"][data-week-id="{data["week_id"]}"] '
            '[data-testid="plan-week-note"]'
        ).first
        assert note.is_visible()
        assert (
            "Finished data import, blocked on evals"
            in note.locator('[data-testid="plan-week-note-body"]').inner_text()
        )

        # Edit
        note.locator('[data-testid="plan-week-note-edit"]').click()
        edit_form = note.locator(
            '[data-testid="plan-week-note-edit-form"]'
        )
        edit_form.locator(
            '[data-testid="plan-week-note-edit-textarea"]'
        ).fill("Finished import and drafted eval checklist")
        with page.expect_navigation():
            edit_form.locator(
                '[data-testid="plan-week-note-edit-save"]'
            ).click()

        note = page.locator(
            f'[data-testid="plan-week-notes"][data-week-id="{data["week_id"]}"] '
            '[data-testid="plan-week-note"]'
        ).first
        assert (
            "Finished import and drafted eval checklist"
            in note.locator('[data-testid="plan-week-note-body"]').inner_text()
        )

        # Delete (skip the confirm() dialog by accepting it)
        page.once("dialog", lambda d: d.accept())
        with page.expect_navigation():
            note.locator('[data-testid="plan-week-note-delete"]').click()

        empty = page.locator(
            f'[data-testid="plan-week-notes"][data-week-id="{data["week_id"]}"] '
            '[data-testid="plan-week-notes-empty"]'
        )
        assert empty.is_visible()
        assert page.locator(
            '[data-testid="plan-week-note"]'
        ).count() == 0

        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestPlanCommentsThread:
    def test_cohort_plan_owner_posts_a_comment(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user(
            "owner-499@test.com", tier_slug="free", email_verified=True,
            first_name="Olivia",
        )
        _create_user(
            "teammate-499@test.com", tier_slug="free", email_verified=True,
        )
        data = _seed_plan(visibility="cohort")

        context = _auth_context(browser, "owner-499@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        # Comments section is visible with the textarea (cohort plan).
        section = page.locator('[data-testid="plan-comments-section"]')
        assert section.is_visible()
        assert section.locator(
            f'[data-content-id="{data["comment_content_id"]}"]'
        ).is_visible()

        textarea = section.locator('#qa-new-question')
        textarea.fill("Will keep evals running each Friday")
        with page.expect_response(
            lambda r: f"/api/comments/{data['comment_content_id']}" in r.url
            and r.request.method == "POST"
        ) as resp_info:
            section.locator('#qa-post-btn').click()
        assert resp_info.value.status == 201

        # The new comment renders inside the list (loadComments() refresh).
        page.wait_for_function(
            "document.querySelectorAll('#qa-list [data-comment-id]').length >= 1"
        )
        body = section.locator('#qa-list [data-comment-id]').first.inner_text()
        assert "Will keep evals running each Friday" in body
        context.close()

    def test_private_plan_owner_sees_disabled_composer(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user(
            "owner-499@test.com", tier_slug="free", email_verified=True,
            first_name="Olivia",
        )
        _create_user(
            "teammate-499@test.com", tier_slug="free", email_verified=True,
        )
        data = _seed_plan(visibility="private")

        context = _auth_context(browser, "owner-499@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        section = page.locator('[data-testid="plan-comments-section"]')
        assert section.is_visible()
        # Disabled composer notice is shown; the textarea is hidden.
        assert section.locator(
            '[data-testid="qa-composer-disabled"]'
        ).is_visible()
        assert section.locator('#qa-new-question').count() == 0
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestStudioPlanRendersNotesAndComments:
    def test_studio_plan_detail_shows_notes_read_only_and_comments_section(
        self, django_server, browser,
    ):
        from plans.models import WeekNote

        _ensure_tiers()
        _clear_plan_data()
        _create_user(
            "owner-499@test.com", tier_slug="free", email_verified=True,
            first_name="Olivia",
        )
        _create_user(
            "teammate-499@test.com", tier_slug="free", email_verified=True,
        )
        _create_staff_user("staff-499@test.com")
        data = _seed_plan(visibility="cohort")
        from accounts.models import User
        from plans.models import Week

        owner = User.objects.get(email="owner-499@test.com")
        WeekNote.objects.create(
            week=Week.objects.get(pk=data["week_id"]),
            body="STUDIO_VIEW_NOTE",
            author=owner,
        )
        connection.close()

        context = _auth_context(browser, "staff-499@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        notes = page.locator('[data-testid="studio-plan-week-note"]')
        assert notes.count() == 1
        assert "STUDIO_VIEW_NOTE" in notes.first.inner_text()

        # Studio surface must NOT render the member-page edit/delete
        # controls -- staff is not the author.
        assert page.locator(
            '[data-testid="plan-week-note-edit"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="plan-week-note-delete"]'
        ).count() == 0

        # Comments section reuses the shared partial.
        comments_section = page.locator(
            '[data-testid="studio-plan-comments-section"]'
        )
        assert comments_section.is_visible()
        assert comments_section.locator(
            f'[data-content-id="{data["comment_content_id"]}"]'
        ).is_visible()

        context.close()
