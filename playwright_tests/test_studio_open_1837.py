"""Browser coverage for Open in Studio and homework privacy (issue #1837).

Server-rendered hrefs also have Django tests. This file is the browser
proof for the issue's navigation scenarios and for the submissions
privacy toggle, which is localStorage plus a DOM update without reload.
"""

import os
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

STAFF_EMAIL = "studio-open-1837@test.com"


def _close():
    connection.close()


def _open_studio(page, base, path):
    page.goto(f"{base}{path}", wait_until="domcontentloaded")
    button = page.locator('[data-testid="studio-edit-button"]')
    assert button.count() == 1
    label = button.inner_text()
    assert "Open in Studio" in label
    assert "Edit in Studio" not in label
    return button


def _course(slug):
    from content.models import Course

    course = Course.objects.create(
        title=slug, slug=slug, status="published", required_level=0,
    )
    _close()
    return course


def _email_privacy(page):
    return page.locator('[data-testid="homework-submission-student"]').evaluate(
        """el => ({
            text: el.textContent,
            filter: getComputedStyle(el).filter,
            userSelect: getComputedStyle(el).userSelect,
        })"""
    )


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_opens_studio_from_a_workshop(django_server, browser):
    from content.models import Workshop

    _create_staff_user(email=STAFF_EMAIL)
    workshop = Workshop.objects.create(
        slug="open-studio-workshop-1837",
        title="Open Studio Workshop",
        description="A published workshop.",
        date=timezone.localdate(),
        status="published",
        landing_required_level=0,
    )
    _close()
    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    button = _open_studio(page, django_server, workshop.get_absolute_url())
    assert button.get_attribute("href") == workshop.get_studio_edit_url()
    button.click()
    page.wait_for_url(f"**{workshop.get_studio_edit_url()}", timeout=10000)
    assert page.url.endswith(workshop.get_studio_edit_url())
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_anonymous_course_pages_hide_the_studio_control(django_server, page):
    course = _course("open-studio-anon-1837")
    page.goto(f"{django_server}/courses/{course.slug}", wait_until="domcontentloaded")
    assert page.locator('[data-testid="studio-edit-button"]').count() == 0
    assert "Open in Studio" not in page.content()
    page.goto(
        f"{django_server}/courses/{course.slug}/home/homework",
        wait_until="domcontentloaded",
    )
    assert page.locator('[data-testid="studio-edit-button"]').count() == 0


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_reviews_homework_submissions_from_the_course(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Homework, Submission

    _create_staff_user(email=STAFF_EMAIL)
    from content.models import Course

    course = Course.objects.create(
        title="Review course", slug="open-studio-review-1837",
        status="published", required_level=0,
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name="Current",
        start_date=today - timedelta(days=3),
        end_date=today + timedelta(days=20),
    )
    first = Homework.objects.create(
        cohort=cohort, slug="first", title="First homework", content_id=uuid.uuid4(),
    )
    second = Homework.objects.create(
        cohort=cohort, slug="second", title="Second homework", content_id=uuid.uuid4(),
    )
    one = _create_user("learner-one-1837@test.com")
    two = _create_user("learner-two-1837@test.com")
    enrollment_one = CohortEnrollment.objects.create(cohort=cohort, user=one)
    enrollment_two = CohortEnrollment.objects.create(cohort=cohort, user=two)
    Submission.objects.create(homework=first, student=one, enrollment=enrollment_one)
    Submission.objects.create(homework=second, student=one, enrollment=enrollment_one)
    Submission.objects.create(homework=second, student=two, enrollment=enrollment_two)
    _close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    button = _open_studio(page, django_server, f"/courses/{course.slug}")
    button.click()
    page.wait_for_url(f"**/studio/courses/{course.pk}/homeworks", timeout=10000)
    assert "First homework" in page.content()
    assert "Second homework" in page.content()
    counts = page.locator('[data-testid="homework-submission-count"]').all_inner_texts()
    assert "1" in counts
    assert "2" in counts
    row = page.locator('[data-testid="homework-row"]', has_text="First homework")
    row.get_by_role("link", name="View submissions").click()
    page.wait_for_url(f"**/studio/homeworks/{first.pk}/submissions", timeout=10000)
    assert "learner-one-1837@test.com" in page.locator(
        '[data-testid="homework-submission-student"]',
    ).inner_text()
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_opens_the_section_studio_destinations(django_server, browser):
    from content.models import Module

    _create_staff_user(email=STAFF_EMAIL)
    from content.models import Course

    course = Course.objects.create(
        title="Sections", slug="open-studio-sections-1837",
        status="published", required_level=0,
    )
    module = Module.objects.create(
        course=course, title="Module", slug="module", sort_order=1,
    )
    _close()
    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    destinations = (
        (f"/courses/{course.slug}/home/projects", f"/studio/courses/{course.pk}/peer-reviews"),
        (f"/courses/{course.slug}/home/sessions", f"/studio/courses/{course.pk}/cohorts/"),
        (f"/courses/{course.slug}/home/syllabus", f"/studio/courses/{course.pk}/edit"),
        (f"/courses/{course.slug}/{module.slug}", f"/studio/courses/{course.pk}/edit"),
    )
    for path, destination in destinations:
        button = _open_studio(page, django_server, path)
        assert button.get_attribute("href") == destination
        button.click()
        page.wait_for_url(f"**{destination}", timeout=10000)
        assert page.url.endswith(destination)
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_opens_the_enrolled_homework_or_the_unit_editor(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Course, Homework, Module, Unit
    from content.models.course import UNIT_KIND_HOMEWORK

    staff = _create_staff_user(email=STAFF_EMAIL)
    course = Course.objects.create(
        title="Units", slug="open-studio-units-1837",
        status="published", required_level=0,
    )
    module = Module.objects.create(
        course=course, title="Module", slug="module", sort_order=1,
    )
    content_id = uuid.uuid4()
    homework_unit = Unit.objects.create(
        module=module, title="Homework", slug="homework", sort_order=1,
        kind=UNIT_KIND_HOMEWORK, content_id=content_id, is_preview=True,
    )
    lesson = Unit.objects.create(
        module=module, title="Lesson", slug="lesson", sort_order=2, is_preview=True,
    )
    today = timezone.localdate()
    enrolled = Cohort.objects.create(
        course=course, name="Enrolled",
        start_date=today - timedelta(days=7),
        end_date=today + timedelta(days=20),
    )
    other = Cohort.objects.create(
        course=course, name="Other",
        start_date=today - timedelta(days=40),
        end_date=today - timedelta(days=10),
    )
    CohortEnrollment.objects.create(cohort=enrolled, user=staff)
    homework = Homework.objects.create(
        cohort=enrolled, slug="homework", title="Enrolled homework", content_id=content_id,
    )
    Homework.objects.create(
        cohort=other, slug="homework", title="Other homework", content_id=content_id,
    )
    _close()
    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    unit_path = f"/courses/{course.slug}/{module.slug}/{homework_unit.slug}"
    button = _open_studio(page, django_server, unit_path)
    button.click()
    page.wait_for_url(f"**/studio/homeworks/{homework.pk}/submissions", timeout=10000)
    assert page.url.endswith(f"/studio/homeworks/{homework.pk}/submissions")
    lesson_path = f"/courses/{course.slug}/{module.slug}/{lesson.slug}"
    button = _open_studio(page, django_server, lesson_path)
    button.click()
    page.wait_for_url(f"**/studio/units/{lesson.pk}/edit", timeout=10000)
    assert page.url.endswith(f"/studio/units/{lesson.pk}/edit")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_reviews_homeworks_in_module_order_for_one_cohort(django_server, browser):
    from content.models import Cohort, Course, Homework, Module, Unit

    _create_staff_user(email=STAFF_EMAIL)
    course = Course.objects.create(
        title="Order", slug="open-studio-order-1837", status="published", required_level=0,
    )
    today = timezone.localdate()
    current = Cohort.objects.create(
        course=course, name="Current cohort",
        start_date=today - timedelta(days=5),
        end_date=today + timedelta(days=20),
    )
    past = Cohort.objects.create(
        course=course, name="Past cohort",
        start_date=today - timedelta(days=80),
        end_date=today - timedelta(days=40),
    )
    early = Module.objects.create(course=course, title="Week 1", slug="week-1", sort_order=1)
    late = Module.objects.create(course=course, title="Week 9", slug="week-9", sort_order=9)
    early_unit = Unit.objects.create(
        module=early, title="Early unit", slug="early-unit", sort_order=1,
        source_content_id=uuid.uuid4(),
    )
    late_unit = Unit.objects.create(
        module=late, title="Late unit", slug="late-unit", sort_order=1,
        source_content_id=uuid.uuid4(),
    )
    Homework.objects.create(
        cohort=current, slug="early", title="Early current",
        content_id=early_unit.source_content_id,
        due_date=timezone.now() + timedelta(days=1),
    )
    Homework.objects.create(
        cohort=current, slug="late", title="Late current",
        content_id=late_unit.source_content_id,
        due_date=timezone.now() + timedelta(days=40),
    )
    Homework.objects.create(
        cohort=past, slug="early", title="Early past",
        content_id=early_unit.source_content_id,
        due_date=timezone.now() - timedelta(days=30),
    )
    _close()
    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/courses/{course.pk}/homeworks",
        wait_until="domcontentloaded",
    )
    cohort_filter = page.locator("#homework-cohort-filter")
    assert cohort_filter.input_value() == str(current.pk)
    assert page.locator('[data-testid="homework-title"]').all_inner_texts() == [
        "Early current", "Late current",
    ]
    assert "Week 1" in page.locator('[data-testid="homework-module"]').nth(0).inner_text()
    assert "Week 9" in page.locator('[data-testid="homework-module"]').nth(1).inner_text()

    with page.expect_navigation(wait_until="domcontentloaded"):
        cohort_filter.select_option(str(past.pk))
    assert page.locator('[data-testid="homework-title"]').all_inner_texts() == ["Early past"]

    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("#homework-cohort-filter").select_option("all")
    assert page.locator('[data-testid="homework-title"]').all_inner_texts() == [
        "Early past", "Early current", "Late current",
    ]
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_hides_learner_emails_across_submissions_pages(django_server, browser):
    from content.models import (
        Answer,
        Cohort,
        CohortEnrollment,
        Course,
        Homework,
        Question,
        Submission,
    )
    from content.models.homework import AnswerType, QuestionType

    _create_staff_user(email=STAFF_EMAIL)
    course = Course.objects.create(
        title="Privacy", slug="open-studio-privacy-1837", status="published",
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name="Current",
        start_date=today - timedelta(days=2),
        end_date=today + timedelta(days=20),
    )
    first = Homework.objects.create(
        cohort=cohort, slug="one", title="Privacy one", content_id=uuid.uuid4(),
    )
    second = Homework.objects.create(
        cohort=cohort, slug="two", title="Privacy two", content_id=uuid.uuid4(),
    )
    learner_one = _create_user("privacy-one-1837@test.com")
    learner_two = _create_user("privacy-two-1837@test.com")
    enrollment_one = CohortEnrollment.objects.create(cohort=cohort, user=learner_one)
    enrollment_two = CohortEnrollment.objects.create(cohort=cohort, user=learner_two)
    submission = Submission.objects.create(
        homework=first, student=learner_one, enrollment=enrollment_one,
    )
    question = Question.objects.create(
        homework=first, source_question_id="q1", text="What is six times seven?",
        question_type=QuestionType.FREE_FORM, answer_type=AnswerType.INTEGER,
        correct_answer="42",
    )
    Answer.objects.create(
        submission=submission, question=question, answer_text="forty-two", is_correct=False,
    )
    Submission.objects.create(
        homework=second, student=learner_two, enrollment=enrollment_two,
    )
    _close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/homeworks/{first.pk}/submissions",
        wait_until="domcontentloaded",
    )
    email = page.locator('[data-testid="homework-submission-student"]')
    assert email.inner_text() == "privacy-one-1837@test.com"
    assert page.get_by_role("button", name="Hide emails").count() == 1
    page.get_by_role("button", name="Hide emails").click()
    hidden = _email_privacy(page)
    assert hidden["text"] == "privacy-one-1837@test.com"
    assert "blur" in hidden["filter"]
    assert hidden["userSelect"] == "none"
    page.locator('[data-testid="homework-submission-row"] summary').click()
    answer = page.locator('[data-testid="homework-submission-answer-text"]')
    assert answer.inner_text() == "forty-two"
    assert "blur" not in answer.evaluate("el => getComputedStyle(el).filter")
    still_hidden = _email_privacy(page)
    assert "blur" in still_hidden["filter"]
    assert still_hidden["userSelect"] == "none"
    assert page.evaluate("() => localStorage.getItem('studio-homework-privacy')") == "1"
    assert page.get_by_role("button", name="Show emails").count() == 1

    page.goto(
        f"{django_server}/studio/homeworks/{second.pk}/submissions",
        wait_until="domcontentloaded",
    )
    second_email = _email_privacy(page)
    assert second_email["text"] == "privacy-two-1837@test.com"
    assert "blur" in second_email["filter"]
    assert second_email["userSelect"] == "none"
    page.get_by_role("button", name="Show emails").click()
    shown = _email_privacy(page)
    assert shown["text"] == "privacy-two-1837@test.com"
    assert "blur" not in shown["filter"]
    assert shown["userSelect"] != "none"
    assert page.evaluate("() => localStorage.getItem('studio-homework-privacy')") == "0"
    context.close()
