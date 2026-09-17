"""Browser journeys for Maven CRM contact tagging (issue #1732).

Every enrollment here goes through the real ``handle_maven_event`` handler
rather than hand-written tag rows, so the journeys prove the shipped webhook
path and not a fixture.
"""

import json
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

COURSE_KEY = "from-rag-to-agents"


def _reset(staff_email):
    """Drop every non-staff account and occurrence so counts are exact."""
    from django.db import connection

    from accounts.models import User
    from integrations.models import MavenEnrollmentEvent

    MavenEnrollmentEvent.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _enroll(email, *, cohort_key="4", course_key=COURSE_KEY, cohort_label=None):
    from unittest.mock import patch

    from django.db import connection

    from integrations.services.maven import handle_maven_event

    payload = {
        "event": "user_cohort.enrolled",
        "email": email,
        "course": {"name": "AI Engineering Buildcamp", "id": course_key},
        "cohort": {"name": cohort_label or cohort_key, "id": cohort_key},
    }
    with patch("integrations.services.maven.send_package_mail"):
        handle_maven_event(payload)
    connection.close()


def _remove(email, *, cohort_key="4", course_key=COURSE_KEY):
    from django.db import connection

    from integrations.services.maven import handle_maven_event

    handle_maven_event(
        {
            "event": "user_cohort.removed",
            "email": email,
            "course": {"name": "AI Engineering Buildcamp", "id": course_key},
            "cohort": {"name": cohort_key, "id": cohort_key},
        }
    )
    connection.close()


def _user_pk(email):
    from django.db import connection

    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _occurrence_pk(email):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    pk = (
        MavenEnrollmentEvent.objects.filter(email=email)
        .order_by("-created_at")
        .values_list("pk", flat=True)
        .first()
    )
    connection.close()
    return pk


def _fail_tagging(email):
    from django.db import connection

    from integrations.models import MavenEnrollmentEvent

    MavenEnrollmentEvent.objects.filter(email=email).update(
        tagging_status=MavenEnrollmentEvent.STEP_FAILED,
        tagging_attempts=3,
        tagging_error="RuntimeError",
    )
    connection.close()


def _opt_in(email):
    """Simulate the enrollee acting on the welcome email's opt-in link.

    A fresh Maven account is deliberately ``unsubscribed=True`` and
    unverified (issue #1593), so it is in no campaign audience at all until
    the person opts in. The tag filter is what this journey is about, so the
    consent step is performed up front rather than asserted here.
    """
    from django.db import connection

    from accounts.models import User

    User.objects.filter(email=email).update(
        email_verified=True, unsubscribed=False,
    )
    connection.close()


def _untag(email):
    from django.db import connection

    from accounts.models import User

    user = User.objects.get(email=email)
    user.tags = []
    user.save(update_fields=["tags"])
    connection.close()


def _staff_page(browser, email):
    ensure_tiers()
    create_staff_user(email)
    _reset(email)
    context = auth_context(browser, email)
    return context, context.new_page()


def _row_emails(page):
    return page.locator("tbody tr")


@browser_journey
def test_ops_lead_segments_the_new_cohort_in_the_crm(django_server, browser):
    staff_email = "maven-tags-segment-1732@example.com"
    context, page = _staff_page(browser, staff_email)
    try:
        _enroll("cohort4-one@example.com")
        _enroll("cohort4-two@example.com")

        page.goto(
            f"{django_server}/studio/users/?tag=ai-buildcamp-4",
            wait_until="domcontentloaded",
        )
        expect(_row_emails(page)).to_have_count(2)
        expect(page.locator("tbody")).to_contain_text("cohort4-one@example.com")
        expect(page.locator("tbody")).to_contain_text("cohort4-two@example.com")
        expect(page.locator("tbody")).not_to_contain_text(staff_email)

        page.goto(
            f"{django_server}/studio/users/?tag=maven",
            wait_until="domcontentloaded",
        )
        expect(_row_emails(page)).to_have_count(2)

        page.goto(f"{django_server}/studio/tags/", wait_until="domcontentloaded")
        for slug in ("maven", "ai-buildcamp", "ai-buildcamp-4"):
            row = page.locator(f'[data-testid="studio-tag-row"][data-tag="{slug}"]')
            expect(row.locator('[data-testid="studio-tag-user-count"]')).to_have_text(
                "2 users"
            )
    finally:
        context.close()


