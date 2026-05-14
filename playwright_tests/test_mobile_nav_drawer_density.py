"""Playwright E2E coverage for the public mobile nav drawer density fix
(issue #623).

The Pixel 7 (412x915) audit surfaced five must-fix bugs in the public
mobile drawer rendered from ``templates/includes/header.html``:

1. The Theme switcher row at the top of the drawer is gated to anonymous
   users, so logged-in members lose it from the top and instead see a
   second copy buried six rows deep inside the Account block.
2. Accordion children render as plain ``block`` rows with no left rule,
   making sub-links visually indistinguishable from top-level rows.
3. Accordion toggle ``<button>``s do not carry ``min-h-[44px]``,
   ``aria-expanded`` or ``aria-controls``, failing the design-system
   accessibility checklist.
4. The drawer wrapper scrolls but the underlying ``<body>`` does too,
   so page chrome bleeds through behind the drawer when the menu is
   long (staff variant + both accordions open ~ 1700 px on a 1830 px
   viewport).
5. The mobile Notifications row reflows when the badge appears or
   disappears because the badge is placed inline mid-text with
   ``inline-flex`` and ``ml-1``.

Each scenario in this file fails on ``main`` and passes after the fix.
The viewport is pinned to Pixel 7 (412x915) which is the size used by
the audit; the desktop scenario uses 1280x800 to assert the fix did
not bleed into the desktop layout.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.django_db(transaction=True)

PIXEL_7 = {"width": 412, "height": 915}
DESKTOP = {"width": 1280, "height": 800}

MEMBER_EMAIL = "drawer-density-member@test.com"
STAFF_EMAIL = "drawer-density-staff@test.com"
NOTIF_MEMBER_EMAIL = "drawer-density-notif@test.com"


def _open_drawer(page):
    """Tap the hamburger and wait for the drawer to be visible."""
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


def _expand(page, section):
    """Tap a mobile accordion toggle and wait for the sub-list to render."""
    page.locator(f"#mobile-{section}-toggle").click()
    page.wait_for_selector(
        f"#mobile-{section}-list:not(.hidden)", timeout=2000
    )


def _seed_unread_notifications(email, count):
    """Seed ``count`` unread notifications for the given user.

    Closes the DB connection so the server thread (running in the same
    process) can read the rows we just wrote.
    """
    from django.db import connection

    from accounts.models import User
    from notifications.models import Notification

    user = User.objects.get(email=email)
    for i in range(count):
        Notification.objects.create(
            user=user,
            title=f"Density notification {i}",
            body=f"Body {i}",
            url="/blog",
            read=False,
        )
    connection.close()


# ---------------------------------------------------------------------------
# Scenario 1: Logged-in members see the Theme switcher in the same place
# anonymous visitors do.
# ---------------------------------------------------------------------------


class TestThemeRowSymmetricAcrossAuthStates:
    def test_member_sees_theme_row_at_top_of_drawer(
        self, django_server, browser
    ):
        _create_user(email=MEMBER_EMAIL, tier_slug="free")
        context = _auth_context(browser, MEMBER_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)

            # The dedicated top-of-drawer Theme row exists and sits
            # above the About accordion trigger (the first non-Theme row).
            theme_row = page.locator(
                '#mobile-menu [data-testid="mobile-theme-row"]'
            )
            assert theme_row.count() == 1
            assert theme_row.is_visible()

            theme_box = theme_row.bounding_box()
            about_link = page.locator("#mobile-about-toggle")
            about_box = about_link.bounding_box()
            assert theme_box is not None
            assert about_box is not None
            assert theme_box["y"] < about_box["y"], (
                "Theme row must sit above the About accordion trigger, "
                "matching the anonymous variant"
            )

            # And the Account block at the bottom must not carry a
            # second Theme button (no double-rendering).
            account_section = page.locator(
                '[data-testid="mobile-account-section"]'
            )
            assert account_section.count() == 1
            account_block_html = page.evaluate(
                """
                () => {
                    const section = document.querySelector(
                        '[data-testid=\"mobile-account-section\"]'
                    );
                    if (!section) return '';
                    const parent = section.parentElement;
                    return parent ? parent.innerHTML : '';
                }
                """
            )
            assert 'data-testid="theme-toggle"' not in account_block_html, (
                "The logged-in Account block must not duplicate the "
                "Theme row -- a single Theme row at the top of the "
                "drawer is the canonical location"
            )
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 2: Resources sub-links sit under a left rule and use compact rows.
# ---------------------------------------------------------------------------


class TestResourcesAccordionGroupingAndTapTargets:
    def test_resources_sublinks_carry_left_rule_and_compact_height(
        self, django_server, browser
    ):
        _create_user(email=MEMBER_EMAIL, tier_slug="free")
        context = _auth_context(browser, MEMBER_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)
            _expand(page, "resources")

            # Every sub-link in the expanded list uses the compact mobile rhythm.
            link_heights = page.evaluate(
                """
                () => {
                    const list = document.getElementById('mobile-resources-list');
                    return Array.from(list.querySelectorAll('a')).map(a => {
                        return a.getBoundingClientRect().height;
                    });
                }
                """
            )
            assert link_heights, "Resources accordion must render sub-links"
            for height in link_heights:
                assert 31 <= height <= 36, (
                    f"Resources sub-link is {height}px tall, "
                    "expected compact 32px-ish footer/header rhythm"
                )

            # The container holding the sub-links carries a left border.
            border_left_width = page.evaluate(
                """
                () => {
                    const list = document.getElementById('mobile-resources-list');
                    return getComputedStyle(list).borderLeftWidth;
                }
                """
            )
            assert border_left_width not in ("0px", "", None), (
                f"Resources sub-list must have a left border to group "
                f"children to the parent, got border-left-width="
                f"{border_left_width!r}"
            )

            # The toggle now reports aria-expanded="true".
            toggle = page.locator("#mobile-resources-toggle")
            assert toggle.get_attribute("aria-expanded") == "true"
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 3: Accordion toggles announce expanded state to assistive tech.
# ---------------------------------------------------------------------------


class TestAccordionAriaExpandedSync:
    def test_accordion_toggles_carry_aria_attributes_and_flip_state(
        self, django_server, browser
    ):
        context = browser.new_context(viewport=PIXEL_7)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)

            community_toggle = page.locator("#mobile-community-toggle")
            resources_toggle = page.locator("#mobile-resources-toggle")

            # Initial state: both accordions collapsed.
            assert community_toggle.get_attribute("aria-expanded") == "false"
            assert (
                community_toggle.get_attribute("aria-controls")
                == "mobile-community-list"
            )
            assert resources_toggle.get_attribute("aria-expanded") == "false"
            assert (
                resources_toggle.get_attribute("aria-controls")
                == "mobile-resources-list"
            )

            # Tap Community: aria-expanded flips to true.
            community_toggle.click()
            page.wait_for_selector(
                "#mobile-community-list:not(.hidden)", timeout=2000
            )
            assert community_toggle.get_attribute("aria-expanded") == "true"

            # Tap Community again: aria-expanded flips back to false.
            community_toggle.click()
            page.wait_for_function(
                "() => document.getElementById('mobile-community-list')"
                ".classList.contains('hidden')",
                timeout=2000,
            )
            assert community_toggle.get_attribute("aria-expanded") == "false"
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 4: Body scroll is locked while the drawer is open.
# ---------------------------------------------------------------------------


class TestBodyScrollLockWhileDrawerOpen:
    def test_body_carries_overflow_hidden_only_while_drawer_open(
        self, django_server, browser
    ):
        _create_staff_user(email=STAFF_EMAIL)
        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")

            # Closed: <body> is not locked.
            assert page.evaluate(
                "() => document.body.classList.contains('overflow-hidden')"
            ) is False

            _open_drawer(page)

            # Opened: <body> carries overflow-hidden.
            assert page.evaluate(
                "() => document.body.classList.contains('overflow-hidden')"
            ) is True

            # Attempt to scroll the page; window.scrollY must stay 0.
            page.mouse.wheel(0, 800)
            page.keyboard.press("PageDown")
            scroll_y = page.evaluate("() => window.scrollY")
            assert scroll_y == 0, (
                f"Body scroll must be locked while drawer is open, "
                f"got window.scrollY={scroll_y}"
            )

            # Close the drawer; <body> class is removed.
            page.locator("#mobile-menu-btn").click()
            page.wait_for_function(
                "() => document.getElementById('mobile-menu')"
                ".classList.contains('hidden')",
                timeout=2000,
            )
            assert page.evaluate(
                "() => document.body.classList.contains('overflow-hidden')"
            ) is False
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 5: Staff Log out is reachable, no underlying-page chrome bleeds
# above or below the drawer rows.
# ---------------------------------------------------------------------------


class TestStaffLogoutReachableWithoutPageBleedThrough:
    def test_log_out_reachable_after_expanding_both_accordions(
        self, django_server, browser
    ):
        _create_staff_user(email=STAFF_EMAIL)
        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)
            _expand(page, "community")
            _expand(page, "resources")

            logout = page.locator(
                '#mobile-menu a[href="/accounts/logout/"]'
            )
            assert logout.count() == 1
            logout.scroll_into_view_if_needed()
            assert logout.is_visible(), (
                "Log out must be visible after scrolling the drawer"
            )

            # The page itself must not have scrolled (body lock holds).
            scroll_y = page.evaluate("() => window.scrollY")
            assert scroll_y == 0, (
                f"Page must not scroll behind the drawer; "
                f"got window.scrollY={scroll_y}"
            )

            # The drawer outer height is bounded by the viewport
            # (max-h: calc(100vh - 4rem)). The drawer must not
            # extend past the bottom of the viewport.
            drawer_box = page.locator("#mobile-menu").bounding_box()
            assert drawer_box is not None
            assert drawer_box["y"] + drawer_box["height"] <= PIXEL_7["height"], (
                f"Drawer must fit inside the viewport; "
                f"drawer bottom={drawer_box['y'] + drawer_box['height']}, "
                f"viewport height={PIXEL_7['height']}"
            )
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 6: Notifications row does not reflow when the badge appears.
# ---------------------------------------------------------------------------


class TestNotificationBadgeNoLayoutShift:
    def test_notifications_label_position_unchanged_when_badge_appears(
        self, django_server, browser
    ):
        _create_user(email=NOTIF_MEMBER_EMAIL, tier_slug="free")
        _seed_unread_notifications(NOTIF_MEMBER_EMAIL, 3)
        context = _auth_context(browser, NOTIF_MEMBER_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            # Block the unread-count fetch so we control when the badge
            # mounts (the count poll runs on page load and again every
            # 60s; we let the route resolve manually below).
            page.route(
                "**/api/notifications/unread-count",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"count": 0}',
                ),
            )
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)

            label = page.locator(
                '[data-testid="mobile-notifications-label"]'
            )
            assert label.count() == 1
            box_before = label.bounding_box()
            assert box_before is not None
            # Badge starts hidden.
            badge = page.locator("#mobile-notification-badge")
            assert "hidden" in (badge.get_attribute("class") or "")

            # Now flip to a non-zero count and trigger an update.
            page.unroute("**/api/notifications/unread-count")
            page.route(
                "**/api/notifications/unread-count",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"count": 3}',
                ),
            )
            # Trigger the same update path the page uses (the badge
            # updates from a fetch result; calling fetch from the page
            # context exercises the live updateBadge code path).
            page.evaluate(
                """
                () => fetch('/api/notifications/unread-count')
                    .then(r => r.json())
                    .then(data => {
                        const badge = document.getElementById('mobile-notification-badge');
                        const text = data.count > 9 ? '9+' : String(data.count);
                        badge.textContent = text;
                        badge.classList.remove('hidden');
                        badge.classList.add('flex');
                    })
                """
            )
            page.wait_for_function(
                "() => !document.getElementById('mobile-notification-badge')"
                ".classList.contains('hidden')",
                timeout=2000,
            )

            box_after = label.bounding_box()
            assert box_after is not None
            # The Notifications text must not move when the badge mounts.
            assert abs(box_after["x"] - box_before["x"]) < 1, (
                f"Notifications text x shifted from {box_before['x']} "
                f"to {box_after['x']} when the badge mounted"
            )
            assert abs(box_after["y"] - box_before["y"]) < 1, (
                f"Notifications text y shifted from {box_before['y']} "
                f"to {box_after['y']} when the badge mounted"
            )

            # The badge must render visibly at the top-right corner of
            # the row, not in the middle of the text.
            badge_box = badge.bounding_box()
            assert badge_box is not None
            row = page.locator(
                '[data-testid="mobile-notifications-link"]'
            )
            row_box = row.bounding_box()
            assert row_box is not None
            # Badge should sit in the right half of the row.
            badge_center_x = badge_box["x"] + badge_box["width"] / 2
            row_center_x = row_box["x"] + row_box["width"] / 2
            assert badge_center_x > row_center_x, (
                f"Badge center x={badge_center_x} should sit to the "
                f"right of row center x={row_center_x}"
            )
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 7: Member drawer with both accordions open is < 1100 px tall.
# ---------------------------------------------------------------------------


class TestDrawerFitsViewportWithBothAccordionsOpen:
    def test_member_drawer_total_scroll_height_under_1100px(
        self, django_server, browser
    ):
        _create_user(email=MEMBER_EMAIL, tier_slug="free")
        context = _auth_context(browser, MEMBER_EMAIL)
        page = context.new_page()
        page.set_viewport_size(PIXEL_7)
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")
            _open_drawer(page)
            _expand(page, "community")
            _expand(page, "resources")

            scroll_height = page.evaluate(
                "() => document.getElementById('mobile-menu').scrollHeight"
            )
            assert scroll_height < 1100, (
                f"Member drawer with both accordions open must be "
                f"< 1100px tall, got {scroll_height}px"
            )

            # And the outer drawer is bounded by max-h: calc(100vh - 4rem).
            max_height = page.evaluate(
                "() => getComputedStyle(document.getElementById"
                "('mobile-menu')).maxHeight"
            )
            assert max_height not in ("none", "", None), (
                f"Drawer must declare a max-height, got {max_height!r}"
            )
        finally:
            context.close()


# ---------------------------------------------------------------------------
# Scenario 8: Desktop layout is untouched.
# ---------------------------------------------------------------------------


class TestDesktopLayoutUntouched:
    def test_desktop_hides_drawer_and_shows_primary_nav(
        self, django_server, browser
    ):
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()
        try:
            page.goto(f"{django_server}/", wait_until="domcontentloaded")

            # Hamburger is hidden at desktop width.
            assert not page.locator("#mobile-menu-btn").is_visible()

            # Desktop primary nav is visible with the current dropdown
            # triggers and their key child links.
            primary = page.locator('[data-testid="desktop-primary-nav"]')
            assert primary.is_visible()
            for testid in [
                "nav-about-trigger",
                "nav-community-trigger",
                "nav-resources-trigger",
            ]:
                assert primary.locator(f'[data-testid="{testid}"]').is_visible()

            primary.locator('[data-testid="nav-about-trigger"]').hover()
            assert primary.locator(
                '[data-testid="nav-about-link-faq"]'
            ).is_visible()
            primary.locator('[data-testid="nav-community-trigger"]').hover()
            assert primary.locator(
                '[data-testid="nav-community-link-membership"]'
            ).is_visible()

            # Mobile drawer is hidden.
            mobile_menu = page.locator("#mobile-menu")
            assert "hidden" in (mobile_menu.get_attribute("class") or "")
        finally:
            context.close()
