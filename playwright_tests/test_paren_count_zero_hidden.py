"""Playwright E2E tests for hiding zero-count suffixes in the UI
(issue #597).

Covers the eight scenarios in the spec:

1. Course unit with zero questions: heading reads ``Questions & Answers``
   with no trailing ``(0)``.
2. Posting the first question makes the ``(1)`` suffix appear.
3. Personal plan with no comments: heading reads ``Comments`` with no
   trailing ``(0)``.
4. Plan with three existing comments: heading reads ``Comments (3)``;
   posting one more updates to ``Comments (4)``.
5. Public workshop page with no Q&A: anonymous visitor sees
   ``Questions & Answers`` heading with no ``(0)`` and the sign-in CTA.
6. Staff user-import result page: zero warnings hides the ``(0)``;
   non-zero shows ``Warnings (N)``.
7. Staff bulk sprint enrollment results: only the buckets with non-zero
   counts show ``(N)``; the empty buckets show the label cleanly.
8. Staff CRM list filter chips: ``Active (N)`` and ``All (N)`` show
   counts; ``Archived`` shows the label only when zero.

Usage:
    uv run pytest playwright_tests/test_paren_count_zero_hidden.py -v
"""

import datetime
import os
import uuid

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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _wipe_comments_courses_workshops():
    from comments.models import Comment, CommentVote
    from content.models import Course, Module, Unit, Workshop, WorkshopPage

    CommentVote.objects.all().delete()
    Comment.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _wipe_plans():
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


def _wipe_crm():
    from crm.models import CRMExperiment, CRMRecord
    CRMExperiment.objects.all().delete()
    CRMRecord.objects.all().delete()
    connection.close()


def _create_open_course_unit(
    course_slug="paren-course",
    module_slug="m1",
    unit_slug="u1",
):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Paren Course", slug=course_slug, status="published",
    )
    module = Module.objects.create(
        course=course, title="Module 1", slug=module_slug, sort_order=1,
    )
    unit = Unit.objects.create(
        module=module,
        title="Unit One",
        slug=unit_slug,
        sort_order=1,
        is_preview=True,
        content_id=uuid.uuid4(),
        body="Unit body",
    )
    connection.close()
    return course, module, unit


def _create_open_workshop_page(slug="paren-ws"):
    from django.utils.text import slugify

    from content.models import (
        Instructor,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    workshop = Workshop.objects.create(
        slug=slug,
        title="Paren Workshop",
        date=datetime.date(2026, 4, 21),
        status="published",
        landing_required_level=0,
        pages_required_level=0,  # open to anonymous visitors
        recording_required_level=20,
        description="Workshop description.",
    )
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify("Alexey")[:200] or "ix",
        defaults={"name": "Alexey", "status": "published"},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop, instructor=instructor, defaults={"position": 0},
    )
    page = WorkshopPage.objects.create(
        workshop=workshop, slug="welcome", title="Welcome",
        sort_order=1, body="# Welcome",
        content_id=uuid.uuid4(),
    )
    connection.close()
    return workshop, page


