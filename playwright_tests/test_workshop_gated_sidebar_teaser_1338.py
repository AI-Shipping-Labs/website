"""Playwright E2E tests for issue #1338.

Show the workshop tutorial page-navigation sidebar to gated/anonymous
visitors as a teaser, on every viewport.

Covers the five groomed scenarios:
1. Logged-out visitor sizes up a gated workshop before signing up
   (desktop): sidebar lists every tutorial page, the current row is
   marked, the main column is a teaser + paywall (no body), and clicking
   another locked row lands on that page's own teaser + paywall.
2. Logged-out visitor on a phone opens the tutorial outline (mobile,
   390px): a `Workshop Navigation` control renders with no "Page N of M"
   text and no progress bar, tapping it opens the drawer with every page,
   and tapping a page navigates.
3. Free member without the required tier still previews the outline.
4. Member with access keeps the full reader and progress (desktop body +
   mobile "Page N of M" progress bar and completion checkmark).
5. Locked row is a real teaser link, not a dead end (403 with teaser +
   paywall, not a 404).

Usage:
    uv run pytest playwright_tests/test_workshop_gated_sidebar_teaser_1338.py -v
"""

import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection) and cannot run against the deployed dev
# environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

from playwright_tests.conftest import (  # noqa: E402
    create_session_for_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

SHOT_DIR = '.tmp/screenshots'

# A gated workshop needs a paid pages level so anonymous / Free visitors
# are gated, but a Basic member has access. LEVEL_BASIC == 10.
GATED_PAGES_LEVEL = 10

PAGES_DATA = [
    ('intro', 'Getting Started', '# Getting Started\n\nWelcome to the workshop.'),
    ('build', 'Build the Pipeline', '# Build\n\nWire the pieces together.'),
    ('deploy', 'Deploy to Prod', '# Deploy\n\nShip it to production.'),
]
PAGE_TITLES = [t for _, t, _ in PAGES_DATA]


def _make_gated_workshop(slug='gated-teaser-1338'):
    _clear_workshops()
    _create_workshop(
        slug=slug,
        title='Gated Teaser Workshop',
        landing=0,
        pages=GATED_PAGES_LEVEL,
        recording=GATED_PAGES_LEVEL,
        pages_data=PAGES_DATA,
    )
    return slug


def _anon_ctx(browser, width=1280, height=900):
    return browser.new_context(viewport={'width': width, 'height': height})


def _session_ctx(browser, email, width=1280, height=900):
    session_key = create_session_for_user(email)
    ctx = browser.new_context(viewport={'width': width, 'height': height})
    ctx.add_cookies([
        {'name': 'sessionid', 'value': session_key,
         'domain': '127.0.0.1', 'path': '/'},
        {'name': 'csrftoken', 'value': 'e2e-test-csrf-token-value',
         'domain': '127.0.0.1', 'path': '/'},
    ])
    return ctx


# ----------------------------------------------------------------------
# Scenario 1: Logged-out visitor sizes up a gated workshop (desktop).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousDesktopOutlinePreview:
    def test_desktop_sidebar_lists_all_pages_marks_current_and_gates_body(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        slug = _make_gated_workshop()

        ctx = _anon_ctx(browser)
        page = ctx.new_page()
        try:
            resp = page.goto(
                f'{django_server}/workshops/{slug}/tutorial/intro',
                wait_until='domcontentloaded',
            )
            # Gated page is served with a 403 status (SEO-indexable teaser).
            assert resp.status == 403

            sidebar = page.locator('[data-testid="workshop-sidebar"]')
            sidebar.wait_for(state='visible')

            # Every tutorial page title is listed, in order.
            row_titles = sidebar.locator('a span.truncate').all_text_contents()
            row_titles = [t.strip() for t in row_titles]
            for title in PAGE_TITLES:
                assert title in row_titles, (
                    f'{title!r} missing from sidebar rows {row_titles}'
                )
            # Order preserved: the first three visible rows are the pages
            # in sort order.
            assert row_titles[:3] == PAGE_TITLES, row_titles

            # The current page row is marked current.
            current = page.locator('[data-testid="sidebar-current-page"]')
            assert current.count() == 1
            assert current.get_attribute('aria-current') == 'page'

            # Main column is a teaser + paywall, not the full body.
            assert page.locator('[data-testid="page-paywall"]').count() == 1
            assert page.locator('[data-testid="page-body"]').count() == 0
            # No completion checkmarks leak to a gated visitor.
            assert page.locator(
                '[data-testid="sidebar-completed-page"]',
            ).count() == 0

            page.screenshot(
                path=f'{SHOT_DIR}/1338-anon-desktop-outline.png',
                full_page=False,
            )

            # Click a different (locked) page row -> lands on that page's
            # own teaser + paywall.
            page.locator(
                f'#sidebar-nav a[href="/workshops/{slug}/tutorial/deploy"]',
            ).click()
            page.wait_for_url(f'**/workshops/{slug}/tutorial/deploy')
            assert page.locator('[data-testid="page-paywall"]').count() == 1
            assert page.locator('[data-testid="page-body"]').count() == 0
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: Logged-out visitor on a phone opens the outline (mobile).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousMobileDrawer:
    def test_mobile_gated_toggle_opens_drawer_without_progress(
        self, browser, django_server,
    ):
        os.makedirs(SHOT_DIR, exist_ok=True)
        slug = _make_gated_workshop()

        ctx = _anon_ctx(browser, width=390, height=780)
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/{slug}/tutorial/intro',
                wait_until='domcontentloaded',
            )

            # The gated-safe mobile control renders above the breadcrumb.
            toggle_wrap = page.locator(
                '[data-testid="reader-mobile-nav-toggle-gated"]',
            )
            toggle_wrap.wait_for(state='visible')
            crumb = page.locator('[data-testid="page-breadcrumb"]')
            crumb.wait_for(state='visible')
            toggle_box = toggle_wrap.bounding_box()
            crumb_box = crumb.bounding_box()
            assert toggle_box is not None and crumb_box is not None
            assert toggle_box['y'] < crumb_box['y'], (
                'Gated mobile toggle is not above the breadcrumb'
            )

            # No progress semantics on the gated control.
            assert page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="reader-mobile-progress-fill"]',
            ).count() == 0
            # The member-only progress bar wrapper must not render either.
            assert page.locator(
                '[data-testid="reader-mobile-progress-bar"]',
            ).count() == 0

            # The desktop collapse control is hidden on mobile.
            assert not page.locator(
                '[data-testid="content-sidebar-collapse-btn"]',
            ).is_visible()

            # The drawer starts hidden; tapping the toggle reveals it.
            nav = page.locator('#sidebar-nav')
            assert nav.is_hidden()

            page.screenshot(
                path=f'{SHOT_DIR}/1338-anon-mobile-collapsed.png',
                full_page=False,
            )

            page.locator('#sidebar-toggle-btn').click()
            nav.wait_for(state='visible')

            # The drawer lists every tutorial page.
            row_titles = [
                t.strip()
                for t in nav.locator('a span.truncate').all_text_contents()
            ]
            for title in PAGE_TITLES:
                assert title in row_titles, (
                    f'{title!r} missing from mobile drawer {row_titles}'
                )

            page.screenshot(
                path=f'{SHOT_DIR}/1338-anon-mobile-open.png',
                full_page=False,
            )

            # Tapping a page navigates to that (still gated) tutorial page.
            nav.locator(
                f'a[href="/workshops/{slug}/tutorial/build"]',
            ).click()
            page.wait_for_url(f'**/workshops/{slug}/tutorial/build')
            assert page.locator('[data-testid="page-paywall"]').count() == 1
            assert page.locator('[data-testid="page-body"]').count() == 0
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Free member without the required tier previews the outline.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeMemberPreviewsOutline:
    def test_free_member_sees_outline_and_upgrade_paywall_no_body(
        self, browser, django_server,
    ):
        slug = _make_gated_workshop()
        _create_user('free-1338@test.com', tier_slug='free')

        ctx = _session_ctx(browser, 'free-1338@test.com')
        page = ctx.new_page()
        try:
            resp = page.goto(
                f'{django_server}/workshops/{slug}/tutorial/build',
                wait_until='domcontentloaded',
            )
            # Free member lacks the Basic tier -> gated (403).
            assert resp.status == 403

            sidebar = page.locator('[data-testid="workshop-sidebar"]')
            sidebar.wait_for(state='visible')
            row_titles = [
                t.strip()
                for t in sidebar.locator('a span.truncate').all_text_contents()
            ]
            for title in PAGE_TITLES:
                assert title in row_titles, (
                    f'{title!r} missing from Free-member sidebar {row_titles}'
                )

            # Paywall shows the upgrade path; no body, no checkmarks.
            paywall = page.locator('[data-testid="page-paywall"]')
            assert paywall.count() == 1
            assert page.locator('[data-testid="page-body"]').count() == 0
            assert page.locator(
                '[data-testid="sidebar-completed-page"]',
            ).count() == 0
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Member with access keeps the full reader and progress.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMemberWithAccessUnchanged:
    def test_member_sees_full_body_and_mobile_progress_and_checkmark(
        self, browser, django_server,
    ):
        slug = _make_gated_workshop()
        _create_user('basic-1338@test.com', tier_slug='basic')

        # Desktop: the full body renders and the member can complete the
        # page (the completion control is visible on desktop).
        ctx = _session_ctx(browser, 'basic-1338@test.com')
        page = ctx.new_page()
        try:
            resp = page.goto(
                f'{django_server}/workshops/{slug}/tutorial/intro',
                wait_until='domcontentloaded',
            )
            assert resp.status == 200
            assert page.locator('[data-testid="page-body"]').is_visible()
            assert page.locator('[data-testid="page-paywall"]').count() == 0
            # The gated-only mobile control must NOT render for a member.
            assert page.locator(
                '[data-testid="reader-mobile-nav-toggle-gated"]',
            ).count() == 0

            page.locator('[data-testid="mark-page-complete-btn"]').click()
            page.wait_for_function(
                "document.querySelector('[data-testid=\"mark-page-complete-btn\"]')"
                ".textContent.includes('Completed')",
            )
        finally:
            ctx.close()

        # Mobile: the standard progress bar shows "Page N of M", the
        # completion fill renders, and the sidebar checkmark reflects the
        # completed page — confirming the gated mobile control did not
        # replace the member experience.
        ctx = _session_ctx(browser, 'basic-1338@test.com', width=390, height=780)
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/{slug}/tutorial/intro',
                wait_until='domcontentloaded',
            )
            bar = page.locator('[data-testid="reader-mobile-progress-bar"]')
            bar.wait_for(state='visible')
            progress_text = page.locator(
                '[data-testid="reader-mobile-progress-text"]',
            )
            assert progress_text.count() == 1
            assert 'Page 1 of 3' in progress_text.text_content()
            # The member keeps the completion fill (absent on the gated
            # control) now that a page is completed.
            assert page.locator(
                '[data-testid="reader-mobile-progress-fill"]',
            ).count() == 1
            # Open the drawer to see the completion checkmark in the nav.
            page.locator('#sidebar-toggle-btn').click()
            page.locator('#sidebar-nav').wait_for(state='visible')
            assert page.locator(
                '[data-testid="sidebar-completed-page"]',
            ).count() >= 1
        finally:
            ctx.close()


# ----------------------------------------------------------------------
# Scenario 5: Locked row is a real teaser link, not a dead end.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLockedRowIsRealTeaserLink:
    def test_clicking_a_locked_row_lands_on_teaser_not_404(
        self, browser, django_server,
    ):
        slug = _make_gated_workshop()

        ctx = _anon_ctx(browser)
        page = ctx.new_page()
        try:
            # Start on the last tutorial page (gated).
            page.goto(
                f'{django_server}/workshops/{slug}/tutorial/deploy',
                wait_until='domcontentloaded',
            )
            first_row = page.locator(
                f'#sidebar-nav a[href="/workshops/{slug}/tutorial/intro"]',
            )
            first_row.wait_for(state='visible')

            # Following the row is a real navigation whose response is a
            # 403 teaser, not a 404.
            with page.expect_response(
                f'**/workshops/{slug}/tutorial/intro',
            ) as resp_info:
                first_row.click()
            resp = resp_info.value
            assert resp.status == 403

            page.wait_for_url(f'**/workshops/{slug}/tutorial/intro')
            assert page.locator('[data-testid="page-paywall"]').count() == 1
            assert page.locator('[data-testid="page-body"]').count() == 0
        finally:
            ctx.close()
