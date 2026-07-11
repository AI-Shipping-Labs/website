"""End-to-end coverage for the Studio user-detail status-clarity changes
(issue #924).

The "Membership & community" card on ``/studio/users/<id>/`` gains an
``Email verified`` row, inline help tooltips on every cryptic label, a
tier-source badge tooltip, a bounce ``State`` tooltip, and a
``What do these mean?`` docs link in the card header.

These scenarios drive the operator journeys from the issue:

- confirming a verified member's email status;
- spotting an unverified, never-engaged newsletter-only row;
- reading the cryptic-label tooltips (Status / Source / Activated);
- following the docs link to the GitHub-hosted reference;
- interpreting the Default vs Override tier-source badge;
- interpreting a bounced user's State tooltip;
- confirming existing detail controls survive the change.

Usage:
    uv run pytest playwright_tests/test_studio_user_detail_status_clarity_924.py -v
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
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only

DOCS_URL = (
    "https://github.com/AI-Shipping-Labs/website/blob/main/"
    "_docs/studio-user-statuses.md"
)


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _make_override(user_email, tier_slug, granted_by_email, days=30):
    from accounts.models import TierOverride, User
    from payments.models import Tier

    user = User.objects.get(email=user_email)
    tier = Tier.objects.get(slug=tier_slug)
    granted_by = User.objects.get(email=granted_by_email)
    override = TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=timezone.now() + timedelta(days=days),
        granted_by=granted_by,
        is_active=True,
    )
    connection.close()
    return override


def _set_bounce(user_email, state="permanent"):
    from accounts.models import User

    User.objects.filter(email=user_email).update(
        bounce_state=state,
        bounce_recorded_at=timezone.now(),
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestEmailVerifiedRow:
    """The Email-verified row reflects ``email_verified``."""

    def test_verified_member_shows_verified_pill(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "verify-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("verified@test.com", email_verified=True)
        member_pk = _user_id_for("verified@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        card = page.locator(
            '[data-testid="user-detail-membership-section"]',
        )
        assert card.count() == 1
        pill = page.locator('[data-testid="user-detail-email-verified"]')
        assert pill.count() == 1
        assert pill.get_attribute("data-email-verified") == "yes"
        assert "Verified" in pill.inner_text()

        context.close()

    def test_unverified_newsletter_only_row(self, django_server, browser):
        _ensure_tiers()
        staff_email = "unverify-924-admin@test.com"
        _create_staff_user(staff_email)
        # Newsletter-only: unverified email, never activated.
        _create_user("unverified@test.com", email_verified=False)
        member_pk = _user_id_for("unverified@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        pill = page.locator('[data-testid="user-detail-email-verified"]')
        assert pill.count() == 1
        assert pill.get_attribute("data-email-verified") == "no"
        assert "Not verified" in pill.inner_text()

        # The user has not engaged, so Activated reads No.
        activated = page.locator(
            '[data-testid="user-detail-account-activated"]',
        )
        assert activated.get_attribute("data-account-activated") == "no"
        assert "No" in activated.inner_text()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestCrypticLabelTooltips:
    """Cryptic labels carry explanatory title tooltips."""

    def test_status_source_activated_tooltips(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "tips-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("tips@test.com", email_verified=True)
        member_pk = _user_id_for("tips@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        card = page.locator(
            '[data-testid="user-detail-membership-section"]',
        )

        status_dt = card.locator("dt", has_text="Status").first
        status_title = status_dt.get_attribute("title")
        assert status_title is not None
        assert "login account state, not the subscription" in status_title
        assert "Active = can log in" in status_title
        assert "Staff = staff account" in status_title
        assert "Inactive = login disabled" in status_title

        source_dt = card.locator("dt", has_text="Source").first
        source_title = source_dt.get_attribute("title")
        assert source_title is not None
        assert "signup attribution" in source_title
        assert "predates signup tracking" in source_title

        activated_dt = card.locator("dt", has_text="Activated").first
        activated_title = activated_dt.get_attribute("title")
        assert activated_title is not None
        assert "verified email" in activated_title
        assert "registered for an event" in activated_title
        assert "linked Slack" in activated_title

        # Each cryptic label carries a help-circle info icon. Lucide
        # swaps the placeholder <i data-lucide> for an inline <svg> on
        # load, so assert on the rendered icons (the studio-help class
        # is preserved onto the generated <svg>).
        card.locator("svg.studio-help").first.wait_for(state="attached")
        assert card.locator("svg.studio-help").count() >= 8

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStatusDocsLink:
    """The card header links to the GitHub-hosted status reference."""

    def test_docs_link_targets_repo_doc_and_opens_new_tab(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "docs-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("docs-member@test.com", email_verified=True)
        member_pk = _user_id_for("docs-member@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        link = page.locator(
            '[data-testid="user-detail-status-docs-link"]',
        )
        assert link.count() == 1
        assert link.get_attribute("href") == DOCS_URL
        assert link.get_attribute("target") == "_blank"
        assert "noopener" in (link.get_attribute("rel") or "")

        context.close()


@pytest.mark.django_db(transaction=True)
class TestTierSourceBadgeTooltip:
    """Default vs Override tier-source badges carry their tooltips."""

    def test_default_badge_tooltip(self, django_server, browser):
        _ensure_tiers()
        staff_email = "default-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("default-member@test.com", tier_slug="free")
        member_pk = _user_id_for("default-member@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") == "default"
        title = badge.get_attribute("title")
        assert title is not None
        assert "No Stripe subscription and no override" in title

        context.close()

    def test_override_badge_tooltip_and_link(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "override-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("override-member@test.com", tier_slug="free")
        _make_override(
            "override-member@test.com", "main", staff_email, days=30,
        )
        member_pk = _user_id_for("override-member@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") == "override"
        title = badge.get_attribute("title")
        assert title is not None
        assert "active temporary upgrade granted in Studio" in title

        anchor = page.locator(
            '[data-testid="user-detail-tier-badge-link"]',
        )
        assert anchor.count() == 1
        assert anchor.evaluate("el => el.tagName").lower() == "a"

        context.close()


@pytest.mark.django_db(transaction=True)
class TestBounceStateTooltip:
    """The bounce State label carries its help tooltip."""

    def test_permanent_bounce_state_tooltip(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "bounce-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("bounced-member@test.com", email_verified=True)
        _set_bounce("bounced-member@test.com", state="permanent")
        member_pk = _user_id_for("bounced-member@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        deliverability_card = page.locator(
            '[data-testid="user-detail-deliverability-section"]',
        )
        assert deliverability_card.count() == 1
        state_label = deliverability_card.locator("dt", has_text="State").first
        title = state_label.get_attribute("title")
        assert title is not None
        assert "SES delivery status" in title
        assert "hard bounce" in title
        assert "auto-unsubscribed" in title

        context.close()


@pytest.mark.django_db(transaction=True)
class TestExistingControlsSurvive:
    """The clarity changes do not break existing detail controls."""

    def test_membership_and_override_controls_intact(
        self, django_server, browser,
    ):
        _ensure_tiers()
        staff_email = "survive-924-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("survive-member@test.com", tier_slug="free")
        member_pk = _user_id_for("survive-member@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # Existing membership selectors still render.
        for testid in (
            "user-detail-tier-pill",
            "user-detail-status-pill",
            "user-detail-signup-source",
            "user-detail-account-activated",
            "user-detail-slack-id-row",
        ):
            assert page.locator(f'[data-testid="{testid}"]').count() == 1

        # The Grant temporary upgrade form still submits (free user, so
        # the override-create form is present).
        form = page.locator(
            '[data-testid="user-detail-tier-override-form"]',
        )
        assert form.count() == 1
        page.locator(
            '[data-testid="user-detail-tier-override-duration"]',
        ).first.click()
        page.wait_for_load_state("domcontentloaded")

        # After granting, the user now shows an active override badge.
        assert f"/studio/users/{member_pk}/" in page.url
        badge = page.locator('[data-testid="user-detail-tier-badge"]')
        assert badge.get_attribute("data-tier-source") == "override"

        context.close()