def _seed_plan(
    *,
    owner_email="owner-597@test.com",
    teammate_email="teammate-597@test.com",
    visibility="cohort",
):
    from accounts.models import User
    from plans.models import Plan, Sprint, SprintEnrollment, Week

    sprint = Sprint.objects.create(
        name="Issue 597 Sprint",
        slug="i597-sprint",
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
    Week.objects.create(plan=plan, week_number=1, position=0)
    connection.close()
    return {
        "sprint_slug": sprint.slug,
        "plan_id": plan.pk,
        "comment_content_id": str(plan.comment_content_id),
        "owner": owner,
        "teammate": teammate,
    }


# ----------------------------------------------------------------------
# Scenario 1 + 2: Course unit with zero questions, then post one.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseUnitZeroAndOneCount:
    def test_zero_questions_hides_paren_then_first_question_shows_one(
        self, browser, django_server,
    ):
        _wipe_comments_courses_workshops()
        _create_open_course_unit()
        _create_user(
            "reader-597@test.com", tier_slug="basic", first_name="Rea",
        )

        ctx = _auth_context(browser, "reader-597@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/courses/paren-course/m1/u1",
            wait_until="networkidle",
        )

        # The Q&A section is visible.
        assert page.locator("#qa-section").is_visible()

        # The heading text is present, but the "(0)" wrapper is hidden.
        heading = page.locator("#qa-section h2")
        heading_text = heading.inner_text()
        assert "Questions & Answers" in heading_text
        assert "(0)" not in heading_text
        assert "()" not in heading_text

        # The wrapper element exists in the DOM but is hidden.
        wrapper = page.locator("#qa-count-wrapper")
        assert wrapper.count() == 1
        assert wrapper.is_hidden()

        # The composer is visible — user can post.
        page.locator("#qa-new-question").fill(
            "How do I install the deps?",
        )
        page.locator("#qa-post-btn").click()

        # After posting, the wrapper unhides and shows " (1)".
        page.wait_for_function(
            "document.getElementById('qa-count-wrapper') && "
            "!document.getElementById('qa-count-wrapper').hasAttribute('hidden') && "
            "document.getElementById('qa-count').textContent === '1'",
            timeout=5000,
        )
        # The H2 is a flex container with gap-2; inner_text() inserts a
        # newline between flex children. Assert on the count wrapper +
        # count element directly so the test is robust to layout tweaks.
        heading_text = page.locator("#qa-section h2").inner_text()
        assert "Questions & Answers" in heading_text
        wrapper = page.locator("#qa-count-wrapper")
        assert wrapper.is_visible()
        assert page.locator("#qa-count").text_content() == "1"

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Personal plan with zero comments — heading is clean.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPlanWithZeroComments:
    def test_plan_owner_sees_comments_label_without_paren_zero(
        self, browser, django_server,
    ):
        _wipe_plans()
        _create_user(
            "owner-597@test.com", tier_slug="free",
            email_verified=True, first_name="Olivia",
        )
        _create_user(
            "teammate-597@test.com", tier_slug="free",
            email_verified=True,
        )
        data = _seed_plan(visibility="cohort")

        ctx = _auth_context(browser, "owner-597@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        section = page.locator("#qa-section")
        assert section.is_visible()
        heading_text = page.locator("#qa-section h2").inner_text()
        assert "Comments" in heading_text
        assert "(0)" not in heading_text
        assert "()" not in heading_text

        # Wrapper present in DOM but hidden.
        assert page.locator("#qa-count-wrapper").is_hidden()
        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Plan with 3 comments shows (3); posting one yields (4).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPlanWithExistingCommentsShowsCount:
    def test_three_comments_show_paren_three_then_post_makes_four(
        self, browser, django_server,
    ):
        _wipe_plans()
        _create_user(
            "owner-597@test.com", tier_slug="free",
            email_verified=True, first_name="Olivia",
        )
        _create_user(
            "teammate-597@test.com", tier_slug="free",
            email_verified=True, first_name="Tia",
        )
        data = _seed_plan(visibility="cohort")

        from comments.models import Comment
        for i in range(3):
            Comment.objects.create(
                content_id=data["comment_content_id"],
                user=data["teammate"],
                body=f"Existing comment {i}",
            )
        connection.close()

        ctx = _auth_context(browser, "owner-597@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="networkidle",
        )

        # Wait for JS to load comments and update count to 3.
        page.wait_for_function(
            "document.getElementById('qa-count') && "
            "document.getElementById('qa-count').textContent === '3' && "
            "!document.getElementById('qa-count-wrapper').hasAttribute('hidden')",
            timeout=5000,
        )
        # The H2 is a flex container with gap-2; inner_text() inserts a
        # newline between flex children. Assert on the count wrapper +
        # count element directly so the test is robust to layout tweaks.
        heading_text = page.locator("#qa-section h2").inner_text()
        assert "Comments" in heading_text
        assert page.locator("#qa-count-wrapper").is_visible()
        assert page.locator("#qa-count").text_content() == "3"

        # Post a new comment.
        page.locator("#qa-new-question").fill("Fourth comment")
        page.locator("#qa-post-btn").click()

        page.wait_for_function(
            "document.getElementById('qa-count').textContent === '4'",
            timeout=5000,
        )
        heading_text = page.locator("#qa-section h2").inner_text()
        assert "Comments" in heading_text
        assert page.locator("#qa-count-wrapper").is_visible()
        assert page.locator("#qa-count").text_content() == "4"

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Anonymous visitor on open workshop page sees clean heading.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopPageAnonymousNoQA:
    def test_anonymous_workshop_page_no_paren_zero(
        self, page, django_server,
    ):
        _wipe_comments_courses_workshops()
        _create_open_workshop_page(slug="paren-ws")

        page.goto(
            f"{django_server}/workshops/paren-ws/tutorial/welcome",
            wait_until="domcontentloaded",
        )

        assert page.locator("#qa-section").is_visible()
        heading_text = page.locator("#qa-section h2").inner_text()
        assert "Questions & Answers" in heading_text
        assert "(0)" not in heading_text
        assert "()" not in heading_text

        # Sign-in CTA is visible (anonymous can't post).
        assert page.locator(
            "#qa-section a[href='/accounts/login/']"
        ).is_visible()
        assert page.locator("#qa-new-question").count() == 0


# ----------------------------------------------------------------------
# Scenario 6: Staff user-import result with zero / non-zero warnings.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffUserImportResult:
    def test_zero_warnings_hides_paren_zero_then_two_warnings_show_paren_two(
        self, browser, django_server, tmp_path,
    ):
        _create_staff_user("paren-staff-597@test.com")

        ctx = _auth_context(browser, "paren-staff-597@test.com")
        page = ctx.new_page()

        # Run 1: a single clean row, zero warnings.
        # Drive the import wizard end to end.
        page.goto(
            f"{django_server}/studio/users/import/",
            wait_until="domcontentloaded",
        )

        clean_csv = tmp_path / "clean.csv"
        clean_csv.write_text(
            "email\nclean-user-597@test.com\n", encoding="utf-8",
        )
        page.locator('input[type="file"]').set_input_files(str(clean_csv))
        # Submit the upload form (first submit on page).
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")

        # Now we're on the confirm page. Submit to actually import.
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")

        body = page.content()
        # Heading text never reads "Warnings (0)".
        assert "Warnings (0)" not in body
        assert "Warnings ()" not in body

        # Run 2: a CSV with two malformed rows that produce warnings.
        page.goto(
            f"{django_server}/studio/users/import/",
            wait_until="domcontentloaded",
        )
        bad_csv = tmp_path / "warn.csv"
        # Two rows missing the @, which the importer treats as malformed.
        bad_csv.write_text(
            "email\nnotanemail\nstillbad\n", encoding="utf-8",
        )
        page.locator('input[type="file"]').set_input_files(str(bad_csv))
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")

        body = page.content()
        # We expect a non-zero warnings count to appear with " (N)".
        # The exact N depends on the importer; we only require that the
        # rendered heading is "Warnings (X)" for some X >= 1, never
        # "Warnings (0)".
        assert "Warnings (0)" not in body
        assert "Warnings (" in body  # paren count is shown

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Staff bulk sprint enroll — only non-zero buckets get (N).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffSprintBulkEnrollResults:
    def test_only_enrolled_bucket_shows_paren_count(
        self, browser, django_server,
    ):
        from accounts.models import User
        from plans.models import Sprint
        # Reset.
        Sprint.objects.filter(slug="paren-sprint-597").delete()
        User.objects.filter(
            email__in=["m1-597@test.com", "m2-597@test.com",
                       "m3-597@test.com"],
        ).delete()
        connection.close()

        _create_staff_user("paren-sprint-staff@test.com")
        _create_user("m1-597@test.com", tier_slug="basic")
        _create_user("m2-597@test.com", tier_slug="basic")
        _create_user("m3-597@test.com", tier_slug="basic")

        sprint = Sprint.objects.create(
            name="Paren Sprint 597",
            slug="paren-sprint-597",
            start_date=datetime.date(2026, 6, 1),
        )
        sprint_pk = sprint.pk
        connection.close()

        ctx = _auth_context(browser, "paren-sprint-staff@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{sprint_pk}/enroll",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="bulk-enroll-emails"]').fill(
            "m1-597@test.com\nm2-597@test.com\nm3-597@test.com",
        )
        page.locator('[data-testid="bulk-enroll-submit"]').click()
        page.wait_for_load_state("networkidle")

        enrolled = page.locator(
            '[data-testid="bulk-enroll-result-enrolled"] h3',
        )
        assert enrolled.count() == 1
        # "Enrolled (3)" — paren count present because count > 0.
        enrolled_text = enrolled.inner_text()
        assert "Enrolled (3)" in enrolled_text

        # The other three buckets have zero entries — labels present, no
        # "(0)" suffix anywhere on those headings.
        for testid, label in (
            ("bulk-enroll-result-already", "Already enrolled"),
            ("bulk-enroll-result-under-tier", "Under-tier warning"),
            ("bulk-enroll-result-unknown", "Unknown emails"),
        ):
            heading = page.locator(f'[data-testid="{testid}"] h3')
            assert heading.count() == 1
            text = heading.inner_text()
            assert label in text, (
                f"expected {label!r} in heading for {testid}, got {text!r}"
            )
            assert "(0)" not in text, (
                f"unexpected '(0)' in heading for {testid}: {text!r}"
            )
            assert "()" not in text

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 8: Staff CRM list filter chips.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffCrmListChips:
    def test_archived_chip_has_no_paren_zero(
        self, browser, django_server,
    ):
        from accounts.models import User

        _wipe_crm()
        # Make sure prior runs don't leave stray users.
        User.objects.filter(
            email__in=[
                "crm-staff-597@test.com",
                "crm-active-1@test.com",
                "crm-active-2@test.com",
            ],
        ).delete()
        connection.close()

        staff = _create_staff_user("crm-staff-597@test.com")
        m1 = _create_user("crm-active-1@test.com", tier_slug="basic")
        m2 = _create_user("crm-active-2@test.com", tier_slug="basic")

        from crm.models import CRMRecord
        CRMRecord.objects.create(
            user=m1, created_by=staff, status="active",
        )
        CRMRecord.objects.create(
            user=m2, created_by=staff, status="active",
        )
        connection.close()

        ctx = _auth_context(browser, "crm-staff-597@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )

        active_chip = page.locator('[data-testid="crm-filter-active"]')
        archived_chip = page.locator('[data-testid="crm-filter-archived"]')
        all_chip = page.locator('[data-testid="crm-filter-all"]')

        assert "Active (2)" in active_chip.inner_text()
        assert "All (2)" in all_chip.inner_text()
        archived_text = archived_chip.inner_text()
        assert "Archived" in archived_text
        assert "(0)" not in archived_text
        assert "()" not in archived_text

        ctx.close()
