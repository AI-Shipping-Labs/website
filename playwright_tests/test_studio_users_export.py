"""Playwright E2E test for the Studio contacts CSV export (issue #355).

Single happy-path scenario covering the operator workflow:
1. Open the Studio users list as a staff user.
2. Click "Export CSV".
3. The downloaded file's name matches the locked timestamped pattern.
4. The CSV header matches the locked column order.
5. Tagged, unsubscribed, override-tier, and Slack status rows render the
   right cells.

Usage:
    uv run pytest playwright_tests/test_studio_users_export.py -v
"""

import csv
import os
import re
from datetime import timedelta

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
from django.db import connection
from django.utils import timezone


def _clear_users_except_staff(staff_email):
    """Drop every user except the named staff account so the export rows are
    deterministic for the assertions below."""
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _set_tags(email, tags):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.tags = tags
    user.save(update_fields=["tags"])
    connection.close()


def _grant_premium_override(email):
    """Apply an active premium tier override to the named user."""
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=email)
    free = Tier.objects.get(slug="free")
    premium = Tier.objects.get(slug="premium")
    TierOverride.objects.create(
        user=user,
        original_tier=free,
        override_tier=premium,
        expires_at=timezone.now() + timedelta(days=14),
        is_active=True,
    )
    connection.close()


def _set_slack_status(email, *, member=False, checked=False):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.slack_member = member
    user.slack_checked_at = timezone.now() if checked else None
    user.save(update_fields=["slack_member", "slack_checked_at"])
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestOperatorExportsContactCSV:
    """Operator clicks Export CSV; the downloaded file is parseable and
    contains the locked columns plus correctly-rendered tag/tier/Slack cells."""

    def test_export_download_and_parse(self, django_server, browser):
        _ensure_tiers()
        staff_email = "export-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)

        # alice: free tier with two tags, verified, subscribed.
        _create_user(
            "alice@test.com",
            tier_slug="free",
            email_verified=True,
            unsubscribed=False,
        )
        _set_tags("alice@test.com", ["early-adopter", "paid-2026"])
        _set_slack_status("alice@test.com", member=True, checked=True)

        # bob: main tier, no tags, unsubscribed, checked but absent from Slack.
        _create_user(
            "bob@test.com",
            tier_slug="main",
            email_verified=True,
            unsubscribed=True,
        )
        _set_slack_status("bob@test.com", member=False, checked=True)

        # charlie: free base with active premium override; one tag; never checked
        # for Slack membership.
        _create_user(
            "charlie@test.com",
            tier_slug="free",
            email_verified=True,
            unsubscribed=False,
        )
        _set_tags("charlie@test.com", ["vip"])
        _grant_premium_override("charlie@test.com")

        # Two more untagged contacts to round out the list.
        _create_user("dave@test.com", tier_slug="free")
        _create_user("erin@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Navigate to /studio/users/.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )
        assert "/studio/users/" in page.url

        # 2. Click "Export CSV". The link is rendered by templates/studio/users/list.html.
        export_link = page.locator("a", has_text="Export CSV")
        assert export_link.count() == 1, (
            f"Expected one Export CSV link, found {export_link.count()}"
        )

        with page.expect_download() as download_info:
            export_link.click()
        download = download_info.value

        # 3. Filename matches the locked pattern.
        assert re.match(
            r"^aishippinglabs-contacts-\d{8}-\d{6}\.csv$",
            download.suggested_filename,
        ), f"Unexpected download filename: {download.suggested_filename!r}"

        downloaded_path = download.path()
        with open(downloaded_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        # 4. Headers exactly match the locked column order.
        assert fieldnames == [
            "email",
            "tier",
            "tags",
            "email_verified",
            "unsubscribed",
            "date_joined",
            "last_login",
            "slack",
        ], f"Unexpected fieldnames: {fieldnames!r}"

        rows_by_email = {row["email"]: row for row in rows}

        # 5a. Alice: tags joined with comma, verified, subscribed, never logged in.
        alice = rows_by_email["alice@test.com"]
        assert alice["tags"] == "early-adopter,paid-2026", (
            f"Alice tags cell: {alice['tags']!r}"
        )
        assert alice["email_verified"] == "Yes"
        assert alice["unsubscribed"] == "No"
        assert alice["last_login"] == ""
        assert alice["slack"] == "Member"

        # 5b. Bob: empty tags, unsubscribed=Yes, checked but not in Slack.
        bob = rows_by_email["bob@test.com"]
        assert bob["tags"] == ""
        assert bob["unsubscribed"] == "Yes"
        assert bob["slack"] == "Not in Slack"

        # 5c. Charlie: override-tier label, single tag, and never-checked Slack.
        charlie = rows_by_email["charlie@test.com"]
        assert charlie["tier"] == "Premium (override)", (
            f"Charlie tier cell: {charlie['tier']!r}"
        )
        assert charlie["tags"] == "vip"
        assert charlie["slack"] == "Never checked"

        context.close()
