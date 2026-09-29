"""Staff pick a user's cohort on the Studio course enrollments page.

Usage:
    uv run pytest playwright_tests/test_studio_enrollment_cohort.py -v
"""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _seed():
    from django.utils import timezone

    from content.models import Cohort, Course, Enrollment

    Enrollment.objects.all().delete()
    Course.objects.all().delete()
    course = Course.objects.create(
        title="AI Buildcamp", slug="ai-buildcamp", status="published",
    )
    today = timezone.localdate()
    Cohort.objects.create(
        course=course, name="Cohort 3", external_key="3",
        start_date=today - datetime.timedelta(days=120),
        end_date=today - datetime.timedelta(days=60),
    )
    Cohort.objects.create(
        course=course, name="Cohort 4", external_key="4",
        start_date=today - datetime.timedelta(days=7),
        end_date=today + datetime.timedelta(days=60),
    )
    connection.close()
    return course


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_picks_and_changes_a_users_cohort(django_server, browser):
    _ensure_tiers()
    _create_staff_user("admin@test.com")
    _create_user("learner@test.com", tier_slug="main")
    course = _seed()

    context = _auth_context(browser, "admin@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/courses/{course.pk}/enrollments/",
        wait_until="domcontentloaded",
    )

    page.get_by_test_id("enroll-email-input").fill("learner@test.com")
    page.get_by_test_id("enroll-cohort-select").select_option(label="Cohort 3")
    page.get_by_role("button", name="Enroll user", exact=True).click()
    page.wait_for_load_state("domcontentloaded")

    row = page.get_by_test_id("enrollment-row").filter(has_text="learner@test.com")
    expect(row.get_by_test_id("enrollment-cohort-names")).to_have_text("Cohort 3")

    row.get_by_test_id("change-cohort-select").select_option(label="Cohort 4")
    row.get_by_role("button", name="Change cohort", exact=True).click()
    page.wait_for_load_state("domcontentloaded")

    row = page.get_by_test_id("enrollment-row").filter(has_text="learner@test.com")
    expect(row.get_by_test_id("enrollment-cohort-names")).to_have_text("Cohort 4")
    expect(page.get_by_text("Moved learner@test.com to Cohort 4.")).to_be_visible()
    context.close()
