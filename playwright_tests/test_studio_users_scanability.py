"""Playwright coverage for the denser Studio users list (issues #410, #451).

Issue #410 introduced the dense user-row layout; issue #451 collapsed
the listing to four columns (User / Status / Last login / Actions),
moved the tier pill INTO the User cell, and surfaces Slack ID, Stripe
customer ID, Newsletter and Slack-workspace state via the row-level
``<tr title="...">`` hover tooltip.
"""

import os
from pathlib import Path

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
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

MOBILE_VIEWPORT = {"width": 390, "height": 900}
SCREENSHOT_DIR = Path("/tmp/aisl-issue-410-screenshots")


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _seed_scanability_user():
    from accounts.models import User

    email = "avery.long.email.address.for.scanability.testing@example.test"
    _create_user(email, tier_slug="premium", email_verified=True, unsubscribed=False)
    user = User.objects.get(email=email)
    user.tags = [
        "early-adopter",
        "beta",
        "paid-2026",
        "vip",
        "cohort-a",
    ]
    user.slack_member = True
    user.slack_user_id = "U01SCAN999"
    user.stripe_customer_id = "cus_SCANABILITY"
    # Issue #930: ``filter=paid`` requires an active Stripe subscription
    # (non-empty ``subscription_id`` + paid base tier), not just a paid
    # tier, so this premium user must carry a subscription id to appear
    # under the Paid chip used by the scanability scenario.
    user.subscription_id = "sub_SCANABILITY"
    user.slack_checked_at = timezone.now()
    user.save(update_fields=[
        "tags", "slack_member", "slack_user_id",
        "stripe_customer_id", "subscription_id", "slack_checked_at",
    ])
    user_pk = user.pk
    connection.close()
    return email, user_pk


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2


def _assert_row_actions_fit(page, row):
    bounds = row.locator('[data-testid="user-row-actions"]').evaluate(
        """node => {
            const actions = node.getBoundingClientRect();
            const list = document
                .querySelector('[data-testid="studio-users-list"]')
                .getBoundingClientRect();
            return {
                actionsLeft: actions.left,
                actionsRight: actions.right,
                listLeft: list.left,
                listRight: list.right,
                viewportWidth: window.innerWidth,
            };
        }"""
    )
    assert bounds["actionsLeft"] >= bounds["listLeft"] - 2
    assert bounds["actionsRight"] <= bounds["listRight"] + 2
    assert bounds["actionsRight"] <= bounds["viewportWidth"] + 2


def _capture_screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@pytest.mark.django_db(transaction=True)
class TestStudioUsersScanability:
    def test_dense_rows_preserve_filters_tooltip_export_and_actions(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "scanability-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        email, _user_pk = _seed_scanability_user()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/?filter=paid&slack=yes&q=avery",
            wait_until="domcontentloaded",
        )

        row = page.locator("tbody tr", has_text=email).first
        assert row.is_visible()

        email_node = row.locator('[data-testid="user-email"]')
        assert email_node.get_attribute("title") == email
        assert email_node.get_attribute("aria-label") == f"Email {email}"
        assert "truncate" in email_node.get_attribute("class")
        assert email_node.evaluate(
            """node => {
                const style = window.getComputedStyle(node);
                return style.whiteSpace === 'nowrap'
                    && style.overflow === 'hidden'
                    && style.textOverflow === 'ellipsis';
            }"""
        )

        # Issue #451: tier pill is inside the User cell (not a separate
        # Membership column), and Slack ID / Stripe customer / Newsletter
        # / Slack workspace facts are surfaced via the row's title attribute.
        user_cell = row.locator('td[data-label="User"]')
        tier_pill = user_cell.locator('[data-testid="user-list-tier-pill"]')
        assert tier_pill.count() == 1
        assert tier_pill.inner_text().strip() == "Premium"

        tooltip = row.get_attribute("title") or ""
        assert "Slack ID: U01SCAN999" in tooltip
        assert "Stripe customer: cus_SCANABILITY" in tooltip
        assert "Newsletter: subscribed" in tooltip
        assert "Slack workspace: Member" in tooltip

        # Status pill renders the user's active state in its own cell.
        status_pill = row.locator('[data-testid="user-status"]')
        assert status_pill.count() == 1
        assert status_pill.inner_text().strip() == "Active"

        export_href = page.locator("a", has_text="Export CSV").get_attribute("href")
        # Issue #766 added ``&bounce=<state>`` to the preserved filters.
        assert export_href.endswith(
            "/studio/users/export?filter=paid&slack=yes&bounce=any&q=avery"
        )

        # Tag filter is now applied via the standalone tag picker / active
        # chip header, not via per-row chips (those were removed in #451).
        # Apply via the ?tag= query directly.
        page.goto(
            f"{django_server}/studio/users/?filter=paid&slack=yes&q=avery&tag=early-adopter",
            wait_until="domcontentloaded",
        )
        active_chip = page.locator('[data-testid="active-tag-chip"]')
        assert active_chip.is_visible()
        assert "Tag: early-adopter" in active_chip.inner_text()
        export_href = page.locator("a", has_text="Export CSV").get_attribute("href")
        assert export_href.endswith(
            "/studio/users/export?filter=paid&slack=yes&bounce=any&q=avery&tag=early-adopter"
        )

        row = page.locator("tbody tr", has_text=email).first
        view = row.locator('[data-testid="user-view-link"]')
        assert view.is_visible()
        assert row.get_by_role("button", name="Login as").count() == 0
        assert view.evaluate(
            "node => window.getComputedStyle(node).whiteSpace === 'nowrap'"
        )
        assert row.locator('form[method="post"]').count() == 0
        _assert_row_actions_fit(page, row)
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "users-1280px")
        context.close()

    def test_users_list_is_usable_at_390px(self, django_server, browser):
        _ensure_tiers()
        staff_email = "scanability-mobile-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        email, _user_pk = _seed_scanability_user()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(
            f"{django_server}/studio/users/?q=avery",
            wait_until="domcontentloaded",
        )

        row = page.locator("tbody tr", has_text=email).first
        assert row.is_visible()
        assert row.locator('[data-testid="user-email"]').is_visible()
        # Tier pill lives inside the User cell now (issue #451).
        assert row.locator(
            'td[data-label="User"] [data-testid="user-list-tier-pill"]'
        ).is_visible()

        view = row.locator('[data-testid="user-view-link"]')
        assert view.is_visible()
        assert row.get_by_role("button", name="Login as").count() == 0
        box = view.bounding_box()
        assert box is not None
        assert box["x"] + box["width"] <= MOBILE_VIEWPORT["width"]
        assert view.evaluate(
            "node => window.getComputedStyle(node).whiteSpace === 'nowrap'"
        )

        _assert_row_actions_fit(page, row)
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "users-390px")
        context.close()