@browser_journey
def test_support_sees_where_a_maven_member_came_from(django_server, browser):
    context, page = _staff_page(browser, "maven-tags-source-1732@example.com")
    try:
        _enroll("source-check@example.com")
        member_pk = _user_pk("source-check@example.com")

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        source = page.locator('[data-signup-source="maven_webhook"]')
        expect(source).to_have_text("Maven enrollment webhook")
        expect(page.locator("body")).not_to_contain_text(
            "Bulk import (Stripe / CSV / course DB)"
        )
        for slug in ("maven", "ai-buildcamp", "ai-buildcamp-4"):
            expect(
                page.locator(f'[data-testid="user-tag-chip"][data-tag="{slug}"]')
            ).to_have_count(1)
    finally:
        context.close()


@browser_journey
def test_marketing_scopes_a_cohort_only_campaign_from_the_tag(
    django_server, browser,
):
    from playwright_tests.conftest import create_user

    context, page = _staff_page(browser, "maven-tags-campaign-1732@example.com")
    try:
        _enroll("campaign-one@example.com")
        _enroll("campaign-two@example.com")
        _opt_in("campaign-one@example.com")
        _opt_in("campaign-two@example.com")
        create_user(
            "campaign-unrelated@example.com",
            tier_slug="main",
            email_verified=True,
            unsubscribed=False,
        )

        page.goto(
            f"{django_server}/studio/campaigns/new", wait_until="domcontentloaded"
        )
        status = page.locator("[data-recipient-count-status]")
        tags_field = page.locator('[data-testid="target-tags-any-input"]')

        tags_field.fill("ai-buildcamp-4")
        expect(status).to_contain_text("Will reach 2 eligible recipients")

        tags_field.fill("maven")
        expect(status).to_contain_text("Will reach 2 eligible recipients")
    finally:
        context.close()


@browser_journey
def test_ops_removes_a_student_mid_cohort_and_the_cohort_comms_stop(
    django_server, browser,
):
    context, page = _staff_page(browser, "maven-tags-removal-1732@example.com")
    try:
        _enroll("removed-student@example.com")
        member_pk = _user_pk("removed-student@example.com")
        _remove("removed-student@example.com")

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        expect(
            page.locator('[data-testid="user-tag-chip"][data-tag="maven"]')
        ).to_have_count(1)
        expect(
            page.locator('[data-testid="user-tag-chip"][data-tag="ai-buildcamp"]')
        ).to_have_count(0)
        expect(
            page.locator('[data-testid="user-tag-chip"][data-tag="ai-buildcamp-4"]')
        ).to_have_count(0)

        page.goto(
            f"{django_server}/studio/users/?tag=ai-buildcamp-4",
            wait_until="domcontentloaded",
        )
        expect(page.locator("body")).not_to_contain_text("removed-student@example.com")
    finally:
        context.close()


@browser_journey
def test_returning_student_keeps_their_earlier_cohort_history(
    django_server, browser,
):
    context, page = _staff_page(browser, "maven-tags-returning-1732@example.com")
    try:
        _enroll("returning-student@example.com", cohort_key="3")
        _enroll("returning-student@example.com", cohort_key="4")
        member_pk = _user_pk("returning-student@example.com")
        detail_url = f"{django_server}/studio/users/{member_pk}/"

        page.goto(detail_url, wait_until="domcontentloaded")
        for slug in ("ai-buildcamp-3", "ai-buildcamp-4"):
            expect(
                page.locator(f'[data-testid="user-tag-chip"][data-tag="{slug}"]')
            ).to_have_count(1)

        _remove("returning-student@example.com", cohort_key="4")
        page.reload(wait_until="domcontentloaded")

        expect(
            page.locator('[data-testid="user-tag-chip"][data-tag="ai-buildcamp-4"]')
        ).to_have_count(0)
        for slug in ("maven", "ai-buildcamp", "ai-buildcamp-3"):
            expect(
                page.locator(f'[data-testid="user-tag-chip"][data-tag="{slug}"]')
            ).to_have_count(1)
    finally:
        context.close()


