"""E2E: effective-tier display on /account/ + gated content (issue #965).

A member with an active TierOverride that raises their tier above base
should see the EFFECTIVE tier as their plan headline, an override
provenance line ("Main plan — tier override from Free until <date>"), and
should reach override-gated content without a paywall. Paid and free
members with no override see their real plan unchanged.

Usage:
    uv run pytest playwright_tests/test_account_effective_tier_965.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds DB rows and injects session cookies. Cannot run against
# the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

# A fixed future expiry so the provenance-line date is deterministic.
OVERRIDE_EXPIRES = datetime.datetime(2099, 5, 6, 0, 0, 0)


def _seed_users_and_content():
    """Create the override / paid / free members and a Main-gated article.

    Must be called inside ``django_db_blocker.unblock()``.
    """
    from django.db import connection

    from accounts.models import TierOverride, User
    from content.models import Article
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {t.slug: t for t in Tier.objects.all()}

    User.objects.filter(
        email__in=[
            "ov-member@test.com", "paid-main@test.com", "free-member@test.com",
        ]
    ).delete()
    Article.objects.filter(slug="main-gated-965").delete()

    # Free base + active Main override.
    ov = User.objects.create_user(
        email="ov-member@test.com", password=None, email_verified=True,
    )
    ov.tier = tiers["free"]
    ov.save()
    TierOverride.objects.create(
        user=ov,
        original_tier=tiers["free"],
        override_tier=tiers["main"],
        expires_at=timezone.make_aware(OVERRIDE_EXPIRES),
        is_active=True,
    )

    # Paid Main subscriber, no override.
    paid = User.objects.create_user(
        email="paid-main@test.com", password=None, email_verified=True,
    )
    paid.tier = tiers["main"]
    paid.subscription_id = "sub_main_965"
    paid.save()

    # Free member, no override.
    free = User.objects.create_user(
        email="free-member@test.com", password=None, email_verified=True,
    )
    free.tier = tiers["free"]
    free.save()

    # Main-gated article for the no-paywall scenario.
    Article.objects.create(
        title="Main Gated 965",
        slug="main-gated-965",
        description="Public teaser for the Main-gated article.",
        content_markdown=(
            "# Main Gated 965\n\nThis full body is gated behind Main."
        ),
        author="Expert",
        required_level=20,
        published=True,
        date=datetime.date(2026, 1, 1),
    )
    connection.close()


@pytest.fixture
def seeded(django_server, django_db_blocker):
    with django_db_blocker.unblock():
        _seed_users_and_content()
    return True


def _go_to_account(page, base_url):
    page.goto(f"{base_url}/account/", wait_until="domcontentloaded")


@pytest.mark.django_db(transaction=True)
class TestOverrideMemberEffectivePlan:
    """Member with a tier override sees their effective plan + window."""

    @pytest.mark.core
    def test_headline_is_effective_tier_with_provenance(
        self, django_server, seeded, browser
    ):
        ctx = _auth_context(browser, "ov-member@test.com")
        page = ctx.new_page()
        _go_to_account(page, django_server)

        # Headline shows the EFFECTIVE tier (Main), not the base (Free).
        assert page.locator("#tier-name").inner_text().strip() == "Main"

        # Provenance line names the effective tier, the base tier, and a date.
        provenance = page.locator("#tier-override-provenance")
        assert provenance.is_visible()
        text = provenance.inner_text()
        assert "Main plan" in text
        assert "tier override from Free" in text
        assert "until" in text

        # The existing temporary-access notice still makes clear this is
        # temporary access, not a subscription change.
        notice = page.locator("#tier-override-notice")
        assert notice.is_visible()
        assert "not a subscription change" in notice.inner_text()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestPaidMemberNoOverride:
    """Paid member without an override sees their real plan unchanged."""

    def test_paid_main_headline_and_no_provenance(
        self, django_server, seeded, browser
    ):
        ctx = _auth_context(browser, "paid-main@test.com")
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Main"
        assert page.locator("#tier-override-provenance").count() == 0
        # Paid member sees the Stripe Customer Portal action, not Upgrade.
        assert page.locator("#manage-subscription-btn").is_visible()
        assert page.locator("#upgrade-btn").count() == 0
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestFreeMemberNoOverride:
    """Free member without an override sees the free plan."""

    def test_free_headline_and_no_provenance(
        self, django_server, seeded, browser
    ):
        ctx = _auth_context(browser, "free-member@test.com")
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Free"
        assert page.locator("#tier-override-provenance").count() == 0
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestOverrideMemberReachesGatedContent:
    """Override member reaches Main-gated content without a paywall."""

    @pytest.mark.core
    def test_main_gated_article_full_content_no_paywall(
        self, django_server, seeded, browser
    ):
        ctx = _auth_context(browser, "ov-member@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/blog/main-gated-965",
            wait_until="domcontentloaded",
        )

        body = page.content()
        # Full gated body is visible.
        assert "This full body is gated behind Main." in body
        # No upgrade paywall CTA.
        assert "Upgrade to Main to read this article" not in body
        ctx.close()
