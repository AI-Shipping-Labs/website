"""Public mobile header/footer tap-target coverage for issue #1214."""

from __future__ import annotations

import os
import uuid

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]

MOBILE = {"width": 390, "height": 844}
TAP_TARGET_MIN = 44


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _open_mobile_menu(page):
    page.locator("#mobile-menu-btn").click()
    page.locator("#mobile-menu:not(.hidden)").wait_for(
        state="visible", timeout=3000
    )


def _expand_mobile_section(page, section):
    trigger = page.locator(f'[data-testid="mobile-nav-{section}-trigger"]')
    menu = page.locator(f'[data-testid="mobile-nav-{section}-menu"]')
    assert trigger.get_attribute("aria-expanded") == "false"
    trigger.click()
    menu.wait_for(state="visible", timeout=3000)
    assert trigger.get_attribute("aria-expanded") == "true"
    return menu


def _visible_boxes(page, selector):
    return page.evaluate(
        """
        selector => Array.from(document.querySelectorAll(selector))
            .map((el, index) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const visible = style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
                return {
                    index,
                    visible,
                    tag: el.tagName.toLowerCase(),
                    testid: el.dataset.testid || '',
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim(),
                    href: el.getAttribute('href') || '',
                    width: rect.width,
                    height: rect.height,
                };
            })
            .filter(item => item.visible)
        """,
        selector,
    )


def _assert_min_height_for_visible(page, selector, label):
    boxes = _visible_boxes(page, selector)
    assert boxes, f"No visible targets found for {label}"
    too_small = [
        box for box in boxes if box["height"] < TAP_TARGET_MIN
    ]
    assert not too_small, (
        f"{label} has targets below {TAP_TARGET_MIN}px: {too_small}"
    )
    return boxes


def _assert_target_size(locator, label, *, width=False):
    box = locator.bounding_box()
    assert box is not None, f"{label} did not render a box"
    assert box["height"] >= TAP_TARGET_MIN, (
        f"{label} height {box['height']}px is below {TAP_TARGET_MIN}px"
    )
    if width:
        assert box["width"] >= TAP_TARGET_MIN, (
            f"{label} width {box['width']}px is below {TAP_TARGET_MIN}px"
        )


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """
        () => Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth
        ) - window.innerWidth
        """
    )
    assert overflow <= 1, f"Expected no horizontal overflow, got {overflow}px"


def _assert_footer_targets(page):
    page.locator("footer").scroll_into_view_if_needed()
    targets = [
        ('footer a[href="/"]', "footer logo/home"),
        ('footer a[href="/about"]', "footer About"),
        ('footer a[href="/pricing"]', "footer Membership Tiers"),
        ('footer a[href="/faq"]', "footer FAQ"),
        ('footer a', "footer Manage Subscription"),
        ('footer a[href="/terms/"]', "footer Terms of Service"),
        ('footer a[href="/privacy/"]', "footer Privacy Policy"),
        ('footer a[href="/impressum/"]', "footer Impressum"),
    ]
    for selector, label in targets:
        locator = page.locator(selector)
        if label == "footer Manage Subscription":
            locator = page.get_by_role("link", name="Manage Subscription")
        _assert_target_size(locator, label)


def _seed_current_plan(email):
    from accounts.models import User
    from plans.models import Plan, Sprint, SprintEnrollment

    user = User.objects.get(email=email)
    today = timezone.localdate()
    sprint = Sprint.objects.create(
        name="Issue 1214 Active Sprint",
        slug=f"issue-1214-active-{uuid.uuid4().hex[:8]}",
        start_date=today,
        duration_weeks=6,
        status="active",
        min_tier_level=20,
    )
    SprintEnrollment.objects.create(sprint=sprint, user=user)
    plan = Plan.objects.create(
        member=user,
        sprint=sprint,
        goal="Keep mobile targets accessible",
    )
    expected_path = reverse(
        "my_plan_detail",
        kwargs={"sprint_slug": sprint.slug, "plan_id": plan.pk},
    )
    connection.close()
    return expected_path


def test_anonymous_mobile_menu_targets_and_navigation(django_server, browser):
    context = browser.new_context(viewport=MOBILE)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _assert_target_size(
            page.locator("#mobile-menu-btn"),
            "mobile menu button",
            width=True,
        )
        _open_mobile_menu(page)
        _assert_target_size(
            page.locator('#mobile-menu [data-testid="theme-toggle"]'),
            "mobile theme toggle",
            width=True,
        )

        about = _expand_mobile_section(page, "about")
        assert [
            about.locator(f'[data-testid="mobile-nav-about-link-{name}"]').get_attribute("href")
            for name in ("team", "faq")
        ] == ["/about", "/faq"]

        _expand_mobile_section(page, "community")
        resources = _expand_mobile_section(page, "resources")
        for testid in (
            "mobile-nav-resources-link-courses",
            "mobile-nav-resources-link-workshops",
        ):
            assert resources.locator(f'[data-testid="{testid}"]').is_visible()

        _assert_min_height_for_visible(
            page,
            "#mobile-menu button, #mobile-menu a",
            "anonymous mobile menu",
        )

        sign_in = page.locator('#mobile-menu [data-testid="header-sign-in-link"]')
        _assert_target_size(sign_in, "mobile Sign in")
        sign_in.click()
        page.wait_for_url(f"{django_server}/accounts/login/**", timeout=5000)
    finally:
        context.close()


