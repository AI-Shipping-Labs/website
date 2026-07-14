"""Production-mode Playwright smokes for pricing and account billing."""

import datetime as dt
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)


def _seed_pricing_user(email, tier_slug="free", subscription_id=""):
    from accounts.models import User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tier = Tier.objects.get(slug=tier_slug)
    tier_updates = {
        "description": f"{tier.name} membership",
        "features": [f"{tier.name} feature"],
    }
    if tier_slug != "free":
        tier_updates.update({
            "price_eur_month": 10 + tier.level,
            "price_eur_year": 100 + tier.level,
        })
    Tier.objects.filter(pk=tier.pk).update(**tier_updates)
    tier.refresh_from_db()

    user, _created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.tier = tier
    user.subscription_id = subscription_id
    user.save()
    return user


def _seed_override_pricing_user(email):
    from accounts.models import TierOverride
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {tier.slug: tier for tier in Tier.objects.all()}
    user = _seed_pricing_user(
        email,
        "basic",
        subscription_id="sub_basic_override_pricing",
    )
    TierOverride.objects.filter(user=user).delete()
    TierOverride.objects.create(
        user=user,
        original_tier=tiers["basic"],
        override_tier=tiers["premium"],
        expires_at=timezone.now() + dt.timedelta(days=14),
        is_active=True,
    )
    return user


def _seed_stale_subscription_user(email):
    from accounts.models import User
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    user, _created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(DEFAULT_PASSWORD)
    user.email_verified = True
    user.tier = None
    user.pending_tier = None
    user.subscription_id = "sub_stale_pricing"
    user.save(update_fields=[
        "password",
        "email_verified",
        "tier",
        "pending_tier",
        "subscription_id",
    ])
    return user


def _auth_context(browser, email, django_db_blocker):
    with django_db_blocker.unblock():
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


def _tier_card(page, slug):
    return page.locator(f'[data-testid="pricing-tier-card"][data-tier-card="{slug}"]')


def _assert_bound_checkout_form(form, tier_slug, period, *, email, user_id):
    action = form.get_attribute("action")
    assert action.endswith(f"/payments/checkout/{tier_slug}/{period}")
    assert str(user_id) not in action
    assert email not in action
    assert "client_reference_id" not in action


@pytest.fixture
def pricing_payment_link_user(django_server, django_db_blocker):
    with django_db_blocker.unblock():
        return _seed_pricing_user("pricing-free+links@test.com", "free")


