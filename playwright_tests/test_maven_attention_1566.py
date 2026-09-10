"""Browser journeys for Maven operator attention and recovery discovery."""

import os
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _event(key, **fields):
    from integrations.models import MavenEnrollmentEvent

    return MavenEnrollmentEvent.objects.create(
        dedupe_key=key,
        identity_hash=key,
        course=key,
        **fields,
    )


def _staff_page(browser, email):
    create_staff_user(email)
    context = auth_context(browser, email)
    return context, context.new_page()


@browser_journey
def test_staff_follows_distinct_dashboard_attention_to_maven_occurrences(
    django_server, browser,
):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    MavenEnrollmentEvent.objects.all().delete()
    _event(
        "Dashboard attention one",
        override_status=MavenEnrollmentEvent.STEP_FAILED,
        override_attempts=3,
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=3,
    )
    _event(
        "Dashboard attention two",
        removal_status=MavenEnrollmentEvent.STEP_FAILED,
        removal_attempts=3,
    )
    connection.close()

    context, page = _staff_page(browser, "maven-dashboard-1566@example.com")
    try:
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        attention = page.get_by_role(
            "link", name="2 Maven enrollments need attention"
        )
        expect(attention).to_be_visible()
        attention.click()
        page.wait_for_url("**/studio/maven-events/?status=needs_attention")
        expect(page.locator("tbody tr")).to_have_count(2)
        expect(
            page.locator("tbody").get_by_text("Needs attention", exact=True)
        ).to_have_count(2)
    finally:
        context.close()


@browser_journey
def test_staff_distinguishes_fresh_failed_from_stalled_and_clears_empty_filter(
    django_server, browser,
):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    MavenEnrollmentEvent.objects.all().delete()
    now = timezone.now()
    _event(
        "Fresh retryable",
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=2,
        welcome_attempted_at=now - timedelta(minutes=5),
        welcome_completed_at=now - timedelta(minutes=5),
    )
    _event(
        "Stalled retryable",
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=2,
        welcome_attempted_at=now - timedelta(minutes=16),
        welcome_completed_at=now - timedelta(minutes=16),
    )
    connection.close()

    context, page = _staff_page(browser, "maven-filter-1566@example.com")
    try:
        page.goto(
            f"{django_server}/studio/maven-events/?status=failed",
            wait_until="domcontentloaded",
        )
        expect(page.locator("tbody tr")).to_have_count(2)
        expect(page.locator("tbody")).to_contain_text("Fresh retryable")
        expect(page.locator("tbody")).to_contain_text("Stalled retryable")

        page.locator('select[name="status"]').select_option("needs_attention")
        page.get_by_role("button", name="Apply filter").click()
        page.wait_for_url("**/studio/maven-events/?status=needs_attention")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody")).to_contain_text("Stalled retryable")
        expect(page.locator("tbody")).not_to_contain_text("Fresh retryable")

        MavenEnrollmentEvent.objects.update(
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        connection.close()
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_text("No Maven occurrences match your filters.")).to_be_visible()
        page.get_by_role("link", name="Clear filters").click()
        page.wait_for_url("**/studio/maven-events/")
        expect(page.locator("tbody tr")).to_have_count(2)
    finally:
        context.close()


@browser_journey
def test_staff_reaches_old_exhausted_occurrence_with_attention_filter_preserved(
    django_server, browser,
):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    MavenEnrollmentEvent.objects.all().delete()
    oldest = _event(
        "Old exhausted occurrence",
        override_status=MavenEnrollmentEvent.STEP_FAILED,
        override_attempts=3,
    )
    MavenEnrollmentEvent.objects.bulk_create(
        [
            MavenEnrollmentEvent(
                dedupe_key=f"new-success-{index}",
                identity_hash=f"new-success-{index}",
                course=f"New success {index}",
                override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            )
            for index in range(201)
        ]
    )
    for index in range(25):
        _event(
            f"New attention {index:02d}",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=3,
        )
    connection.close()

    context, page = _staff_page(browser, "maven-pagination-1566@example.com")
    try:
        page.goto(
            f"{django_server}/studio/maven-events/?status=needs_attention",
            wait_until="domcontentloaded",
        )
        expect(page.locator("tbody tr")).to_have_count(25)
        page.get_by_test_id("maven-occurrence-list-pager-next").click()
        page.wait_for_url("**/studio/maven-events/?status=needs_attention&page=2")
        expect(page.locator('select[name="status"]')).to_have_value(
            "needs_attention"
        )
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody")).to_contain_text("Old exhausted occurrence")
        page.get_by_role("link", name="View").click()
        page.wait_for_url(f"**/studio/maven-events/{oldest.pk}/")
        expect(page.get_by_text("Automatic retries are exhausted.")).to_be_visible()
    finally:
        context.close()
