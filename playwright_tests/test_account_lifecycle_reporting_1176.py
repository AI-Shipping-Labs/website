"""Playwright coverage for account-lifecycle reporting (issue #1176)."""

import os
from datetime import timedelta

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

pytestmark = [pytest.mark.core, pytest.mark.local_only]


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _set_lifecycle_and_attribution(
    email,
    *,
    signup_source,
    account_activated,
    signup_path,
    created_at,
):
    from accounts.models import User
    from analytics.models import UserAttribution

    user = User.objects.get(email=email)
    user.signup_source = signup_source
    user.account_activated = account_activated
    user.save(update_fields=["signup_source", "account_activated"])
    attr, _created = UserAttribution.objects.get_or_create(user=user)
    attr.signup_path = signup_path
    attr.save(update_fields=["signup_path"])
    UserAttribution.objects.filter(pk=attr.pk).update(created_at=created_at)
    connection.close()


@pytest.mark.django_db(transaction=True)
def test_staff_filters_signup_analytics_and_users_by_lifecycle(django_server, browser):
    _ensure_tiers()
    staff_email = "lifecycle-admin@test.com"
    _create_staff_user(staff_email)
    _clear_users_except_staff(staff_email)

    now = timezone.now()
    _create_user("newsletter-only@test.com", tier_slug="free", unsubscribed=True)
    _set_lifecycle_and_attribution(
        "newsletter-only@test.com",
        signup_source="newsletter",
        account_activated=False,
        signup_path="newsletter",
        created_at=now - timedelta(hours=1),
    )
    _create_user("full-account@test.com", tier_slug="main")
    _set_lifecycle_and_attribution(
        "full-account@test.com",
        signup_source="signup",
        account_activated=True,
        signup_path="email_password",
        created_at=now - timedelta(hours=2),
    )
    _create_user("imported@test.com", tier_slug="free")
    _set_lifecycle_and_attribution(
        "imported@test.com",
        signup_source="imported",
        account_activated=False,
        signup_path="unknown",
        created_at=now - timedelta(hours=3),
    )

    context = _auth_context(browser, staff_email)
    page = context.new_page()

    page.goto(f"{django_server}/studio/signup-analytics/", wait_until="domcontentloaded")
    lifecycle_table = page.locator('[data-testid="signup-analytics-lifecycle-table"]')
    assert lifecycle_table.is_visible()
    assert lifecycle_table.get_by_text("Newsletter-only").is_visible()
    assert lifecycle_table.get_by_text("Full account").is_visible()
    assert lifecycle_table.get_by_text("Imported / unknown").is_visible()

    page.locator("#filter-account-lifecycle").select_option("newsletter_only")
    page.wait_for_url("**/studio/signup-analytics/*account_lifecycle=newsletter_only*")
    assert "account_lifecycle=newsletter_only" in page.url
    recent_table = page.locator('[data-testid="signup-analytics-recent-table"]')
    assert recent_table.get_by_text("newsletter-only@test.com").is_visible()
    assert page.get_by_test_id("signup-analytics-recent-lifecycle").get_by_text(
        "Newsletter-only"
    ).is_visible()
    assert recent_table.get_by_text("full-account@test.com").count() == 0

    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
    page.locator('[data-lifecycle-filter="newsletter_only"]').click()
    page.wait_for_url("**/studio/users/*account_lifecycle=newsletter_only*")
    users_table = page.locator('[data-testid="studio-users-list"]')
    assert users_table.get_by_text("newsletter-only@test.com").is_visible()
    assert page.get_by_test_id("user-list-lifecycle-pill").get_by_text(
        "Newsletter-only"
    ).is_visible()
    assert users_table.get_by_text("full-account@test.com").count() == 0

    context.close()
