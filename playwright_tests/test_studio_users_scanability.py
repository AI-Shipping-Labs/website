"""Playwright coverage for the denser Studio users list (issue #410)."""

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
    user.slack_checked_at = timezone.now()
    user.save(update_fields=["tags", "slack_member", "slack_checked_at"])
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
    def test_dense_rows_preserve_filters_tags_export_and_actions(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "scanability-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        email, user_pk = _seed_scanability_user()

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

        badges = row.locator('[data-testid="membership-badges"] span')
        assert badges.count() == 4
        badge_text = row.locator('[data-testid="membership-badges"]').inner_text()
        assert "Premium" in badge_text
        assert "Newsletter" in badge_text
        assert "Slack" in badge_text
        assert "Active" in badge_text

        tag_links = row.locator('[data-testid="user-tags-cell"] a')
        assert tag_links.count() == 3
        assert tag_links.nth(0).inner_text().strip() == "early-adopter"
        overflow = row.locator('[data-testid="user-tags-overflow"]')
        assert overflow.inner_text().strip() == "+2"
        assert "vip, cohort-a" in overflow.get_attribute("aria-label")

        export_href = page.locator("a", has_text="Export CSV").get_attribute("href")
        assert export_href.endswith(
            "/studio/users/export?filter=paid&slack=yes&q=avery"
        )

        tag_links.nth(0).click()
        page.wait_for_load_state("domcontentloaded")
        assert "tag=early-adopter" in page.url
        active_chip = page.locator('[data-testid="active-tag-chip"]')
        assert active_chip.is_visible()
        assert "Tag: early-adopter" in active_chip.inner_text()
        export_href = page.locator("a", has_text="Export CSV").get_attribute("href")
        assert export_href.endswith(
            "/studio/users/export?filter=paid&slack=yes&q=avery&tag=early-adopter"
        )

        row = page.locator("tbody tr", has_text=email).first
        view = row.locator('[data-testid="user-view-link"]')
        login_as = row.get_by_role("button", name="Login as")
        assert view.is_visible()
        assert login_as.is_visible()
        assert login_as.evaluate(
            "node => window.getComputedStyle(node).whiteSpace === 'nowrap'"
        )
        assert row.locator('form[method="post"]').get_attribute("action").endswith(
            f"/studio/impersonate/{user_pk}/"
        )
        _assert_row_actions_fit(page, row)
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "users-1280px")

        login_as.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url == f"{django_server}/"
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
        assert row.locator('[data-testid="membership-badges"]').is_visible()

        view = row.locator('[data-testid="user-view-link"]')
        login_as = row.get_by_role("button", name="Login as")
        assert view.is_visible()
        assert login_as.is_visible()
        for action in [view, login_as]:
            box = action.bounding_box()
            assert box is not None
            assert box["x"] + box["width"] <= MOBILE_VIEWPORT["width"]
            assert action.evaluate(
                "node => window.getComputedStyle(node).whiteSpace === 'nowrap'"
            )

        _assert_row_actions_fit(page, row)
        _assert_no_horizontal_overflow(page)
        _capture_screenshot(page, "users-390px")
        context.close()
