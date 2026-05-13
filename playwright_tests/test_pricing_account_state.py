"""Production-mode Playwright smokes for pricing and account billing."""

from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
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


def _assert_prefilled_payment_link(href, expected_base, email):
    assert href.startswith(expected_base)
    assert "/api/checkout/create" not in href
    parsed = urlparse(href)
    assert parse_qs(parsed.query)["prefilled_email"] == [email]


@pytest.fixture
def pricing_payment_link_user(django_server, django_db_blocker):
    with django_db_blocker.unblock():
        _seed_pricing_user("pricing-free+links@test.com", "free")


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
def test_signed_in_pricing_uses_payment_links_with_prefilled_email(
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
            cta = _tier_card(page, tier_slug).locator(".tier-cta-link")
            assert cta.inner_text().strip() == "Upgrade"
            _assert_prefilled_payment_link(
                cta.get_attribute("href"),
                settings.STRIPE_PAYMENT_LINKS[tier_slug]["annual"],
                email,
            )

        page.locator("#billing-toggle").click()
        for tier_slug in ("basic", "main", "premium"):
            cta = _tier_card(page, tier_slug).locator(".tier-cta-link")
            _assert_prefilled_payment_link(
                cta.get_attribute("href"),
                settings.STRIPE_PAYMENT_LINKS[tier_slug]["monthly"],
                email,
            )
    finally:
        context.close()


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
