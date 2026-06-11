"""Playwright E2E for the Studio membership breakdown (issue #923).

The Studio users dashboard replaces the single (inflated) "Paid" stat with a
tier x source decomposition: Paid {Basic,Main,Premium} (active Stripe
subscription, grouped by base tier) and Override {Basic,Main,Premium} (active
TierOverride, excluding subscription holders), plus Total paying and Total
comped. "Paid" everywhere means an active Stripe subscription only — overrides
are never counted as paid.

Scenarios:
- Operator sees a true paid count that excludes comped accounts.
- Operator filters to Paid and sees only paying members (chip row count ==
  Total paying).
- Canceled subscriber drops out of the paid count and list.
- Operator distinguishes paying members from comped overrides (six cells +
  both totals render with their data-testids).
- Operator exports the paid segment and gets only subscription users.
- A both-sub-and-override user counts under Paid only.

Usage:
    uv run pytest playwright_tests/test_studio_users_membership_breakdown.py -v
"""

import csv
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
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _set_subscription(email, subscription_id):
    """Mark a paid user as an active Stripe subscriber."""
    from accounts.models import User

    user = User.objects.get(email=email)
    user.subscription_id = subscription_id
    user.save(update_fields=["subscription_id"])
    connection.close()


def _grant_override(email, tier_slug):
    """Apply an active tier override at the given tier."""
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=email)
    free = Tier.objects.get(slug="free")
    override_tier = Tier.objects.get(slug=tier_slug)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier or free,
        override_tier=override_tier,
        expires_at=timezone.now() + timedelta(days=14),
        is_active=True,
    )
    connection.close()


def _cell(page, testid):
    return page.locator(f'[data-testid="{testid}"]')


@pytest.mark.django_db(transaction=True)
class TestMembershipBreakdown:
    """Operator reads the tier x source decomposition and the Paid chip."""

    def _seed(self):
        _ensure_tiers()
        staff_email = "breakdown-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)

        # 2 paid Main subscribers (active Stripe subscription).
        _create_user("paid-main-1@test.com", tier_slug="main")
        _set_subscription("paid-main-1@test.com", "sub_m1")
        _create_user("paid-main-2@test.com", tier_slug="main")
        _set_subscription("paid-main-2@test.com", "sub_m2")
        # 1 paid Basic, 1 paid Premium.
        _create_user("paid-basic@test.com", tier_slug="basic")
        _set_subscription("paid-basic@test.com", "sub_b1")
        _create_user("paid-premium@test.com", tier_slug="premium")
        _set_subscription("paid-premium@test.com", "sub_p1")

        # 3 override-only Main users (comped, no subscription).
        for idx in range(3):
            email = f"comp-main-{idx}@test.com"
            _create_user(email, tier_slug="free")
            _grant_override(email, "main")

        # 1 both-sub-and-override user: Main subscription + Premium override.
        # Must count under Paid (Main) only.
        _create_user("both@test.com", tier_slug="main")
        _set_subscription("both@test.com", "sub_both")
        _grant_override("both@test.com", "premium")

        # 1 canceled subscriber: webhook cleared sub + reverted to Free.
        _create_user("canceled@test.com", tier_slug="free")
        return staff_email

    def test_breakdown_excludes_comped_and_filters_paid(
        self, django_server, browser,
    ):
        staff_email = self._seed()
        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )

        # Six cells: paid_main = 2 dedicated + 1 "both" = 3.
        expect(_cell(page, "paid-basic")).to_have_text("1")
        expect(_cell(page, "paid-main")).to_have_text("3")
        expect(_cell(page, "paid-premium")).to_have_text("1")
        # Override cells: 3 comped Main; "both" excluded from override.
        expect(_cell(page, "override-basic")).to_have_text("0")
        expect(_cell(page, "override-main")).to_have_text("3")
        expect(_cell(page, "override-premium")).to_have_text("0")
        # Totals: paying = 1+3+1 = 5; comped = 3.
        expect(_cell(page, "total-paying")).to_have_text("5")
        expect(_cell(page, "total-comped")).to_have_text("3")

        # Filter to Paid: only the 5 active subscribers, none of the comped.
        page.goto(
            f"{django_server}/studio/users/?filter=paid",
            wait_until="domcontentloaded",
        )
        rows = page.locator('tr[data-testid^="user-row-"]')
        expect(rows).to_have_count(5)

        body = page.content()
        for paying in (
            "paid-main-1@test.com",
            "paid-main-2@test.com",
            "paid-basic@test.com",
            "paid-premium@test.com",
            "both@test.com",
        ):
            assert paying in body, f"{paying} missing from Paid list"
        for excluded in (
            "comp-main-0@test.com",
            "comp-main-1@test.com",
            "comp-main-2@test.com",
            "canceled@test.com",
        ):
            assert excluded not in body, (
                f"{excluded} should not appear under Paid"
            )

        context.close()

    def test_paid_csv_export_contains_only_subscribers(
        self, django_server, browser,
    ):
        staff_email = self._seed()
        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/?filter=paid",
            wait_until="domcontentloaded",
        )
        export_link = page.locator("a", has_text="Export CSV")
        with page.expect_download() as download_info:
            export_link.click()
        download = download_info.value

        with open(download.path(), encoding="utf-8") as f:
            emails = {row["email"] for row in csv.DictReader(f)}

        assert emails == {
            "paid-main-1@test.com",
            "paid-main-2@test.com",
            "paid-basic@test.com",
            "paid-premium@test.com",
            "both@test.com",
        }, f"Unexpected paid CSV rows: {emails!r}"

        context.close()
