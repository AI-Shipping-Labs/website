"""Focused Playwright coverage for signed-in pricing account states (#383)."""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _seed_pricing_users():
    from accounts.models import TierOverride, User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {tier.slug: tier for tier in Tier.objects.all()}

    def upsert_user(email, tier_slug, subscription_id="", pending_slug=None):
        user, _ = User.objects.get_or_create(
            email=email,
            defaults={"email_verified": True},
        )
        user.set_password(DEFAULT_PASSWORD)
        user.email_verified = True
        user.tier = tiers[tier_slug] if tier_slug else None
        user.subscription_id = subscription_id
        user.pending_tier = tiers[pending_slug] if pending_slug else None
        user.billing_period_end = timezone.make_aware(
            datetime.datetime(2026, 5, 29, 12, 0, 0)
        )
        user.save()
        return user

    upsert_user("pricing-free@test.com", "free")
    upsert_user("pricing-basic@test.com", "basic", "sub_basic_pricing")
    upsert_user("pricing-main@test.com", "main", "sub_main_pricing")
    upsert_user("pricing-premium@test.com", "premium", "sub_premium_pricing")
    upsert_user(
        "pricing-pending@test.com",
        "main",
        "sub_pending_pricing",
        "basic",
    )
    upsert_user(
        "pricing-canceling@test.com",
        "basic",
        "sub_canceling_pricing",
        "free",
    )
    override_user = upsert_user(
        "pricing-override@test.com",
        "basic",
        "sub_override_pricing",
    )
    TierOverride.objects.filter(user=override_user).delete()
    TierOverride.objects.create(
        user=override_user,
        original_tier=tiers["basic"],
        override_tier=tiers["premium"],
        expires_at=timezone.now() + datetime.timedelta(days=14),
    )
    upsert_user("pricing-stale@test.com", None, "sub_stale_pricing")


@pytest.fixture
def pricing_users(django_server, django_db_blocker):
    from django.db import connection

    with django_db_blocker.unblock():
        _seed_pricing_users()
        connection.close()


def _auth_context(browser, email, db_blocker):
    with db_blocker.unblock():
        session_key = _create_session_for_user(email)

    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _pricing_page(browser, django_server, django_db_blocker, email):
    context = _auth_context(browser, email, django_db_blocker)
    page = context.new_page()
    page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
    return context, page


def _account_page(browser, django_server, django_db_blocker, email, viewport=VIEWPORT):
    with django_db_blocker.unblock():
        session_key = _create_session_for_user(email)

    context = browser.new_context(viewport=viewport)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    page = context.new_page()
    page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
    return context, page


def _tier_card(page, tier_name):
    cards = page.locator("div.grid.sm\\:grid-cols-2.lg\\:grid-cols-4 > div")
    for index in range(cards.count()):
        card = cards.nth(index)
        if card.locator("h2").inner_text().strip() == tier_name:
            return card
    raise AssertionError(f"Tier card {tier_name!r} not found")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_free_member_sees_current_free_and_paid_upgrades(
    django_server, browser, django_db_blocker, pricing_users
):
    context, page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-free@test.com"
    )
    try:
        assert "Current free plan" in _tier_card(page, "Free").inner_text()
        for tier_name in ("Basic", "Main", "Premium"):
            card_text = _tier_card(page, tier_name).inner_text()
            assert "Upgrade" in card_text
            assert "Manage Subscription" not in card_text
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_main_member_sees_current_plan_downgrade_and_upgrade(
    django_server, browser, django_db_blocker, pricing_users
):
    context, page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-main@test.com"
    )
    try:
        assert "Included" in _tier_card(page, "Free").inner_text()
        assert "Downgrade" in _tier_card(page, "Basic").inner_text()
        main_text = _tier_card(page, "Main").inner_text()
        assert "Current plan" in main_text
        assert "Join" not in main_text
        assert "Upgrade" in _tier_card(page, "Premium").inner_text()
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_premium_member_does_not_see_join_for_lower_paid_tiers(
    django_server, browser, django_db_blocker, pricing_users
):
    context, page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-premium@test.com"
    )
    try:
        assert "Current plan" in _tier_card(page, "Premium").inner_text()
        for tier_name in ("Basic", "Main"):
            card_text = _tier_card(page, tier_name).inner_text()
            assert "Downgrade" in card_text
            assert "Join" not in card_text
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("email", "expected_plan", "upgrade_visible"),
    [
        ("pricing-free@test.com", "Free", True),
        ("pricing-premium@test.com", "Premium", False),
    ],
)
@pytest.mark.core
def test_account_primary_action_matches_current_plan_state(
    django_server,
    browser,
    django_db_blocker,
    pricing_users,
    email,
    expected_plan,
    upgrade_visible,
):
    context, page = _account_page(browser, django_server, django_db_blocker, email)
    try:
        assert page.locator("#tier-name").inner_text().strip() == expected_plan
        assert page.locator("#upgrade-btn").count() == (1 if upgrade_visible else 0)
        if expected_plan == "Premium":
            assert "Current plan" in page.locator("#account-plan-state").inner_text()
            assert page.locator("#downgrade-btn").is_visible()
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_pending_downgrade_and_cancellation_copy_is_visible(
    django_server, browser, django_db_blocker, pricing_users
):
    pending_context, pending_page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-pending@test.com"
    )
    try:
        assert "changes to Basic on May 29, 2026" in _tier_card(
            pending_page, "Main"
        ).inner_text()
        assert "Scheduled change" in _tier_card(pending_page, "Basic").inner_text()
    finally:
        pending_context.close()

    cancel_context, cancel_page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-canceling@test.com"
    )
    try:
        basic_text = _tier_card(cancel_page, "Basic").inner_text()
        assert "Access ending" in basic_text
        assert "Access ends on May 29, 2026" in basic_text
        assert "Join" not in basic_text
    finally:
        cancel_context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_account_pending_and_temporary_states_do_not_show_normal_upgrade(
    django_server, browser, django_db_blocker, pricing_users
):
    pending_context, pending_page = _account_page(
        browser, django_server, django_db_blocker, "pricing-pending@test.com"
    )
    try:
        pending_text = pending_page.locator("#account-plan-state").inner_text()
        assert "changes to Basic on May 29, 2026" in pending_text
        assert pending_page.locator("#upgrade-btn").count() == 0
    finally:
        pending_context.close()

    override_context, override_page = _account_page(
        browser, django_server, django_db_blocker, "pricing-override@test.com"
    )
    try:
        assert "Temporary Premium access" in override_page.locator(
            "#tier-override-notice"
        ).inner_text()
        assert override_page.locator("#upgrade-btn").count() == 0
    finally:
        override_context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_override_and_stale_subscription_use_non_join_copy(
    django_server, browser, django_db_blocker, pricing_users
):
    override_context, override_page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-override@test.com"
    )
    try:
        assert "Base subscription" in _tier_card(override_page, "Basic").inner_text()
        premium_text = _tier_card(override_page, "Premium").inner_text()
        assert "Temporary access" in premium_text
        assert "Join" not in premium_text
    finally:
        override_context.close()

    stale_context, stale_page = _pricing_page(
        browser, django_server, django_db_blocker, "pricing-stale@test.com"
    )
    try:
        for tier_name in ("Basic", "Main", "Premium"):
            card_text = _tier_card(stale_page, tier_name).inner_text()
            assert "Manage Subscription" in card_text
            assert "Join" not in card_text
    finally:
        stale_context.close()