@browser_journey
def test_support_inspects_the_ledger_to_confirm_the_crm_tags_went_out(
    django_server, browser,
):
    context, page = _staff_page(browser, "maven-tags-ledger-1732@example.com")
    try:
        _enroll("ledger-check@example.com")
        occurrence_pk = _occurrence_pk("ledger-check@example.com")

        page.goto(f"{django_server}/studio/maven-events/", wait_until="domcontentloaded")
        page.locator(f'a[href="/studio/maven-events/{occurrence_pk}/"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        card = page.locator(
            '[data-testid="maven-occurrence-detail"] > div'
        ).filter(has=page.get_by_role("heading", name="tagging", exact=True))
        expect(card).to_contain_text("Succeeded")
        expect(card.locator('[data-testid="maven-step-note-tagging"]')).to_contain_text(
            "Attempts: 1"
        )
    finally:
        context.close()


@browser_journey
def test_operator_recovers_an_occurrence_whose_tagging_step_failed(
    django_server, browser,
):
    context, page = _staff_page(browser, "maven-tags-recovery-1732@example.com")
    try:
        _enroll("recovery-check@example.com")
        _untag("recovery-check@example.com")
        _fail_tagging("recovery-check@example.com")
        occurrence_pk = _occurrence_pk("recovery-check@example.com")
        member_pk = _user_pk("recovery-check@example.com")

        page.goto(
            f"{django_server}/studio/maven-events/?status=failed",
            wait_until="domcontentloaded",
        )
        # The list identifies occurrences by id, not by the enrollee's email.
        occurrence_link = page.locator(
            f'a[href="/studio/maven-events/{occurrence_pk}/"]'
        )
        expect(occurrence_link.first).to_be_visible()
        occurrence_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        card = page.locator(
            '[data-testid="maven-occurrence-detail"] > div'
        ).filter(has=page.get_by_role("heading", name="tagging", exact=True))
        card.get_by_role("button", name="Retry safely").click()
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("body")).to_contain_text("Maven tagging step recovered.")
        card = page.locator(
            '[data-testid="maven-occurrence-detail"] > div'
        ).filter(has=page.get_by_role("heading", name="tagging", exact=True))
        expect(card).to_contain_text("Succeeded")

        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        for slug in ("maven", "ai-buildcamp", "ai-buildcamp-4"):
            expect(
                page.locator(f'[data-testid="user-tag-chip"][data-tag="{slug}"]')
            ).to_have_count(1)
    finally:
        context.close()


@browser_journey
def test_operator_onboards_a_second_maven_course_from_studio_settings(
    django_server, browser,
):
    context, page = _staff_page(browser, "maven-tags-settings-1732@example.com")
    try:
        page.goto(
            f"{django_server}/studio/settings/#messaging",
            wait_until="domcontentloaded",
        )
        maven_card = page.locator("#integration-maven")
        maven_card.locator("#field-MAVEN_COURSE_TAG_PREFIXES").fill(
            json.dumps({COURSE_KEY: "ai-buildcamp", "agentic-evals": "ai-evals"})
        )
        maven_card.get_by_role("button", name="Save maven").click()
        page.wait_for_load_state("domcontentloaded")

        badge = page.locator('[data-settings-source="MAVEN_COURSE_TAG_PREFIXES"]')
        expect(badge).to_have_attribute("data-source-badge", "db")

        _enroll("second-course@example.com", course_key="agentic-evals", cohort_key="1")

        page.goto(
            f"{django_server}/studio/users/?tag=ai-evals-1",
            wait_until="domcontentloaded",
        )
        expect(page.locator("tbody")).to_contain_text("second-course@example.com")

        page.goto(
            f"{django_server}/studio/users/?tag=ai-buildcamp",
            wait_until="domcontentloaded",
        )
        expect(page.locator("body")).not_to_contain_text("second-course@example.com")
    finally:
        context.close()
