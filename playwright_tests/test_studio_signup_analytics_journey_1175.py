"""Playwright coverage for signup analytics journey context (#1175)."""

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

pytestmark = pytest.mark.local_only


def _clear_attributions_except(staff_email):
    from accounts.models import User
    from analytics.models import CampaignVisit, UserAttribution

    CampaignVisit.objects.all().delete()
    User.objects.exclude(email=staff_email).delete()
    UserAttribution.objects.filter(user__email=staff_email).delete()
    connection.close()


def _seed_journey_signups():
    from accounts.models import User
    from analytics.models import CampaignVisit, UserAttribution
    from integrations.models import UtmCampaign

    now = timezone.now()
    campaign = UtmCampaign.objects.create(
        name="Spring Launch",
        slug="spring_launch",
        default_utm_source="newsletter",
        default_utm_medium="email",
    )
    tracked_user = User.objects.create_user(
        email="journey@test.com",
        password="x",
    )
    attr, _ = UserAttribution.objects.get_or_create(user=tracked_user)
    attr.first_touch_utm_campaign = "spring_launch"
    attr.first_touch_campaign = campaign
    attr.anonymous_id = "anon-playwright-1175"
    attr.save()
    UserAttribution.objects.filter(pk=attr.pk).update(
        created_at=now - timedelta(hours=1)
    )

    direct_user = User.objects.create_user(
        email="direct@test.com",
        password="x",
    )
    direct_attr, _ = UserAttribution.objects.get_or_create(user=direct_user)
    direct_attr.save()
    UserAttribution.objects.filter(pk=direct_attr.pk).update(
        created_at=now - timedelta(minutes=30)
    )

    for path, hours_ago in (
        ("/pricing", 4),
        ("/blog/source", 3),
        ("/blog/deep-dive", 2),
    ):
        visit = CampaignVisit.objects.create(
            anonymous_id="anon-playwright-1175",
            path=path,
            utm_source="newsletter",
            utm_medium="email",
            utm_campaign="spring_launch",
        )
        CampaignVisit.objects.filter(pk=visit.pk).update(
            ts=now - timedelta(hours=hours_ago)
        )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSignupAnalyticsJourneyContext:
    def test_staff_sees_actionable_sources_activity_and_recent_journey(
        self, django_server, browser
    ):
        staff_email = "admin@test.com"
        _create_staff_user(staff_email)
        _clear_attributions_except(staff_email)
        _seed_journey_signups()

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )

        source_table = page.locator(
            '[data-testid="signup-analytics-actionable-source-table"]'
        )
        expect(source_table).to_contain_text("Spring Launch (spring_launch)")
        expect(source_table).to_contain_text("direct / no tracked source")
        expect(
            page.locator('[data-testid="actionable-source-campaign-drill-link"]')
        ).to_have_attribute(
            "href",
            "/studio/utm-analytics/campaign/spring_launch/?range=7d",
        )

        activity_table = page.locator(
            '[data-testid="signup-analytics-activity-table"]'
        )
        expect(activity_table).to_contain_text("Pricing")
        expect(activity_table).to_contain_text("/pricing")
        expect(activity_table).to_contain_text("Blog")
        expect(activity_table).to_contain_text("/blog/source")

        recent_table = page.locator(
            '[data-testid="signup-analytics-recent-table"]'
        )
        journey_row = recent_table.locator("tbody tr", has_text="journey@test.com")
        expect(journey_row).to_contain_text("Spring Launch (spring_launch)")
        expect(journey_row).to_contain_text("/pricing")
        expect(journey_row).to_contain_text("/blog/deep-dive")
        expect(journey_row).to_contain_text("3")
        expect(journey_row).to_contain_text("Blog")

        direct_row = recent_table.locator("tbody tr", has_text="direct@test.com")
        expect(direct_row).to_contain_text("direct / no tracked source")
        expect(direct_row).to_contain_text("No tracked pre-signup visits")

        context.close()
