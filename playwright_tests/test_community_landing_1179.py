"""Playwright coverage for the /community overview landing page."""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1179")


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _seed_base(*, with_activity_config=True):
    from django.db import connection

    from content.models import SiteConfig

    ensure_tiers()
    if with_activity_config:
        ensure_site_config_tiers()
    else:
        SiteConfig.objects.filter(key="tiers").delete()
        connection.close()


def _unique_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _open_mobile_community_menu(page):
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)
    page.get_by_test_id("mobile-nav-community-trigger").click()
    menu = page.get_by_test_id("mobile-nav-community-menu")
    menu.wait_for(state="visible")
    return menu


def _menu_links(menu):
    return menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => [
                a.getAttribute('data-testid'),
                a.getAttribute('href'),
                a.textContent.trim()
            ])
        """
    )


def test_anonymous_builder_understands_community_value_and_ctas(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()

    page.set_viewport_size({"width": 1280, "height": 900})
    response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    assert response.status == 200
    _shot(page, "anonymous-desktop")

    expect(page.get_by_test_id("community-landing-page")).to_be_visible()
    expect(page.locator("#site-header")).to_be_visible()
    expect(page.locator("footer")).to_be_visible()
    expect(page.get_by_test_id("community-landing-heading")).to_contain_text(
        "Ship AI projects with structure, accountability"
    )
    expect(page.get_by_test_id("community-hero-copy")).to_contain_text(
        "guided work, community support, live sessions, sprints, workshops"
    )
    assert page.get_by_test_id("community-landing-pricing-cta").get_attribute(
        "href"
    ) == "/pricing"
    assert page.get_by_test_id("community-landing-activities-cta").get_attribute(
        "href"
    ) == "/activities#access-by-tier"


def test_visitor_compares_activities_and_returns_to_community(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()

    page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    page.get_by_test_id("community-landing-activities-cta").click()
    page.wait_for_url("**/activities#access-by-tier")
    expect(page.get_by_test_id("activities-access-by-tier-section")).to_be_visible()

    page.go_back(wait_until="domcontentloaded")
    page.wait_for_url("**/community")
    expect(page.get_by_test_id("community-landing-heading")).to_be_visible()


def test_active_participation_links_load_public_destinations(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()

    page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    expect(page.get_by_test_id("community-activities-section")).to_be_visible()
    for test_id, path in [
        ("community-link-sprints", "/sprints"),
        ("community-link-events", "/events"),
        ("community-link-workshops", "/workshops"),
    ]:
        assert page.get_by_test_id(test_id).get_attribute("href") == path
        response = page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
        assert response.status == 200
        assert "/accounts/login/" not in page.url
        page.goto(f"{django_server}/community", wait_until="domcontentloaded")


def test_free_member_sees_what_requires_main_or_premium(
    django_server, browser, django_db_blocker
):
    email = _unique_email("community-free")
    with django_db_blocker.unblock():
        _seed_base()
        create_user(email, tier_slug="free")

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
        assert response.status == 200
        expect(page.get_by_test_id("community-benefits-section")).to_contain_text(
            "Main and Premium members"
        )
        expect(page.get_by_text("Private Slack community access")).to_be_visible()
        assert page.get_by_test_id("community-landing-pricing-cta").get_attribute(
            "href"
        ) == "/pricing"
    finally:
        context.close()


def test_main_member_uses_page_as_orientation_hub(
    django_server, browser, django_db_blocker
):
    email = _unique_email("community-main")
    with django_db_blocker.unblock():
        _seed_base()
        create_user(email, tier_slug="main")

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
        assert response.status == 200
        body = page.content()
        assert "Register free" not in body
        assert "Subscribe for updates" not in body
        for label in [
            "Community sprints",
            "Events",
            "Workshops",
            "Activities by tier",
        ]:
            expect(page.get_by_role("link", name=label).first).to_be_visible()

        page.get_by_test_id("community-link-sprints").click()
        page.wait_for_url("**/sprints")
        assert "/accounts/login/" not in page.url
    finally:
        context.close()


def test_desktop_navigation_discovers_community_overview(django_server, page):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")
    assert _menu_links(menu)[:5] == [
        ["nav-community-link-overview", "/community", "Overview"],
        ["nav-community-link-membership", "/pricing", "Membership"],
        ["nav-community-link-activities", "/activities#access-by-tier", "Activities"],
        ["nav-community-link-sprints", "/sprints", "Community Sprints"],
        ["nav-community-link-events", "/events", "Events"],
    ]

    page.get_by_test_id("nav-community-link-overview").click()
    page.wait_for_url("**/community")
    expect(page.get_by_test_id("community-landing-heading")).to_be_visible()


def test_mobile_navigation_discovers_community_overview(django_server, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        menu = _open_mobile_community_menu(page)
        _shot(page, "mobile-menu")
        assert _menu_links(menu)[:5] == [
            ["mobile-nav-community-link-overview", "/community", "Overview"],
            ["mobile-nav-community-link-membership", "/pricing", "Membership"],
            [
                "mobile-nav-community-link-activities",
                "/activities#access-by-tier",
                "Activities",
            ],
            ["mobile-nav-community-link-sprints", "/sprints", "Community Sprints"],
            ["mobile-nav-community-link-events", "/events", "Events"],
        ]

        page.get_by_test_id("mobile-nav-community-link-overview").click()
        page.wait_for_url("**/community")
        expect(page.get_by_test_id("community-landing-heading")).to_be_visible()
    finally:
        context.close()


def test_activity_config_fallback_stays_useful_without_javascript_errors(
    django_server, page, django_db_blocker
):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    with django_db_blocker.unblock():
        _seed_base(with_activity_config=False)

    response = page.goto(f"{django_server}/community", wait_until="networkidle")
    assert response.status == 200
    _shot(page, "fallback")

    expect(page.get_by_test_id("community-activity-fallback")).to_be_visible()
    expect(page.get_by_test_id("community-activity-fallback")).to_contain_text(
        "Membership activities are being refreshed"
    )
    assert page.locator('[data-testid="community-tier-card"]').count() == 0
    assert page.locator('[data-testid="community-activity-fallback"] a[href="/pricing"]').count() == 1
    assert page.locator(
        '[data-testid="community-activity-fallback"] a[href="/activities#access-by-tier"]'
    ).count() == 1
    assert errors == []


def test_public_page_protects_private_slack_access_details(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()

    response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    assert response.status == 200

    body = page.content().lower()
    assert "private slack community access" in body
    for forbidden in [
        "/community/slack",
        "join.slack.com",
        "slack.com/invite",
        "hooks.slack.com",
        "when we launch",
        "community launch",
        "invite-only",
    ]:
        assert forbidden not in body
