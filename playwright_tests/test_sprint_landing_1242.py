"""End-to-end journeys for per-sprint landing pages (#1242)."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import DEFAULT_PASSWORD, ensure_site_config_tiers, ensure_tiers
from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]

ISSUE_PREFIX = "issue-1242"


def _reset():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    sprints = Sprint.objects.filter(slug__startswith=ISSUE_PREFIX)
    Plan.objects.filter(sprint__in=sprints).delete()
    SprintEnrollment.objects.filter(sprint__in=sprints).delete()
    sprints.delete()
    connection.close()


def _create_sprint(suffix, *, authored=True, status="active", min_tier_level=20):
    from django.db import connection

    from plans.models import Sprint

    kwargs = {}
    if authored:
        kwargs = {
            "description": "Turn one focused AI idea into a useful shipped product.",
            "outcomes": "A working prototype\nA documented launch",
            "audience": "Builders with an AI project idea\nEngineers who want accountability",
        }
    sprint = Sprint.objects.create(
        name=f"Issue 1242 {suffix.title()} Sprint",
        slug=f"{ISSUE_PREFIX}-{suffix}",
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        duration_weeks=6,
        status=status,
        min_tier_level=min_tier_level,
        **kwargs,
    )
    connection.close()
    return sprint


def _seed_user(email, tier_slug="main", *, is_staff=False):
    return _create_user(
        email,
        tier_slug=tier_slug,
        password=DEFAULT_PASSWORD,
        is_staff=is_staff,
    )


def _assert_pitch_before_action(page, *, authored=True):
    ordered = [
        "sprint-landing-about",
        "sprint-landing-includes",
        "sprint-landing-schedule",
    ]
    if authored:
        ordered += ["sprint-landing-outcomes", "sprint-landing-audience"]
    ordered.append("sprint-primary-action")
    positions = [
        page.locator(f'[data-testid="{testid}"]').evaluate(
            "element => element.getBoundingClientRect().top + window.scrollY"
        )
        for testid in ordered
    ]
    assert positions == sorted(positions)


def test_home_sprint_story_opens_authored_landing_before_login_ask(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        ensure_site_config_tiers()
        sprint = _create_sprint("home")

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    page.locator('[data-testid="home-featured-sprint-link"]').click()
    page.wait_for_url(f"{django_server}{sprint.get_absolute_url()}")

    _assert_pitch_before_action(page)
    expect(page.locator('[data-testid="sprint-landing-about"]')).to_contain_text(
        "Turn one focused AI idea"
    )
    expect(page.locator('[data-testid="sprint-landing-about-generic"]')).to_have_count(0)
    expect(page.locator('[data-testid="sprint-cta-login"]')).to_be_visible()


def test_anonymous_login_returns_to_same_sprint_with_join_after_pitch(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        _seed_user("main@test.com")
        sprint = _create_sprint("login-return")

    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")
    page.locator('[data-testid="sprint-cta-login"]').click()
    page.wait_for_url(f"**/accounts/login/?next=/sprints/{sprint.slug}")
    page.locator("#login-email").fill("main@test.com")
    page.locator("#login-password").fill(DEFAULT_PASSWORD)
    page.locator("#login-submit").click()
    page.wait_for_url(f"{django_server}{sprint.get_absolute_url()}")

    _assert_pitch_before_action(page)
    expect(page.locator('[data-testid="sprint-cta-join"]')).to_be_visible()


def test_blank_sprint_still_explains_itself_without_empty_optional_sections(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        sprint = _create_sprint("fallback", authored=False)

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    _assert_pitch_before_action(page, authored=False)
    expect(page.locator('[data-testid="sprint-landing-about-generic"]')).to_be_visible()
    expect(page.locator('[data-testid="sprint-landing-outcomes"]')).to_have_count(0)
    expect(page.locator('[data-testid="sprint-landing-audience"]')).to_have_count(0)
    expect(page.locator('[data-testid="sprint-cta-login"]')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_free_member_reads_pitch_then_upgrade_links_to_pricing(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        _seed_user("free@test.com", tier_slug="free")
        sprint = _create_sprint("upgrade")

    context = _auth_context(browser, "free@test.com")
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    _assert_pitch_before_action(page)
    expect(page.locator('[data-testid="sprint-cta-join"]')).to_have_count(0)
    upgrade = page.locator('[data-testid="sprint-cta-upgrade"]')
    expect(upgrade).to_contain_text("Upgrade to Main to join")
    upgrade.click()
    page.wait_for_url(f"{django_server}/pricing")
    context.close()


def test_eligible_member_joins_then_sees_working_view_without_landing(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        _seed_user("joiner@test.com")
        sprint = _create_sprint("join")

    context = _auth_context(browser, "joiner@test.com")
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")
    _assert_pitch_before_action(page)
    page.locator('[data-testid="sprint-cta-join"]').click()
    page.wait_for_url(f"**/sprints/{sprint.slug}/board")
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    expect(page.locator('[data-testid="sprint-cta-enrolled"]')).to_be_visible()
    expect(page.locator('[data-testid="sprint-landing"]')).to_have_count(0)
    context.close()


def test_enrolled_member_plan_action_remains_first_and_opens_plan(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        from django.db import connection

        from plans.models import Plan

        _reset()
        ensure_tiers()
        user = _seed_user("planner@test.com")
        sprint = _create_sprint("enrolled")
        plan = Plan.objects.create(sprint=sprint, member=user, goal="Ship the demo")
        plan_id = plan.pk
        connection.close()

    context = _auth_context(browser, "planner@test.com")
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    expect(page.locator('[data-testid="sprint-landing"]')).to_have_count(0)
    expect(page.locator('[data-testid="sprint-cta-open-plan"]')).to_be_visible()
    action_top = page.locator('[data-testid="sprint-primary-action"]').evaluate(
        "element => element.getBoundingClientRect().top + window.scrollY"
    )
    calls_top = page.locator('[data-testid="sprint-meeting-schedule"]').evaluate(
        "element => element.getBoundingClientRect().top + window.scrollY"
    )
    assert action_top < calls_top
    page.locator('[data-testid="sprint-cta-open-plan"]').click()
    page.wait_for_url(f"**/sprints/{sprint.slug}/plan/{plan_id}")
    context.close()


def test_staff_authors_landing_fields_in_studio_and_public_page_renders_them(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        _seed_user("staff@test.com", is_staff=True)
        sprint = _create_sprint("studio", authored=False)

    context = _auth_context(browser, "staff@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/sprints/{sprint.pk}/edit", wait_until="domcontentloaded")
    page.locator("#sprint-description").fill("A Studio-authored sprint description.")
    page.locator("#sprint-outcomes").fill("Prototype\nLaunch notes")
    page.locator("#sprint-audience").fill("Builders\nFounders")
    page.locator('button[type="submit"][form="sprint-edit-form"]').click()
    page.wait_for_url(f"{django_server}/studio/sprints/{sprint.pk}/")

    page.goto(f"{django_server}/studio/sprints/{sprint.pk}/edit", wait_until="domcontentloaded")
    expect(page.locator("#sprint-description")).to_have_value(
        "A Studio-authored sprint description."
    )
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.locator('[data-testid="sprint-landing-about"]')).to_contain_text(
        "A Studio-authored sprint description."
    )
    expect(page.locator('[data-testid="sprint-landing-outcomes"] li')).to_have_count(2)
    expect(page.locator('[data-testid="sprint-landing-audience"] li')).to_have_count(2)
    context.close()


def test_draft_sprint_with_landing_content_remains_404(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _reset()
        ensure_tiers()
        sprint = _create_sprint("draft", status="draft")

    response = page.goto(
        f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded"
    )
    assert response.status == 404
    expect(page.locator('[data-testid="sprint-landing"]')).to_have_count(0)