def test_anonymous_mobile_menu_links_reach_existing_destinations(
    django_server, browser
):
    context = browser.new_context(viewport=MOBILE)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_mobile_menu(page)
        _expand_mobile_section(page, "community")

        membership = page.locator(
            '[data-testid="mobile-nav-community-link-membership"]'
        )
        _assert_target_size(membership, "mobile Membership link")
        membership.click()
        page.wait_for_url(f"{django_server}/pricing", timeout=5000)

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_mobile_menu(page)
        _expand_mobile_section(page, "resources")
        courses = page.locator('[data-testid="mobile-nav-resources-link-courses"]')
        _assert_target_size(courses, "mobile Courses link")
        courses.click()
        page.wait_for_url(f"{django_server}/courses", timeout=5000)
    finally:
        context.close()


def test_expanded_mobile_menu_has_no_horizontal_overflow_and_scrolls(
    django_server, browser
):
    context = browser.new_context(viewport=MOBILE)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_mobile_menu(page)
        for section in ("about", "community", "resources"):
            _expand_mobile_section(page, section)

        _assert_min_height_for_visible(
            page,
            "#mobile-menu button, #mobile-menu a",
            "fully expanded anonymous mobile menu",
        )
        _assert_no_horizontal_overflow(page)

        scroll_state = page.evaluate(
            """
            () => {
                const menu = document.getElementById('mobile-menu');
                const style = window.getComputedStyle(menu);
                return {
                    clientHeight: menu.clientHeight,
                    scrollHeight: menu.scrollHeight,
                    overflowY: style.overflowY,
                    bottom: menu.getBoundingClientRect().bottom,
                    viewportHeight: window.innerHeight,
                };
            }
            """
        )
        assert scroll_state["overflowY"] in ("auto", "scroll")
        assert scroll_state["scrollHeight"] > scroll_state["clientHeight"]
        assert scroll_state["bottom"] <= scroll_state["viewportHeight"]
    finally:
        context.close()


def test_signed_in_mobile_account_targets_and_account_navigation(
    django_server, browser
):
    email = _email("tap-free-1214")
    create_user(email=email, tier_slug="free", first_name="Ada")
    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size(MOBILE)
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_mobile_menu(page)

        selectors = {
            "Notifications": '[data-testid="mobile-notifications-link"]',
            "Account": "#mobile-account-link",
            "Log out": '#mobile-menu a[href="/accounts/logout/"]',
        }
        for label, selector in selectors.items():
            _assert_target_size(page.locator(selector), f"mobile {label}")

        _assert_min_height_for_visible(
            page,
            "#mobile-menu button, #mobile-menu a",
            "signed-in mobile account menu",
        )

        page.locator("#mobile-account-link").click()
        page.wait_for_url(f"{django_server}/account/", timeout=5000)
    finally:
        context.close()


def test_main_member_mobile_plan_shortcut_target_and_navigation(
    django_server, browser
):
    email = _email("tap-main-1214")
    create_user(email=email, tier_slug="main", first_name="Max")
    expected_path = _seed_current_plan(email)

    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size(MOBILE)
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _open_mobile_menu(page)

        plan = page.locator('[data-testid="mobile-header-plan-link"]')
        _assert_target_size(plan, "mobile Plan shortcut")
        assert plan.inner_text().strip() == "Plan"
        assert plan.get_attribute("href") == expected_path
        plan.click()
        page.wait_for_url(f"{django_server}{expected_path}", timeout=5000)
    finally:
        context.close()


def test_mobile_footer_targets_navigation_and_newsletter_suppression(
    django_server, browser
):
    context = browser.new_context(viewport=MOBILE)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        assert page.locator("#newsletter").count() == 1
        _assert_footer_targets(page)
        _assert_no_horizontal_overflow(page)

        page.get_by_role("link", name="Membership Tiers").click()
        page.wait_for_url(f"{django_server}/pricing", timeout=5000)

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        page.locator("footer").scroll_into_view_if_needed()
        privacy = page.locator("footer").get_by_role(
            "link", name="Privacy Policy", exact=True
        )
        _assert_target_size(privacy, "footer Privacy Policy")
        privacy.click()
        page.wait_for_url(f"{django_server}/privacy", timeout=5000)

        page.goto(f"{django_server}/subscribe", wait_until="domcontentloaded")
        assert page.locator("form.subscribe-form").count() == 1
        assert page.locator("#newsletter").count() == 0
        assert page.locator("body").get_by_text(
            "Build AI in public, with a group."
        ).count() == 0
        _assert_footer_targets(page)
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()