@pytest.fixture
def paid_account_user(django_server, django_db_blocker):
    with django_db_blocker.unblock():
        _seed_pricing_user(
            "pricing-main@test.com",
            "main",
            subscription_id="sub_main_pricing",
        )


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_signed_in_pricing_uses_server_bound_checkout_posts(
    django_server,
    browser,
    django_db_blocker,
    pricing_payment_link_user,
):
    email = "pricing-free+links@test.com"
    context = _auth_context(browser, email, django_db_blocker)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")

        assert "/api/checkout/create" not in page.content()
        assert page.locator("text=Current free plan").is_visible()

        for tier_slug in ("basic", "main", "premium"):
            form = _tier_card(page, tier_slug).locator(".tier-cta-form")
            assert form.locator('button[type="submit"]').inner_text().strip() == "Upgrade"
            _assert_bound_checkout_form(
                form,
                tier_slug,
                "annual",
                email=email,
                user_id=pricing_payment_link_user.pk,
            )

        page.locator("#billing-toggle").click()
        for tier_slug in ("basic", "main", "premium"):
            form = _tier_card(page, tier_slug).locator(".tier-cta-form")
            _assert_bound_checkout_form(
                form,
                tier_slug,
                "monthly",
                email=email,
                user_id=pricing_payment_link_user.pk,
            )
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_disabled_checkout_returns_to_visible_recovery_ui(
    django_server,
    browser,
    django_db_blocker,
    pricing_payment_link_user,
):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    with django_db_blocker.unblock():
        IntegrationSetting.objects.update_or_create(
            key="AUTHENTICATED_CHECKOUT_BINDING_ENABLED",
            defaults={"value": "false", "group": "stripe"},
        )
        clear_config_cache()

    context = _auth_context(
        browser,
        pricing_payment_link_user.email,
        django_db_blocker,
    )
    page = context.new_page()
    try:
        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        _tier_card(page, "basic").locator('button[type="submit"]').click()

        banner = page.get_by_test_id("checkout-recovery-banner")
        banner.wait_for(state="visible")
        assert "checkout_error=temporarily_unavailable" in page.url
        assert "Checkout is temporarily unavailable" in banner.inner_text()
        assert banner.get_by_role("link", name="View membership tiers").is_visible()
        support = banner.get_by_role("link", name="Contact support")
        assert support.is_visible()
        assert support.get_attribute("href").startswith(
            "mailto:contact@aishippinglabs.com"
        )
        screenshot = Path(".tmp/screenshots/issue-1105-checkout-recovery.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
    finally:
        context.close()
        clear_config_cache()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_operator_queue_shows_complete_stripe_repair_trail(
    django_server,
    browser,
    django_db_blocker,
):
    from accounts.models import User
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting
    from payments.models import CheckoutFulfillment, PaymentAccountMismatch, Tier
    from playwright_tests.conftest import ensure_tiers

    with django_db_blocker.unblock():
        ensure_tiers()
        staff = User.objects.create_user(
            email="payment-trail-staff@test.com",
            password=DEFAULT_PASSWORD,
            is_staff=True,
        )
        paid = User.objects.create_user(email="payment-trail-paid@test.com")
        paid.tier = Tier.objects.get(slug="free")
        paid.save(update_fields=["tier"])
        mismatch = PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_operator_trail_1105",
            stripe_customer_id="cus_operator_trail_1105",
            stripe_subscription_id="sub_operator_trail_1105",
            stripe_email="different-billing@test.com",
            paid_user=paid,
            reason=PaymentAccountMismatch.REASON_BILLING_EMAIL_MISMATCH,
        )
        CheckoutFulfillment.objects.create(
            stripe_session_id=mismatch.stripe_session_id,
            user=paid,
            status=CheckoutFulfillment.STATUS_QUARANTINED,
            reason=PaymentAccountMismatch.REASON_BILLING_EMAIL_MISMATCH,
        )
        IntegrationSetting.objects.update_or_create(
            key="STRIPE_DASHBOARD_ACCOUNT_ID",
            defaults={"value": "acct_operator_1105", "group": "stripe"},
        )
        clear_config_cache()

    context = _auth_context(browser, staff.email, django_db_blocker)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}/studio/users/payment-mismatches/",
            wait_until="domcontentloaded",
        )
        row = page.get_by_test_id("payment-mismatch-row")
        row.wait_for(state="visible")
        assert "Outcome: quarantined" in row.inner_text()
        assert "cs_operator_trail_1105" in row.inner_text()
        assert "cus_operator_trail_1105" in row.locator(
            'a[href*="/customers/"]'
        ).get_attribute("href")
        assert "sub_operator_trail_1105" in row.get_by_test_id(
            "stripe-subscription-link"
        ).inner_text()
        assert "/checkout/sessions/cs_operator_trail_1105" in row.get_by_test_id(
            "stripe-session-link"
        ).get_attribute("href")
        screenshot = Path(".tmp/screenshots/issue-1105-operator-repair-trail.png")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
    finally:
        context.close()
        clear_config_cache()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_paid_account_uses_customer_portal_without_local_plan_controls(
    django_server,
    browser,
    django_db_blocker,
    paid_account_user,
):
    context = _auth_context(browser, "pricing-main@test.com", django_db_blocker)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")

        assert page.locator("#tier-name").inner_text().strip() == "Main"
        portal = page.locator("#manage-subscription-btn")
        assert portal.is_visible()
        assert portal.get_attribute("href") == settings.STRIPE_CUSTOMER_PORTAL_URL
        assert page.locator("#upgrade-btn").count() == 0
        assert page.locator("#downgrade-btn").count() == 0
        assert page.locator("#cancel-btn").count() == 0
        assert "/api/subscription/" not in page.content()
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_override_member_pricing_uses_temporary_access_and_portal_actions(
    django_server,
    browser,
    django_db_blocker,
):
    email = "pricing-basic-override@test.com"
    with django_db_blocker.unblock():
        _seed_override_pricing_user(email)

    context = _auth_context(browser, email, django_db_blocker)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")

        basic = _tier_card(page, "basic")
        assert "Current plan" in basic.inner_text()
        assert (
            "Base subscription. Temporary Premium access is active."
            in basic.inner_text()
        )

        main = _tier_card(page, "main")
        assert "Temporary access" in main.inner_text()
        assert (
            "Included with your temporary Premium access."
            in main.inner_text()
        )

        premium = _tier_card(page, "premium")
        assert "Temporary access" in premium.inner_text()
        assert "Temporary access active until" in premium.inner_text()

        for tier_slug in ("main", "premium"):
            card = _tier_card(page, tier_slug)
            assert card.locator(".tier-cta-link").count() == 0
            portal = card.locator('[data-action="manage-subscription"]')
            assert portal.inner_text().strip() == "Manage Subscription"
            assert portal.get_attribute("href") == settings.STRIPE_CUSTOMER_PORTAL_URL
    finally:
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_stale_subscription_pricing_shows_review_warning_and_portal_actions(
    django_server,
    browser,
    django_db_blocker,
):
    email = "pricing-stale-subscription@test.com"
    with django_db_blocker.unblock():
        _seed_stale_subscription_user(email)

    context = _auth_context(browser, email, django_db_blocker)
    page = context.new_page()
    try:
        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")

        free = _tier_card(page, "free")
        assert "Included" in free.inner_text()
        assert "Your subscription needs review." in free.inner_text()

        for tier_slug in ("basic", "main", "premium"):
            card = _tier_card(page, tier_slug)
            card_text = card.inner_text()
            assert "Manage Subscription" in card_text
            assert (
                "Your subscription needs review before changing plans."
                in card_text
            )
            assert "Join" not in card_text
            assert card.locator(".tier-cta-link").count() == 0
            portal = card.locator('[data-action="manage-subscription"]')
            assert portal.inner_text().strip() == "Manage Subscription"
            assert portal.get_attribute("href") == settings.STRIPE_CUSTOMER_PORTAL_URL
    finally:
        context.close()
