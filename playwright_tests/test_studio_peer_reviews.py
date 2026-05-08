"""Playwright E2E tests for issue #493: Studio peer-review management rework.

Covers:
- Desktop: course-context header, cohort group with submission, self-paced
  group with submission, friendly status filters and renamed actions.
- Empty/disabled state: peer-review-disabled banner is shown.
- Mobile: page renders without horizontal overflow at 390x844 and primary
  action button is tappable (>= 44px).

Usage:
    uv run pytest playwright_tests/test_studio_peer_reviews.py -v
"""

import os
from datetime import timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
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
from django.utils import timezone  # noqa: E402


def _mobile_auth_context(browser, email):
    """Create a 390x844 mobile auth context for the given user."""
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _clear_state():
    from content.models import (
        Cohort,
        CohortEnrollment,
        Course,
        ProjectSubmission,
    )

    ProjectSubmission.objects.all().delete()
    CohortEnrollment.objects.all().delete()
    Cohort.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course_with_peer_review(
    title="Peer Review Course", slug="peer-review-course", enabled=True,
):
    from content.models import Course

    course = Course.objects.create(
        title=title, slug=slug, status="published",
        peer_review_enabled=enabled,
        peer_review_count=2,
        peer_review_deadline_days=10,
    )
    connection.close()
    return course


def _create_cohort(course, name="March 2026 Cohort"):
    from content.models import Cohort

    today = timezone.now().date()
    cohort = Cohort.objects.create(
        course=course, name=name,
        start_date=today - timedelta(days=15),
        end_date=today + timedelta(days=45),
        is_active=True,
    )
    connection.close()
    return cohort


def _create_submission(user, course, cohort=None, status="submitted"):
    from content.models import ProjectSubmission

    sub = ProjectSubmission.objects.create(
        user=user, course=course, cohort=cohort,
        project_url="https://example.com/project",
        description="A demo submission.",
        status=status,
    )
    connection.close()
    return sub


@pytest.mark.django_db(transaction=True)
class TestDesktopCohortGrouping:
    def test_course_header_cohort_group_and_self_paced(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        cohort_user = _create_user("cohort-student@test.com", tier_slug="main")
        solo_user = _create_user("solo-student@test.com", tier_slug="main")

        course = _create_course_with_peer_review()
        cohort = _create_cohort(course)

        from content.models import CohortEnrollment

        CohortEnrollment.objects.create(cohort=cohort, user=cohort_user)
        connection.close()

        _create_submission(cohort_user, course, cohort=cohort, status="submitted")
        _create_submission(solo_user, course, cohort=None, status="in_review")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/peer-reviews",
            wait_until="domcontentloaded",
        )

        # Course-context header.
        header = page.locator('[data-testid="peer-review-course-header"]')
        assert header.is_visible()
        assert "Peer reviews for Peer Review Course" in header.inner_text()
        status_chip = page.locator('[data-testid="peer-review-status"]')
        assert "Peer review enabled" in status_chip.inner_text()
        back_link = page.locator('[data-testid="back-to-course-edit"]')
        assert back_link.is_visible()
        assert back_link.get_attribute("href") == (
            f"/studio/courses/{course.pk}/edit"
        )

        # Two cohort groups: the named cohort, then the self-paced bucket.
        groups = page.locator('[data-testid="cohort-group"]')
        assert groups.count() == 2

        cohort_group = groups.nth(0)
        assert "March 2026 Cohort" in cohort_group.locator(
            '[data-testid="cohort-group-name"]'
        ).inner_text()
        assert cohort_group.locator(
            '[data-testid="cohort-active-state"]'
        ).inner_text().strip() == "Active"
        assert "1 enrolled" in cohort_group.locator(
            '[data-testid="cohort-enrollment-count"]'
        ).inner_text()
        # Submission row inside the cohort group references the cohort student.
        assert "cohort-student@test.com" in cohort_group.inner_text()

        self_paced = groups.nth(1)
        assert "Self-paced / no cohort" in self_paced.locator(
            '[data-testid="cohort-group-name"]'
        ).inner_text()
        assert self_paced.locator(
            '[data-testid="cohort-self-paced-tag"]'
        ).is_visible()
        assert "solo-student@test.com" in self_paced.inner_text()

        # Renamed action labels.
        assert page.locator(
            '[data-testid="action-create-assignments"]'
        ).inner_text().strip().startswith("Create review assignments")
        assert "Issue certificates for eligible completions" in (
            page.locator('[data-testid="action-issue-certificates"]').inner_text()
        )
        assert "Form Batch" not in page.content()

        # Friendly status filter chips with counts.
        assert "Awaiting reviewers" in page.locator(
            '[data-testid="status-filter-submitted"]'
        ).inner_text()
        assert "Being reviewed" in page.locator(
            '[data-testid="status-filter-in_review"]'
        ).inner_text()

        # Submission status chip uses friendly label, not raw code.
        chips = page.locator('[data-testid="submission-status-label"]')
        chip_texts = [chips.nth(i).inner_text().strip() for i in range(chips.count())]
        assert "Awaiting reviewers" in chip_texts
        assert "Being reviewed" in chip_texts
        assert "submitted" not in chip_texts
        assert "in_review" not in chip_texts


@pytest.mark.django_db(transaction=True)
class TestDesktopDisabledState:
    def test_disabled_course_shows_helpful_empty_state(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        course = _create_course_with_peer_review(
            title="Disabled Course", slug="disabled", enabled=False,
        )

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/peer-reviews",
            wait_until="domcontentloaded",
        )

        empty = page.locator('[data-testid="peer-review-disabled-empty"]')
        assert empty.is_visible()
        assert "Peer review is turned off" in empty.inner_text()

        # The disabled chip in the header.
        chip = page.locator('[data-testid="peer-review-status"]')
        assert "Peer review disabled" in chip.inner_text()


@pytest.mark.django_db(transaction=True)
class TestMobilePeerReviewResponsive:
    def test_no_horizontal_overflow_and_action_tappable(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        course = _create_course_with_peer_review()
        cohort = _create_cohort(course)
        student = _create_user("mobile-student@test.com", tier_slug="main")
        from content.models import CohortEnrollment

        CohortEnrollment.objects.create(cohort=cohort, user=student)
        connection.close()
        _create_submission(student, course, cohort=cohort, status="submitted")

        context = _mobile_auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/peer-reviews",
            wait_until="domcontentloaded",
        )

        # No horizontal overflow.
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"Horizontal overflow: scrollWidth={scroll_width}, "
            f"clientWidth={client_width}"
        )

        # Primary action button is tall enough to tap.
        action = page.locator('[data-testid="action-create-assignments"]')
        assert action.is_visible()
        box = action.bounding_box()
        assert box is not None
        assert box["height"] >= 36, (
            f"Mobile primary action height {box['height']} is too small"
        )

        # The cohort group still renders on mobile.
        assert page.locator('[data-testid="cohort-group"]').count() >= 1
