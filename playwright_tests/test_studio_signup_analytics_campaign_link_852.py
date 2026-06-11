"""Playwright E2E for matched first-touch campaign drill-through (#852).

Covers the operator journey on ``/studio/signup-analytics/`` for the
"Top first-touch campaigns" section:

* A first-touch campaign value that matches an existing ``UtmCampaign.slug``
  renders as a clickable link that navigates to that campaign's UTM analytics
  page, preserving the active Date range.
* The matched link carries a tooltip clarifying where it leads.
* An external campaign code (Mailchimp/Substack-style) that can never match a
  ``UtmCampaign`` slug renders as plain text with no anchor.

Usage:
    uv run pytest playwright_tests/test_studio_signup_analytics_campaign_link_852.py -v
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


def _seed_campaign(slug):
    from integrations.models import UtmCampaign

    UtmCampaign.objects.get_or_create(
        slug=slug,
        defaults={
            "name": slug.replace("_", " ").title(),
            "default_utm_source": "newsletter",
            "default_utm_medium": "email",
        },
    )
    connection.close()


def _seed_signups_with_campaign(n, *, campaign, prefix, days_ago=1):
    """Create ``n`` signups with the given first-touch utm_campaign value."""
    from accounts.models import User
    from analytics.models import UserAttribution

    now = timezone.now()
    for i in range(n):
        user = User.objects.create_user(email=f"{prefix}{i}@t.com", password="x")
        attr, _ = UserAttribution.objects.get_or_create(user=user)
        attr.first_touch_utm_campaign = campaign
        attr.save()
        UserAttribution.objects.filter(pk=attr.pk).update(
            created_at=now - timedelta(days=days_ago)
        )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSignupAnalyticsCampaignDrillLink:
    """Operator clicks through from a recognized campaign to its analytics."""

    def test_matched_campaign_links_to_utm_analytics_and_preserves_range(
        self, django_server, browser
    ):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_campaign("spring_launch")
        _seed_signups_with_campaign(4, campaign="spring_launch", prefix="c")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )

        link = page.locator('[data-testid="campaign-drill-link"]')
        expect(link).to_have_text("spring_launch")
        # Tooltip clarifies where the matched link leads.
        expect(link).to_have_attribute(
            "title", "Opens UTM campaign analytics for spring_launch"
        )

        link.click()
        page.wait_for_url("**/utm-analytics/campaign/spring_launch/?range=7d")
        assert "/utm-analytics/campaign/spring_launch/" in page.url
        assert "range=7d" in page.url

        context.close()

    def test_external_campaign_code_is_plain_text_not_a_link(
        self, django_server, browser
    ):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        code = "934616d2a5-email_campaign_2026_05_18_12_38"
        _seed_signups_with_campaign(3, campaign=code, prefix="ext")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )

        # The external code is shown in the campaign table cell.
        campaign_cell = page.get_by_role("cell", name=code, exact=True).first
        expect(campaign_cell).to_be_visible()
        # The cell value is plain text — there is no anchor inside it.
        expect(campaign_cell.locator("a")).to_have_count(0)
        # And no matched drill link anywhere on the page for it.
        expect(page.locator('[data-testid="campaign-drill-link"]')).to_have_count(0)

        context.close()
