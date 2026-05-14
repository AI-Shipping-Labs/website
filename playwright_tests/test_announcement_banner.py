"""Playwright E2E tests for the site-wide announcement banner (Issue #190).

Covers browser-valued scenarios from the issue spec:
1. Visitor sees a launch announcement above the header
2. Banner appears across all public pages
3. Disabled banner is invisible to visitors
4. Visitor dismisses the banner and it stays gone on reload
5. Edited banner re-shows to a user who previously dismissed it
6. Non-dismissible banner has no close button
8. Banner does not appear in Studio
9. Staff configures a new banner and sees it instantly on the public site
10. Staff disables the banner and it disappears site-wide
11. Studio editor shows a live preview matching what users see
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
    expand_studio_sidebar_section as _expand_studio_sidebar_section,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BANNER_SELECTOR = '[data-testid="announcement-banner"]'


def _reset_banner():
    """Delete the AnnouncementBanner row and clear the in-process cache.

    Closes the DB connection so the server thread can read fresh state.
    """
    from integrations.middleware import clear_announcement_banner_cache
    from integrations.models import AnnouncementBanner

    AnnouncementBanner.objects.all().delete()
    clear_announcement_banner_cache()
    connection.close()


def _set_banner(
    *,
    message,
    link_url="",
    link_label="Read more",
    is_enabled=True,
    is_dismissible=True,
):
    """Create or update the singleton banner. Bumps version on text change."""
    from integrations.middleware import clear_announcement_banner_cache
    from integrations.models import AnnouncementBanner

    banner = AnnouncementBanner.get_singleton()
    if message != banner.message or link_url != banner.link_url:
        banner.version = banner.version + 1
    banner.message = message
    banner.link_url = link_url
    banner.link_label = link_label
    banner.is_enabled = is_enabled
    banner.is_dismissible = is_dismissible
    banner.save()
    clear_announcement_banner_cache()
    connection.close()
    return banner


# ---------------------------------------------------------------------------
# Scenario 1: Visitor sees a launch announcement above the header
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorSeesLaunch:
    def test_banner_above_nav_with_link(self, django_server, page):
        _reset_banner()
        _set_banner(
            message="AI Shipping Labs launched - early members get extra onboarding benefits.",
            link_url="/events/launch-recap",
            link_label="Read more",
            is_dismissible=True,
        )

        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        banner = page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)

        # Banner copy is present.
        assert banner.locator("text=AI Shipping Labs launched").count() == 1
        # Link suffix is rendered with accent-underline classes.
        assert banner.locator("text=Read more").count() == 1
        # Banner sits above the <nav> in the DOM (header > banner > nav).
        # Use evaluate to confirm the banner element comes before the first <nav>
        # within the same <header>.
        ordered = page.evaluate(
            """() => {
                const header = document.querySelector('header');
                if (!header) return null;
                const banner = header.querySelector('[data-testid="announcement-banner"]');
                const nav = header.querySelector('nav');
                if (!banner || !nav) return null;
                return banner.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING;
            }"""
        )
        assert ordered, "Banner must precede <nav> inside <header>"

    def test_banner_link_navigates(self, django_server, page):
        _reset_banner()
        _set_banner(
            message="Launch announcement",
            link_url="/about",
            link_label="Read more",
        )
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        banner = page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)
        banner.click()
        page.wait_for_url(f"{django_server}/about", timeout=5000)
        assert page.url.endswith("/about")


# ---------------------------------------------------------------------------
# Scenario 2: Banner appears across all public pages
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AllPublicPages:
    def test_banner_on_home_blog_pricing_about(self, django_server, page):
        _reset_banner()
        _set_banner(message="Cross-page banner", link_url="/about", link_label="Read more")

        for path in ["/", "/blog", "/pricing", "/about"]:
            response = page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
            # We tolerate any page that successfully loads (200 or 304).
            assert response.status < 400, f"{path} returned {response.status}"
            banner = page.locator(BANNER_SELECTOR)
            assert banner.count() >= 1, f"Banner missing on {path}"
            assert banner.first.is_visible(), f"Banner not visible on {path}"


# ---------------------------------------------------------------------------
# Scenario 3: Disabled banner is invisible to visitors
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3DisabledBannerHidden:
    def test_disabled_banner_does_not_render(self, django_server, page):
        _reset_banner()
        _set_banner(message="I am hidden", is_enabled=False)

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator(BANNER_SELECTOR).count() == 0

        page.goto(f"{django_server}/blog", wait_until="domcontentloaded")
        assert page.locator(BANNER_SELECTOR).count() == 0


# ---------------------------------------------------------------------------
# Scenario 4: Visitor dismisses the banner and it stays gone on reload
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4DismissAndStayGone:
    def test_dismiss_persists_via_cookie(self, django_server, browser):
        _reset_banner()
        _set_banner(message="Dismiss me", is_dismissible=True)

        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        banner = page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)

        close_btn = page.locator("#announcement-banner-close")
        assert close_btn.count() == 1
        close_btn.click()

        # Banner element should be hidden (display:none) without a navigation.
        page.wait_for_function(
            "() => { var w = document.getElementById('announcement-banner-wrapper'); return w && w.style.display === 'none'; }",
            timeout=2000,
        )

        # Reload — banner should not reappear thanks to the dismissal cookie.
        page.reload(wait_until="domcontentloaded")
        # The wrapper still renders server-side, but JS hides it on load.
        page.wait_for_function(
            "() => { var w = document.getElementById('announcement-banner-wrapper'); return !w || w.style.display === 'none'; }",
            timeout=2000,
        )
        wrapper = page.locator("#announcement-banner-wrapper")
        if wrapper.count() > 0:
            display = wrapper.evaluate("el => el.style.display")
            assert display == "none"
        context.close()


# ---------------------------------------------------------------------------
# Scenario 5: Edited banner re-shows to a user who previously dismissed it
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5EditedBannerReshowsAfterDismiss:
    def test_message_change_bumps_version_and_reshows(self, django_server, browser):
        _reset_banner()
        _set_banner(message="Original message", is_dismissible=True)

        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        page.locator(BANNER_SELECTOR).wait_for(state="visible", timeout=5000)
        page.locator("#announcement-banner-close").click()

        page.wait_for_function(
            "() => { var w = document.getElementById('announcement-banner-wrapper'); return w && w.style.display === 'none'; }",
            timeout=2000,
        )

        # Simulate staff edit: bump version and update message.
        _set_banner(message="Updated message", is_dismissible=True)

        page.reload(wait_until="domcontentloaded")
        # The new banner version uses a fresh cookie key, so it shows again.
        banner = page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)
        assert "Updated message" in banner.inner_text()
        context.close()


# ---------------------------------------------------------------------------
# Scenario 6: Non-dismissible banner has no close button
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6NoCloseWhenNotDismissible:
    def test_close_button_absent(self, django_server, page):
        _reset_banner()
        _set_banner(message="Cannot dismiss", is_dismissible=False)
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        banner = page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)
        assert page.locator("#announcement-banner-close").count() == 0


# ---------------------------------------------------------------------------
# Scenario 8: Banner does not appear in Studio
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8NotInStudio:
    def test_banner_hidden_in_studio_pages(self, django_server, browser):
        _reset_banner()
        _set_banner(message="Should not show in studio")
        _create_staff_user("staff-banner@test.com")

        context = _auth_context(browser, "staff-banner@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        assert page.locator(BANNER_SELECTOR).count() == 0

        page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")
        assert page.locator(BANNER_SELECTOR).count() == 0
        context.close()


# ---------------------------------------------------------------------------
# Scenario 9: Staff configures a new banner and sees it instantly on the site
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9StaffConfiguresAndPublishes:
    def test_studio_create_then_visible_on_homepage(self, django_server, browser):
        _reset_banner()
        _create_staff_user("staff-publish@test.com")

        staff_ctx = _auth_context(browser, "staff-publish@test.com")
        staff_page = staff_ctx.new_page()

        # Sidebar shows Site banner entry under Communication.
        staff_page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        _expand_studio_sidebar_section(staff_page, "communication")
        ann_link = staff_page.locator('a[href="/studio/announcement/"]')
        assert ann_link.count() >= 1

        ann_link.first.click()
        staff_page.wait_for_url(f"{django_server}/studio/announcement/", timeout=5000)

        # Empty state: message empty, enabled unchecked, dismissible checked.
        message_field = staff_page.locator('textarea[name="message"]')
        assert message_field.input_value() == ""
        assert not staff_page.locator('input[name="is_enabled"]').is_checked()
        assert staff_page.locator('input[name="is_dismissible"]').is_checked()

        # Fill in and submit.
        message_field.fill("Spring cohort registrations close Friday")
        staff_page.locator('input[name="link_url"]').fill("/courses")
        staff_page.locator('input[name="link_label"]').fill("Reserve your seat")
        staff_page.locator('input[name="is_enabled"]').check()
        staff_page.locator('button[type="submit"]').click()

        staff_page.wait_for_url(f"{django_server}/studio/announcement/", timeout=5000)
        assert "Announcement banner saved" in staff_page.content()

        # Anonymous visitor in a fresh context sees the new banner.
        anon_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon_ctx.new_page()
        anon_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        banner = anon_page.locator(BANNER_SELECTOR)
        banner.wait_for(state="visible", timeout=5000)
        assert "Spring cohort registrations close Friday" in banner.inner_text()
        assert "Reserve your seat" in banner.inner_text()
        anon_ctx.close()
        staff_ctx.close()


# ---------------------------------------------------------------------------
# Scenario 10: Staff disables banner -> disappears site-wide
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10StaffDisablesBanner:
    def test_uncheck_enabled_removes_banner(self, django_server, browser):
        _reset_banner()
        _set_banner(message="Visible for now", is_enabled=True, is_dismissible=True)
        _create_staff_user("staff-disable@test.com")

        # Confirm visible first as anonymous user.
        anon_ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon_ctx.new_page()
        anon_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        anon_page.locator(BANNER_SELECTOR).wait_for(state="visible", timeout=5000)

        # Staff disables it via Studio.
        staff_ctx = _auth_context(browser, "staff-disable@test.com")
        staff_page = staff_ctx.new_page()
        staff_page.goto(f"{django_server}/studio/announcement/", wait_until="domcontentloaded")
        # Uncheck enabled and submit.
        enabled = staff_page.locator('input[name="is_enabled"]')
        if enabled.is_checked():
            enabled.uncheck()
        staff_page.locator('button[type="submit"]').click()
        staff_page.wait_for_url(f"{django_server}/studio/announcement/", timeout=5000)

        # Anonymous reload no longer sees the banner.
        anon_page.reload(wait_until="domcontentloaded")
        assert anon_page.locator(BANNER_SELECTOR).count() == 0

        anon_ctx.close()
        staff_ctx.close()


# ---------------------------------------------------------------------------
# Scenario 11: Studio editor shows a live preview matching what users see
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11StudioPreviewMatches:
    def test_preview_block_shows_same_message_and_dot(self, django_server, browser):
        _reset_banner()
        _set_banner(
            message="Preview parity check",
            link_url="/about",
            link_label="Learn more",
            is_enabled=True,
            is_dismissible=True,
        )
        _create_staff_user("staff-preview@test.com")
        staff_ctx = _auth_context(browser, "staff-preview@test.com")
        staff_page = staff_ctx.new_page()

        staff_page.goto(f"{django_server}/studio/announcement/", wait_until="domcontentloaded")
        preview = staff_page.locator('[data-testid="announcement-banner-preview"]')
        preview.wait_for(state="visible", timeout=5000)
        text = preview.inner_text()
        assert "Preview parity check" in text
        assert "Learn more" in text
        # The accent dot is rendered as an empty span with the accent class.
        assert preview.locator("span.bg-accent").count() >= 1
        staff_ctx.close()
