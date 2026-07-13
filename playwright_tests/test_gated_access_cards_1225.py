"""End-to-end coverage for canonical gated cards (issue #1225)."""

import datetime
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1225")


def _reset_content():
    from django.db import connection

    from content.models import Course, Project
    from voting.models import Poll

    Poll.objects.all().delete()
    Course.objects.all().delete()
    Project.objects.all().delete()
    connection.close()


def _course(slug, required_level):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title=f"{slug.title()} Course",
        slug=slug,
        status="published",
        required_level=required_level,
        description="A practical course description.",
    )
    module = Module.objects.create(
        course=course,
        title="Foundations",
        slug="foundations",
        overview="Public module overview.",
    )
    Unit.objects.create(
        module=module,
        title="First lesson",
        slug="first-lesson",
    )
    connection.close()
    return course, module


def _project(slug="gated-project-1225", required_level=10):
    from django.db import connection

    from content.models import Project

    project = Project.objects.create(
        title="Gated Project 1225",
        slug=slug,
        description="Public project metadata.",
        content_html="<p>SECRET PROJECT BODY 1225</p>",
        date=datetime.date(2026, 7, 13),
        required_level=required_level,
        published=True,
    )
    connection.close()
    return project


def _poll(poll_type="topic", allow_proposals=False):
    from django.db import connection

    from voting.models import Poll, PollOption

    poll = Poll.objects.create(
        title=f"{poll_type.title()} Poll 1225",
        poll_type=poll_type,
        status="open",
        allow_proposals=allow_proposals,
    )
    option = PollOption.objects.create(poll=poll, title="First option")
    connection.close()
    return poll, option


def _prepare_page(page, *, mobile=False, theme="light"):
    viewport = {"width": 390, "height": 844} if mobile else {
        "width": 1280,
        "height": 900,
    }
    page.set_viewport_size(viewport)
    page.add_init_script(
        f"localStorage.setItem('theme', {theme!r});",
    )


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    )


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@pytest.mark.core
def test_visitor_understands_paid_course_requirement(django_server, page):
    _reset_content()
    _course("main-1225", 20)
    _prepare_page(page, theme="dark")

    page.goto(f"{django_server}/courses/main-1225", wait_until="domcontentloaded")

    expect(page.locator("article header")).to_contain_text("Main or above")
    gate = page.get_by_test_id("course-gated-cta")
    expect(gate).to_contain_text("Main or above required")
    expect(page.get_by_test_id("gated-required-tier")).to_have_count(1)
    _assert_no_horizontal_overflow(page)
    _capture(page, "course-desktop-dark")
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    _capture(page, "course-mobile-dark")
    page.get_by_test_id("course-gated-cta-button").click()
    page.wait_for_url(f"{django_server}/pricing")


@pytest.mark.core
def test_visitor_can_still_start_free_course_inline(django_server, page):
    _reset_content()
    _course("free-1225", 0)
    _prepare_page(page, mobile=True, theme="light")

    page.goto(f"{django_server}/courses/free-1225", wait_until="domcontentloaded")

    expect(page.locator("article header")).to_contain_text("Free")
    expect(page.get_by_test_id("inline-register-card")).to_be_visible()
    expect(page.get_by_test_id("course-gated-cta")).to_have_count(0)
    expect(page.get_by_test_id("gated-required-tier")).to_have_count(0)
    _assert_no_horizontal_overflow(page)


@pytest.mark.core
def test_free_member_gets_module_upgrade_path(django_server, browser):
    _reset_content()
    _course("module-main-1225", 20)
    ensure_tiers()
    create_user("module-free-1225@example.com", tier_slug="free")
    context = auth_context(browser, "module-free-1225@example.com")
    page = context.new_page()
    _prepare_page(page, mobile=True, theme="dark")
    try:
        page.goto(
            f"{django_server}/courses/module-main-1225/foundations",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("First lesson", exact=True)).to_be_visible()
        expect(page.get_by_test_id("module-cta")).to_contain_text(
            "Main or above required"
        )
        _assert_no_horizontal_overflow(page)
        _capture(page, "module-mobile-dark")
        page.set_viewport_size({"width": 1280, "height": 900})
        _assert_no_horizontal_overflow(page)
        _capture(page, "module-desktop-dark")
        page.get_by_test_id("module-cta-button").click()
        page.wait_for_url(f"{django_server}/pricing")
    finally:
        context.close()


