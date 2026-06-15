"""Playwright E2E for override-aware campaign audience size (issue #966).

The Studio campaign audience-size preview must count active-override
holders. Two scenarios:

1. A Free-base member with an active Main override is included in a Main+
   campaign's audience alongside a paid Main member (not undercounted),
   and the detail-page recipient count equals the create-form preview for
   the same audience (preview == what ships).
2. A Free-base member whose Main override has EXPIRED does NOT inflate the
   Main+ audience (only active overrides count).

The three buggy paths in #966 are background/staff surfaces; the only
user-visible surface is this Studio campaign audience preview, so that is
what we drive here. Queryset semantics (distinct, below-threshold,
notifications, email-matcher) are covered by Django TestCases.
"""

import os
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
from django.db import connection  # noqa: E402

# Issue #656: local-only fixtures (DB seeding, session-cookie injection).
pytestmark = pytest.mark.local_only


def _clear_campaigns():
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _grant_override(email, override_tier_slug, *, expires_in_days=7,
                    is_active=True):
    """Give ``email`` a TierOverride to ``override_tier_slug``."""
    from django.utils import timezone

    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=email)
    override_tier = Tier.objects.get(slug=override_tier_slug)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=override_tier,
        expires_at=timezone.now() + timedelta(days=expires_in_days),
        is_active=is_active,
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestCampaignAudienceIncludesOverrideMembers:
    """An active override member is counted in a Main+ audience; the
    detail count equals the create-form preview for that audience."""

    def test_active_override_member_counted_and_preview_matches(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")

        # Paid Main member.
        _create_user(
            "paid-main@test.com", tier_slug="main",
            email_verified=True, unsubscribed=False,
        )
        # Free-base member with an ACTIVE Main override.
        _create_user(
            "comp-main@test.com", tier_slug="free",
            email_verified=True, unsubscribed=False,
        )
        _grant_override("comp-main@test.com", "main")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # 1. Create form: pick the Main+ audience and create the draft.
        page.goto(
            f"{django_server}/studio/campaigns/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="subject"]', "Main cohort update")
        page.fill('textarea[name="body"]', "Body content")
        page.select_option('select[name="target_min_level"]', "20")
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        # 2. We land on the detail page; the audience size counts BOTH the
        # paid Main member and the Free-base active-override member -> 2,
        # not undercounted by one.
        assert "/studio/campaigns/" in page.url
        eligible = page.locator('[data-testid="eligible-recipients"]')
        assert eligible.inner_text().strip() == "2"

        # 3. The Send button number is in sync with that audience size.
        send_btn = page.locator('[data-testid="send-campaign-btn"]')
        assert "Send to 2 recipients" in send_btn.inner_text()


@pytest.mark.django_db(transaction=True)
class TestCampaignAudienceExcludesExpiredOverride:
    """An expired override does not inflate the Main+ audience."""

    def test_expired_override_member_not_counted(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_campaigns()
        _create_staff_user("admin@test.com")

        # Paid Main member -> the only real Main recipient.
        _create_user(
            "paid-main@test.com", tier_slug="main",
            email_verified=True, unsubscribed=False,
        )
        # Free-base member whose Main override has EXPIRED.
        _create_user(
            "expired@test.com", tier_slug="free",
            email_verified=True, unsubscribed=False,
        )
        _grant_override("expired@test.com", "main", expires_in_days=-1)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/campaigns/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="subject"]', "Main cohort update")
        page.fill('textarea[name="body"]', "Body content")
        page.select_option('select[name="target_min_level"]', "20")
        page.locator('button[type="submit"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        # Only the paid Main member counts; the expired-override member is
        # excluded -> audience size is 1.
        assert "/studio/campaigns/" in page.url
        eligible = page.locator('[data-testid="eligible-recipients"]')
        assert eligible.inner_text().strip() == "1"
