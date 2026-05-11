"""Playwright E2E for the Stripe deep-link on the Studio user detail page (issue #566).

Covers the two scenarios from the groomed spec:

1. Operator opens a paying member's profile and jumps to Stripe in one
   click via the ``user-detail-stripe-link`` anchor.
2. Operator views a member when no Stripe dashboard account is configured;
   the ``cus_*`` value is still readable as plain text but no anchor is
   rendered (nothing pretends to be a link that would 404).

Usage:
    uv run pytest playwright_tests/test_studio_user_detail_stripe_link.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
)
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

SETTINGS_KEY = "STRIPE_DASHBOARD_ACCOUNT_ID"


def _reset_users_and_settings(staff_email):
    """Drop every non-staff user and clear ``STRIPE_DASHBOARD_ACCOUNT_ID``
    so each test starts from a deterministic state."""
    from accounts.models import User
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    User.objects.exclude(email=staff_email).delete()
    IntegrationSetting.objects.filter(key=SETTINGS_KEY).delete()
    # Also pop any developer env var so the "blank" scenario truly
    # starts blank — IntegrationSetting deletion alone is not enough
    # when ``get_config`` falls back to ``os.environ``.
    os.environ.pop(SETTINGS_KEY, None)
    clear_config_cache()
    connection.close()


def _set_account_id(value):
    """Persist a dashboard account ID via ``IntegrationSetting`` + clear cache."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key=SETTINGS_KEY,
        defaults={
            "value": value,
            "group": "stripe",
            "is_secret": False,
            "description": "Stripe dashboard account ID for deep-links.",
        },
    )
    clear_config_cache()
    connection.close()


def _create_paid_member(email, stripe_customer_id):
    """Create a free-tier member with a ``stripe_customer_id`` set."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    tier = Tier.objects.get(slug="free")
    user = User.objects.create_user(
        email=email,
        password=DEFAULT_PASSWORD,
        email_verified=True,
    )
    user.tier = tier
    user.stripe_customer_id = stripe_customer_id
    user.save()
    pk = user.pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestStudioUserDetailStripeLink:
    # ---------------- Scenario 1 --------------------------------------------

    def test_stripe_link_opens_dashboard_when_account_configured(
        self, django_server, browser,
    ):
        staff_email = "stripe-detail-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)
        _set_account_id("acct_TEST123")
        member_pk = _create_paid_member("paid@test.com", "cus_ABC")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # The Profile card shows the Stripe row as an anchor with the
        # exact dashboard URL, target="_blank", and rel containing
        # ``noopener``. The visible label is the ``cus_*`` value itself.
        link = page.locator('[data-testid="user-detail-stripe-link"]')
        assert link.count() == 1
        assert link.is_visible()
        assert (
            link.get_attribute("href")
            == "https://dashboard.stripe.com/acct_TEST123/customers/cus_ABC"
        )
        assert link.get_attribute("target") == "_blank"
        assert (link.get_attribute("rel") or "").lower().find("noopener") != -1
        assert link.inner_text().strip() == "cus_ABC"
        context.close()

    # ---------------- Scenario 2 --------------------------------------------

    def test_stripe_value_is_plain_text_when_account_not_configured(
        self, django_server, browser,
    ):
        staff_email = "stripe-no-account-admin@test.com"
        _create_staff_user(staff_email)
        _reset_users_and_settings(staff_email)  # account id intentionally blank
        member_pk = _create_paid_member("paid@test.com", "cus_ABC")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        # No anchor — nothing that would 404 in the dashboard.
        assert (
            page.locator('[data-testid="user-detail-stripe-link"]').count()
            == 0
        )
        # But the cus_* value is still on the page so the operator can
        # read and copy it. Scope to the Profile section so we don't
        # match unrelated occurrences elsewhere on the page.
        profile = page.locator('[data-testid="user-detail-profile-section"]')
        assert profile.is_visible()
        assert "cus_ABC" in profile.inner_text()
        context.close()
