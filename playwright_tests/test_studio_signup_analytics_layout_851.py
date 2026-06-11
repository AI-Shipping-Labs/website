"""Playwright E2E for the Signup Analytics headline/filter layout fix (#851).

Covers the operator-understanding journey on ``/studio/signup-analytics/``:

* The headline cards (Last 24h / 7d / 30d) render at the top, before the
  filter form, with a caption explaining they are fixed rolling windows that
  ignore the Date range.
* The filter form sits below the cards and is labelled as applying to the
  sections below it.
* Changing the Date range does NOT change the headline-card counts.
* Changing the Signup path DOES narrow the headline-card counts.

Usage:
    uv run pytest playwright_tests/test_studio_signup_analytics_layout_851.py -v
"""

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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

# Local-only: seeds DB rows and injects a session cookie, cannot run against
# the deployed dev environment.
pytestmark = pytest.mark.local_only


def _clear_attributions_except(staff_email):
    from accounts.models import User
    from analytics.models import UserAttribution

    User.objects.exclude(email=staff_email).delete()
    UserAttribution.objects.filter(user__email=staff_email).delete()
    connection.close()


def _seed_signups(n, *, signup_path="email_password", prefix="r", days_ago=1):
    """Create ``n`` User + UserAttribution rows ``days_ago`` days in the past."""
    from accounts.models import User
    from analytics.models import UserAttribution

    now = timezone.now()
    for i in range(n):
        user = User.objects.create_user(email=f"{prefix}{i}@t.com", password="x")
        attr, _ = UserAttribution.objects.get_or_create(user=user)
        attr.signup_path = signup_path
        attr.save()
        UserAttribution.objects.filter(pk=attr.pk).update(
            created_at=now - timedelta(days=days_ago)
        )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSignupAnalyticsHeadlineLayout:
    """Operator reads the dashboard and understands the cards vs filter split."""

    def test_cards_render_before_filter_with_caption(self, django_server, browser):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(3)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )

        headlines = page.locator('[data-testid="signup-analytics-headlines"]')
        filters = page.locator('[data-testid="signup-analytics-filters"]')
        expect(headlines).to_be_visible()
        expect(filters).to_be_visible()

        # The cards box appears above (lower top coordinate) the filter form.
        cards_box = headlines.bounding_box()
        filter_box = filters.bounding_box()
        assert cards_box is not None and filter_box is not None
        assert cards_box["y"] < filter_box["y"], (
            "Headline cards must sit above the filter form (#851)."
        )

        note = page.locator('[data-testid="headline-fixed-note"]')
        expect(note).to_contain_text("ignore the Date range")
        expect(note).to_contain_text("Signup path")

        expect(
            page.locator('[data-testid="filter-scope-note"]')
        ).to_contain_text("Filter the sections below")

        context.close()

    def test_date_range_change_does_not_change_headline_counts(
        self, django_server, browser
    ):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        # 5 signups 1 day ago: inside both 24h-card and 7d/30d-card windows,
        # but the dashboard default Date range is 7d.
        _seed_signups(5, days_ago=1)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )
        seven_card = page.locator(
            '[data-testid="signup-analytics-headlines"] > div'
        ).nth(1)
        expect(seven_card).to_contain_text("Last 7d")
        expect(seven_card.locator("div.text-3xl")).to_have_text("5")

        # Switch the Date range to 30 days — sections below change, but the
        # fixed 7d headline card still reads 5.
        page.select_option("select[name='range']", "30d")
        page.wait_for_url("**/signup-analytics/?*range=30d*")
        seven_card = page.locator(
            '[data-testid="signup-analytics-headlines"] > div'
        ).nth(1)
        expect(seven_card.locator("div.text-3xl")).to_have_text("5")

        context.close()

    def test_signup_path_change_narrows_headline_counts(
        self, django_server, browser
    ):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_signups(3, signup_path="google_oauth", prefix="g", days_ago=1)
        _seed_signups(2, signup_path="email_password", prefix="e", days_ago=1)

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )
        seven_card = page.locator(
            '[data-testid="signup-analytics-headlines"] > div'
        ).nth(1)
        expect(seven_card.locator("div.text-3xl")).to_have_text("5")

        # Narrow to Google OAuth — the headline card drops to 3 (cards honor
        # the Signup path even though they ignore the Date range).
        page.select_option("select[name='signup_path']", "google_oauth")
        page.wait_for_url("**/signup-analytics/?*signup_path=google_oauth*")
        seven_card = page.locator(
            '[data-testid="signup-analytics-headlines"] > div'
        ).nth(1)
        expect(seven_card.locator("div.text-3xl")).to_have_text("3")

        context.close()
