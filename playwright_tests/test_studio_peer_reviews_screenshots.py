"""Screenshot generator for issue #493 tester review.

Run with:
    uv run pytest playwright_tests/test_studio_peer_reviews_screenshots.py -v

Writes desktop (1280x900) and mobile (390x844) screenshots to
/tmp/issue-493-screenshots/ for the four key states:
  - empty cohort (cohort defined but no submissions)
  - cohort with submissions
  - self-paced submissions group
  - action bar with renamed labels
"""

import os
from datetime import timedelta
from pathlib import Path

import pytest

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

OUT = Path("/tmp/issue-493-screenshots")
OUT.mkdir(parents=True, exist_ok=True)

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}


def _auth_cookies(session_key):
    return [
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
    ]


def _seed():
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

    course = Course.objects.create(
        title="Shipping AI 101", slug="shipping-ai-101",
        status="published",
        peer_review_enabled=True,
        peer_review_count=2,
        peer_review_deadline_days=10,
    )
    today = timezone.now().date()
    cohort_active = Cohort.objects.create(
        course=course, name="March 2026 Cohort",
        start_date=today - timedelta(days=14),
        end_date=today + timedelta(days=45),
        is_active=True,
    )
    Cohort.objects.create(
        course=course, name="January 2026 Cohort",
        start_date=today - timedelta(days=120),
        end_date=today - timedelta(days=30),
        is_active=False,
    )

    student_a = _create_user("amelia@example.com", tier_slug="main")
    student_b = _create_user("bashir@example.com", tier_slug="main")
    student_c = _create_user("clio@example.com", tier_slug="main")
    student_d = _create_user("diego@example.com", tier_slug="main")

    CohortEnrollment.objects.create(cohort=cohort_active, user=student_a)
    CohortEnrollment.objects.create(cohort=cohort_active, user=student_b)
    CohortEnrollment.objects.create(cohort=cohort_active, user=student_c)

    ProjectSubmission.objects.create(
        user=student_a, course=course, cohort=cohort_active,
        project_url="https://github.com/amelia/peer-project",
        description="Demo agent that ships customer ops automations.",
        status="submitted",
    )
    ProjectSubmission.objects.create(
        user=student_b, course=course, cohort=cohort_active,
        project_url="https://github.com/bashir/peer-project",
        description="LLM router benchmark.",
        status="in_review",
    )
    # Self-paced submission.
    ProjectSubmission.objects.create(
        user=student_d, course=course, cohort=None,
        project_url="https://github.com/diego/solo-project",
        description="Self-paced submission outside the cohort window.",
        status="submitted",
    )
    connection.close()
    return course


@pytest.mark.manual_visual
@pytest.mark.django_db(transaction=True)
def test_capture_screenshots(django_server, browser):
    _ensure_tiers()
    _create_staff_user("admin@test.com")
    course = _seed()

    session_key = _create_session_for_user("admin@test.com")

    url = f"{django_server}/studio/courses/{course.pk}/peer-reviews"

    # Desktop populated state.
    desktop = browser.new_context(viewport=DESKTOP)
    desktop.add_cookies(_auth_cookies(session_key))
    page = desktop.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "desktop_populated.png"), full_page=True)

    # Desktop: zoom-in on the cohort with submissions (first cohort group).
    cohort_group = page.locator('[data-testid="cohort-group"]').first
    cohort_group.screenshot(path=str(OUT / "desktop_cohort_with_submissions.png"))

    # Desktop: zoom-in on the self-paced group.
    self_paced = page.locator('[data-testid="cohort-group"]').nth(2)
    self_paced.screenshot(path=str(OUT / "desktop_self_paced_group.png"))

    # Desktop: zoom-in on the action bar showing renamed labels.
    action_bar = page.locator(
        '[data-testid="action-create-assignments"]'
    ).locator('xpath=ancestor::div[contains(@class, "flex-wrap")][1]')
    action_bar.screenshot(path=str(OUT / "desktop_action_bar.png"))
    desktop.close()

    # Desktop: empty cohort group state — drop submissions and re-render.
    from content.models import ProjectSubmission

    ProjectSubmission.objects.all().delete()
    connection.close()

    desktop2 = browser.new_context(viewport=DESKTOP)
    desktop2.add_cookies(_auth_cookies(session_key))
    page2 = desktop2.new_page()
    page2.goto(url, wait_until="domcontentloaded")
    page2.wait_for_timeout(300)
    page2.screenshot(path=str(OUT / "desktop_empty_cohort.png"), full_page=True)
    desktop2.close()

    # Mobile populated state — re-seed and use the new course pk.
    course2 = _seed()
    url2 = f"{django_server}/studio/courses/{course2.pk}/peer-reviews"
    mobile = browser.new_context(viewport=MOBILE)
    mobile.add_cookies(_auth_cookies(session_key))
    mpage = mobile.new_page()
    mpage.goto(url2, wait_until="domcontentloaded")
    mpage.wait_for_timeout(300)
    mpage.screenshot(path=str(OUT / "mobile_populated.png"), full_page=True)
    mobile.close()

    # Mobile: empty cohort — drop submissions on the same course.
    from content.models import ProjectSubmission as PS

    PS.objects.all().delete()
    connection.close()
    mobile2 = browser.new_context(viewport=MOBILE)
    mobile2.add_cookies(_auth_cookies(session_key))
    mpage2 = mobile2.new_page()
    mpage2.goto(url2, wait_until="domcontentloaded")
    mpage2.wait_for_timeout(300)
    mpage2.screenshot(path=str(OUT / "mobile_empty_cohort.png"), full_page=True)
    mobile2.close()