@pytest.mark.core
def test_main_member_reads_course_and_module_without_paywalls(
    django_server, browser,
):
    _reset_content()
    _course("member-main-1225", 20)
    ensure_tiers()
    create_user("main-1225@example.com", tier_slug="main")
    context = auth_context(browser, "main-1225@example.com")
    page = context.new_page()
    _prepare_page(page)
    try:
        page.goto(
            f"{django_server}/courses/member-main-1225",
            wait_until="domcontentloaded",
        )
        expect(page.locator("article header")).to_contain_text("Main or above")
        expect(page.get_by_test_id("course-gated-cta")).to_have_count(0)
        page.goto(
            f"{django_server}/courses/member-main-1225/foundations",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_text("Public module overview.")).to_be_visible()
        expect(page.get_by_test_id("module-cta")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.core
def test_guest_project_keeps_free_and_paid_next_steps(django_server, page):
    _reset_content()
    project = _project()
    _prepare_page(page, theme="light")

    page.goto(f"{django_server}{project.get_absolute_url()}", wait_until="domcontentloaded")

    body = page.content()
    assert "SECRET PROJECT BODY 1225" not in body
    assert "filter: blur(8px)" not in body
    gate = page.get_by_test_id("project-paywall")
    expect(gate).to_contain_text("Basic or above required")
    expect(page.get_by_test_id("project-upgrade-cta")).to_be_visible()
    expect(page.get_by_test_id("gated-create-free-account-link")).to_be_visible()
    expect(page.get_by_test_id("gated-sign-in-link")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _capture(page, "project-desktop-light")
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    _capture(page, "project-mobile-light")
    page.get_by_test_id("gated-create-free-account-link").click()
    page.wait_for_url(
        f"{django_server}/accounts/register/?next={project.get_absolute_url()}"
    )


@pytest.mark.core
def test_free_member_project_has_upgrade_without_signup(django_server, browser):
    _reset_content()
    project = _project("member-project-1225")
    ensure_tiers()
    create_user("project-free-1225@example.com", tier_slug="free")
    context = auth_context(browser, "project-free-1225@example.com")
    page = context.new_page()
    _prepare_page(page, mobile=True)
    try:
        page.goto(
            f"{django_server}{project.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("project-paywall")).to_contain_text(
            "Upgrade to Basic"
        )
        expect(page.get_by_test_id("project-upgrade-cta")).to_be_visible()
        expect(page.get_by_test_id("gated-create-free-account-link")).to_have_count(0)
        expect(page.get_by_test_id("gated-sign-in-link")).to_have_count(0)
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()


@pytest.mark.core
def test_unverified_project_member_gets_verification_not_pricing(
    django_server, browser,
):
    _reset_content()
    project = _project("verify-project-1225", required_level=0)
    ensure_tiers()
    create_user(
        "project-unverified-1225@example.com",
        tier_slug="free",
        email_verified=False,
    )
    context = auth_context(browser, "project-unverified-1225@example.com")
    page = context.new_page()
    _prepare_page(page)
    try:
        page.goto(
            f"{django_server}{project.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_test_id("verify-email-required-card")).to_be_visible()
        expect(page.get_by_test_id("project-paywall")).to_have_count(0)
        expect(page.get_by_test_id("gated-required-tier")).to_have_count(0)
        expect(page.get_by_test_id("project-upgrade-cta")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.core
def test_free_member_topic_poll_shows_main_requirement(django_server, browser):
    _reset_content()
    poll, _ = _poll("topic")
    ensure_tiers()
    create_user("poll-free-1225@example.com", tier_slug="free")
    context = auth_context(browser, "poll-free-1225@example.com")
    page = context.new_page()
    _prepare_page(page, theme="light")
    try:
        page.goto(f"{django_server}/vote/{poll.id}", wait_until="domcontentloaded")
        expect(page.get_by_test_id("poll-gated")).to_contain_text(
            "Main or above required"
        )
        expect(page.locator("button.vote-btn")).to_have_count(0)
        _capture(page, "poll-desktop-light")
        page.get_by_test_id("poll-pricing-cta").click()
        page.wait_for_url(f"{django_server}/pricing")
    finally:
        context.close()


@pytest.mark.core
def test_main_member_course_poll_shows_terminal_premium_tier(
    django_server, browser,
):
    _reset_content()
    poll, _ = _poll("course")
    ensure_tiers()
    create_user("poll-main-1225@example.com", tier_slug="main")
    context = auth_context(browser, "poll-main-1225@example.com")
    page = context.new_page()
    _prepare_page(page, mobile=True, theme="dark")
    try:
        page.goto(f"{django_server}/vote/{poll.id}", wait_until="domcontentloaded")
        gate = page.get_by_test_id("poll-gated")
        expect(gate).to_contain_text("Premium required")
        expect(gate).not_to_contain_text("Premium or above required")
        _assert_no_horizontal_overflow(page)
        _capture(page, "poll-mobile-dark")
        expect(page.get_by_test_id("poll-pricing-cta")).to_have_attribute(
            "href", "/pricing"
        )
    finally:
        context.close()


@pytest.mark.core
def test_premium_member_keeps_vote_and_proposal_flow(django_server, browser):
    _reset_content()
    poll, option = _poll("course", allow_proposals=True)
    ensure_tiers()
    create_user("poll-premium-1225@example.com", tier_slug="premium")
    context = auth_context(browser, "poll-premium-1225@example.com")
    page = context.new_page()
    _prepare_page(page)
    try:
        page.goto(f"{django_server}/vote/{poll.id}", wait_until="domcontentloaded")
        expect(page.get_by_test_id("poll-gated")).to_have_count(0)
        expect(page.get_by_test_id("gated-required-tier")).to_have_count(0)
        vote = page.locator(f'button.vote-btn[data-option-id="{option.id}"]')
        vote.click()
        expect(vote).to_have_attribute("data-voted", "true")
        page.fill("#proposal-title", "New proposal 1225")
        page.get_by_role("button", name="Submit Proposal").click()
        expect(page.locator("#propose-message")).to_contain_text(
            "Proposal submitted"
        )
    finally:
        context.close()
