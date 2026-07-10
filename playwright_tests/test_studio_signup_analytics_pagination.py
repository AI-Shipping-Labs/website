"""Playwright E2E for the Studio Signup Analytics "Recent signups" pager (#850).

Covers the operator paging journey on the dashboard at
``/studio/signup-analytics/``:

* paging back through signups beyond the first 50 (next / disabled controls),
* returning to page 1 via first/prev,
* range-filter survival across paging,
* signup_path-filter survival across paging,
* the small-result no-pager case,
* the empty-window empty state,
* graceful clamp of an out-of-range ?page=,
* a recent-signup row still links to /studio/users/<id>/ after paging.

Usage:
    uv run pytest playwright_tests/test_studio_signup_analytics_pagination.py -v
"""

import os
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

# Issue #656: local-only fixtures (DB seeding + session-cookie injection),
# cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only


def _clear_attributions_except(staff_email):
    """Drop every user (and their attribution) except the named staff account,
    and remove the staff account's own auto-created attribution row, so the
    dashboard window only contains the rows each test seeds."""
    from accounts.models import User
    from analytics.models import UserAttribution

    User.objects.exclude(email=staff_email).delete()
    # The post_save signal creates a blank attribution at "now" for the staff
    # user; it would otherwise leak into the active window as an extra signup.
    UserAttribution.objects.filter(user__email=staff_email).delete()
    connection.close()


def _seed_signups(n, *, signup_path="email_password", prefix="r",
                  hours_ago_base=2):
    """Create ``n`` User + UserAttribution rows inside the default 7d window.

    Index 0 is the OLDEST, index n-1 the NEWEST, matching the dashboard's
    ``-created_at`` ordering (newest first on page 1).
    """
    from accounts.models import User
    from analytics.models import UserAttribution

    now = timezone.now()
    for i in range(n):
        user = User.objects.create_user(
            email=f"{prefix}{i}@t.com", password="x",
        )
        attr, _ = UserAttribution.objects.get_or_create(user=user)
        attr.signup_path = signup_path
        attr.save()
        # i=0 oldest, i=n-1 newest; stay well inside the 7d window.
        created = now - timedelta(hours=hours_ago_base + (n - i))
        UserAttribution.objects.filter(pk=attr.pk).update(created_at=created)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSignupAnalyticsPagination:
    """Operator pages through the Recent signups list."""

    def test_pages_back_through_older_signups(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )

        rows = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr')
        expect(rows).to_have_count(50)
        pager = page.locator('[data-testid="signup-recent-pager"]')
        expect(pager.locator('[data-testid="signup-recent-pager-range"]')).to_contain_text(
            "Showing 1-50 of 60"
        )
        expect(pager.locator('[data-testid="signup-recent-pager-status"]')).to_contain_text(
            "page 1 of 2"
        )

        # Page 2: the remaining 10 older signups.
        page.locator('[data-testid="signup-recent-pager-next"]').click()
        page.wait_for_url("**/signup-analytics/?*page=2*")
        rows = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr')
        expect(rows).to_have_count(10)
        expect(pager.locator('[data-testid="signup-recent-pager-range"]')).to_contain_text(
            "Showing 51-60 of 60"
        )
        # next / last are now disabled spans (no href).
        next_span = page.locator('span[data-testid="signup-recent-pager-next"]')
        last_span = page.locator('span[data-testid="signup-recent-pager-last"]')
        expect(next_span).to_have_attribute("aria-disabled", "true")
        expect(last_span).to_have_attribute("aria-disabled", "true")

        context.close()

    def test_returns_to_first_page(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/?page=2",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="signup-recent-pager-first"]').click()
        page.wait_for_url("**/signup-analytics/?*page=1*")

        rows = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr')
        expect(rows).to_have_count(50)
        pager = page.locator('[data-testid="signup-recent-pager"]')
        expect(pager.locator('[data-testid="signup-recent-pager-status"]')).to_contain_text(
            "page 1 of 2"
        )
        first_span = page.locator('span[data-testid="signup-recent-pager-first"]')
        prev_span = page.locator('span[data-testid="signup-recent-pager-prev"]')
        expect(first_span).to_have_attribute("aria-disabled", "true")
        expect(prev_span).to_have_attribute("aria-disabled", "true")

        context.close()

    def test_range_filter_preserved_across_paging(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/?range=30d",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="signup-recent-pager-next"]').click()
        page.wait_for_url("**/signup-analytics/?*page=2*")

        # The querystring still carries range=30d, and the window did not
        # reset to the 7d default.
        assert "range=30d" in page.url
        # The range selector still shows "Last 30 days" (window did not reset
        # to the 7d default after paging).
        expect(page.locator("select[name='range']")).to_have_value("30d")

        context.close()

    def test_signup_path_filter_preserved_across_paging(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60, signup_path="google_oauth", prefix="g")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/?signup_path=google_oauth",
            wait_until="domcontentloaded",
        )
        # Page 1 rows are all Google OAuth.
        path_cells = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr td:nth-child(7)')
        for i in range(path_cells.count()):
            expect(path_cells.nth(i)).to_have_text("Google OAuth")

        page.locator('[data-testid="signup-recent-pager-next"]').click()
        page.wait_for_url("**/signup-analytics/?*page=2*")
        assert "signup_path=google_oauth" in page.url
        path_cells = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr td:nth-child(7)')
        assert path_cells.count() > 0
        for i in range(path_cells.count()):
            expect(path_cells.nth(i)).to_have_text("Google OAuth")

        context.close()

    def test_small_result_set_has_no_pager(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(5)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )
        rows = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr')
        expect(rows).to_have_count(5)
        expect(page.locator('[data-testid="signup-recent-pager"]')).to_have_count(0)

        context.close()

    def test_empty_window_shows_empty_state(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        # No signups in the last 24h window.

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/?range=24h",
            wait_until="domcontentloaded",
        )
        empty = page.locator('[data-testid="signup-analytics-recent-empty"]')
        expect(empty).to_contain_text("No signups in this range.")
        expect(page.locator('[data-testid="signup-recent-pager"]')).to_have_count(0)

        context.close()

    def test_out_of_range_page_clamps(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        # Beyond the last page -> clamps to the last page (no 404/500).
        page.goto(
            f"{django_server}/studio/signup-analytics/?page=9999",
            wait_until="domcontentloaded",
        )
        pager = page.locator('[data-testid="signup-recent-pager"]')
        expect(pager.locator('[data-testid="signup-recent-pager-status"]')).to_contain_text(
            "page 2 of 2"
        )
        # Non-integer -> page 1 (no 404/500).
        page.goto(
            f"{django_server}/studio/signup-analytics/?page=abc",
            wait_until="domcontentloaded",
        )
        expect(pager.locator('[data-testid="signup-recent-pager-status"]')).to_contain_text(
            "page 1 of 2"
        )

        context.close()

    def test_row_links_to_user_detail_after_paging(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(60)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/?page=2",
            wait_until="domcontentloaded",
        )
        first_email_link = page.locator('[data-testid="signup-analytics-recent-table"] tbody tr td:first-child a').first
        first_email_link.click()
        page.wait_for_url("**/studio/users/*/")
        assert "/studio/users/" in page.url

        context.close()
