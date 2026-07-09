"""Playwright coverage for sprint partner intro emails (#1124)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

STAFF_EMAIL = "partner-intro-staff@test.com"


def _clear_partner_intro_data():
    from email_app.models import EmailLog
    from plans.models import (
        Plan,
        Sprint,
        SprintAccountabilityPartner,
        SprintEnrollment,
        SprintPartnerIntroEmailLog,
    )

    SprintPartnerIntroEmailLog.objects.all().delete()
    EmailLog.objects.filter(email_type="sprint_partner_intro").delete()
    SprintAccountabilityPartner.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _staff():
    from accounts.models import User

    return User.objects.get(email=STAFF_EMAIL)


def _seed_sprint(name, slug, *, status="active"):
    from plans.models import Sprint

    return Sprint.objects.create(
        name=name,
        slug=slug,
        # date-rot-ok: partner-intro service fixture; stored status drives eligibility.
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=4,
        status=status,
    )


def _enroll_with_plan(sprint, user, *, with_plan=True):
    from plans.models import Plan, SprintEnrollment

    SprintEnrollment.objects.get_or_create(
        sprint=sprint,
        user=user,
        defaults={"enrolled_by": _staff()},
    )
    if with_plan:
        Plan.objects.get_or_create(sprint=sprint, member=user)


def _assign_pair(sprint, left, right):
    from plans.services import assign_accountability_partners

    assign_accountability_partners(
        sprint=sprint,
        member=left,
        partner=right,
        assigned_by=_staff(),
    )


def _seed_ready_pair(slug="ready-partner-intro", *, slack_ids=True):
    from accounts.models import User

    sprint = _seed_sprint("Ready Partner Intro", slug)
    alice = User.objects.get(email=f"alice-{slug}@test.com")
    bob = User.objects.get(email=f"bob-{slug}@test.com")
    if slack_ids:
        bob.slack_user_id = "UBOB1124"
        bob.save(update_fields=["slack_user_id"])
    _enroll_with_plan(sprint, alice)
    _enroll_with_plan(sprint, bob)
    _assign_pair(sprint, alice, bob)
    sprint_id = sprint.pk
    connection.close()
    return sprint_id


def _open_sprint(page, django_server, sprint_id):
    page.goto(
        f"{django_server}/studio/sprints/{sprint_id}/",
        wait_until="domcontentloaded",
    )


@pytest.mark.django_db(transaction=True)
def test_staff_sends_partner_intro_emails_for_ready_sprint(django_server, browser):
    from email_app.models import EmailLog

    _ensure_tiers()
    _clear_partner_intro_data()
    _create_staff_user(STAFF_EMAIL)
    slug = "ready-partner-intro"
    _create_user(f"alice-{slug}@test.com", tier_slug="free", email_verified=True)
    _create_user(f"bob-{slug}@test.com", tier_slug="free", email_verified=True)
    sprint_id = _seed_ready_pair(slug)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())

    _open_sprint(page, django_server, sprint_id)

    expect(page.locator('[data-testid="partner-intro-total-count"]')).to_have_text("2")
    expect(page.locator('[data-testid="partner-intro-eligible-count"]')).to_have_text("2")
    expect(page.locator('[data-testid="partner-intro-already-count"]')).to_have_text("0")
    expect(page.locator('[data-testid="partner-intro-email-button"]')).to_be_enabled()

    page.locator('[data-testid="partner-intro-email-button"]').click()
    page.wait_for_url(f"{django_server}/studio/sprints/{sprint_id}/")

    expect(page.get_by_text("2 sent, 0 skipped, 0 failed")).to_be_visible()
    expect(page.locator('[data-testid="partner-intro-eligible-count"]')).to_have_text("0")
    expect(page.locator('[data-testid="partner-intro-already-count"]')).to_have_text("2")
    expect(page.locator('[data-testid="partner-intro-email-button"]')).to_be_disabled()
    assert EmailLog.objects.filter(email_type="sprint_partner_intro").count() == 2
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_sees_partner_intro_readiness_blockers(django_server, browser):
    from accounts.models import User

    _ensure_tiers()
    _clear_partner_intro_data()
    _create_staff_user(STAFF_EMAIL)
    for email in (
        "missing-plan-a@test.com",
        "missing-plan-b@test.com",
        "missing-partner-a@test.com",
        "missing-partner-b@test.com",
    ):
        _create_user(email, tier_slug="free", email_verified=True)

    missing_plan = _seed_sprint("Missing Plan Partner Intro", "missing-plan-partner-intro")
    alice = User.objects.get(email="missing-plan-a@test.com")
    bob = User.objects.get(email="missing-plan-b@test.com")
    _enroll_with_plan(missing_plan, alice)
    _enroll_with_plan(missing_plan, bob, with_plan=False)
    _assign_pair(missing_plan, alice, bob)

    missing_partner = _seed_sprint(
        "Missing Partner Intro",
        "missing-partner-intro",
    )
    carol = User.objects.get(email="missing-partner-a@test.com")
    dana = User.objects.get(email="missing-partner-b@test.com")
    _enroll_with_plan(missing_partner, carol)
    _enroll_with_plan(missing_partner, dana)
    missing_plan_id = missing_plan.pk
    missing_partner_id = missing_partner.pk
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()

    _open_sprint(page, django_server, missing_plan_id)
    expect(page.locator('[data-testid="partner-intro-missing-plan-count"]')).to_have_text("1")
    expect(page.locator('[data-testid="partner-intro-missing-plan-list"]')).to_contain_text(
        "missing-plan-b@test.com",
    )
    expect(page.locator('[data-testid="partner-intro-email-button"]')).to_be_disabled()

    _open_sprint(page, django_server, missing_partner_id)
    expect(page.locator('[data-testid="partner-intro-missing-partner-count"]')).to_have_text("2")
    expect(page.locator('[data-testid="partner-intro-missing-partner-list"]')).to_contain_text(
        "missing-partner-a@test.com",
    )
    expect(page.locator('[data-testid="partner-intro-email-button"]')).to_be_disabled()

    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_sends_despite_missing_slack_profile_link_warning(django_server, browser):
    from plans.models import SprintPartnerIntroEmailLog

    _ensure_tiers()
    _clear_partner_intro_data()
    _create_staff_user(STAFF_EMAIL)
    slug = "missing-slack-link-partner-intro"
    _create_user(f"alice-{slug}@test.com", tier_slug="free", email_verified=True)
    _create_user(f"bob-{slug}@test.com", tier_slug="free", email_verified=True)
    sprint_id = _seed_ready_pair(slug, slack_ids=False)

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())

    _open_sprint(page, django_server, sprint_id)

    expect(page.locator('[data-testid="partner-intro-slack-warning-list"]')).to_be_visible()
    expect(page.locator('[data-testid="partner-intro-email-button"]')).to_be_enabled()

    page.locator('[data-testid="partner-intro-email-button"]').click()
    page.wait_for_url(f"{django_server}/studio/sprints/{sprint_id}/")

    expect(page.get_by_text("2 sent, 0 skipped, 0 failed")).to_be_visible()
    snapshots = [
        log.partner_snapshot
        for log in SprintPartnerIntroEmailLog.objects.filter(sprint_id=sprint_id)
    ]
    assert any(
        partner.get("slack_profile_url") == ""
        for snapshot in snapshots
        for partner in snapshot
    )
    connection.close()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_retries_failed_partner_intro_without_duplicate_sent(
    django_server,
    browser,
):
    from accounts.models import User
    from email_app.models import EmailLog
    from plans.models import (
        PARTNER_INTRO_EMAIL_STATUS_FAILED,
        PARTNER_INTRO_EMAIL_STATUS_SENT,
        SprintPartnerIntroEmailLog,
    )

    _ensure_tiers()
    _clear_partner_intro_data()
    _create_staff_user(STAFF_EMAIL)
    slug = "retry-partner-intro"
    _create_user(f"alice-{slug}@test.com", tier_slug="free", email_verified=True)
    _create_user(f"bob-{slug}@test.com", tier_slug="free", email_verified=True)
    sprint_id = _seed_ready_pair(slug)
    alice = User.objects.get(email=f"alice-{slug}@test.com")
    bob = User.objects.get(email=f"bob-{slug}@test.com")
    from plans.models import Sprint

    target = Sprint.objects.get(pk=sprint_id)
    SprintPartnerIntroEmailLog.objects.create(
        sprint=target,
        member=alice,
        status=PARTNER_INTRO_EMAIL_STATUS_FAILED,
        last_error="SES timeout",
    )
    SprintPartnerIntroEmailLog.objects.create(
        sprint=target,
        member=bob,
        status=PARTNER_INTRO_EMAIL_STATUS_SENT,
    )
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())

    _open_sprint(page, django_server, sprint_id)
    expect(page.locator('[data-testid="partner-intro-eligible-count"]')).to_have_text("1")
    expect(page.locator('[data-testid="partner-intro-already-count"]')).to_have_text("1")

    page.locator('[data-testid="partner-intro-email-button"]').click()
    page.wait_for_url(f"{django_server}/studio/sprints/{sprint_id}/")

    expect(page.get_by_text("1 sent, 1 skipped, 0 failed")).to_be_visible()
    assert EmailLog.objects.filter(email_type="sprint_partner_intro").count() == 1
    connection.close()
    context.close()
