"""Playwright coverage for Studio UTM Analytics tables (#387)."""

import os
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

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


def _clear_analytics_data():
    from analytics.models import CampaignVisit, UserAttribution
    from integrations.models import UtmCampaign, UtmCampaignLink
    from payments.models import ConversionAttribution

    ConversionAttribution.objects.all().delete()
    UserAttribution.objects.all().delete()
    CampaignVisit.objects.all().delete()
    UtmCampaignLink.objects.all().delete()
    UtmCampaign.objects.all().delete()
    connection.close()


def _seed_visit(slug, *, content="", source="newsletter", medium="email", anon="anon", days_ago=0):
    from analytics.models import CampaignVisit

    visit = CampaignVisit.objects.create(
        utm_campaign=slug,
        utm_content=content,
        utm_source=source,
        utm_medium=medium,
        anonymous_id=anon,
    )
    if days_ago:
        CampaignVisit.objects.filter(pk=visit.pk).update(
            ts=timezone.now() - timedelta(days=days_ago)
        )
    connection.close()
    return visit


def _seed_launch_campaign():
    from integrations.models import UtmCampaign, UtmCampaignLink

    campaign = UtmCampaign.objects.create(
        name="Launch April",
        slug="launch_april",
        default_utm_source="newsletter",
        default_utm_medium="email",
    )
    link = UtmCampaignLink.objects.create(
        campaign=campaign,
        utm_content="ai_hero_list",
        destination="/events/launch",
        label="AI Hero list",
    )
    connection.close()
    return campaign, link


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_reviews_dashboard_drills_down_and_edits_campaign(django_server, browser):
    _clear_analytics_data()
    _ensure_tiers()
    _create_staff_user("utm-analytics-admin@test.com")
    campaign, _link = _seed_launch_campaign()
    _seed_visit("launch_april", content="ai_hero_list", anon="launch-1")
    _seed_visit("launch_april", content="ai_hero_list", anon="launch-2", days_ago=2)
    _seed_visit("other_campaign", content="other", source="linkedin", anon="other-1")

    context = _auth_context(browser, "utm-analytics-admin@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/utm-analytics/?range=7d", wait_until="domcontentloaded")

    row = page.locator("tbody tr", has_text="Launch April")
    assert row.count() == 1
    assert "launch_april" in row.inner_text()
    assert "newsletter" in row.inner_text()
    assert "Drill down" in row.inner_text()
    assert "Edit campaign" in row.inner_text()
    assert page.locator('svg[aria-label="Trend sparkline"]').count() >= 1
    assert "Sparkline:" in page.content()

    row.locator('a:has-text("Drill down")').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith("/studio/utm-analytics/campaign/launch_april/?range=7d")

    page.goto(f"{django_server}/studio/utm-analytics/?range=7d", wait_until="domcontentloaded")
    page.locator("tbody tr", has_text="Launch April").locator('a:has-text("Edit campaign")').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith(f"/studio/utm-campaigns/{campaign.pk}/")
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_inspects_campaign_links_and_unminted_rows(django_server, browser):
    _clear_analytics_data()
    _ensure_tiers()
    _create_staff_user("utm-links-admin@test.com")
    _campaign, link = _seed_launch_campaign()
    _seed_visit("launch_april", content="ai_hero_list", anon="minted-1")
    _seed_visit("launch_april", content="ai_hero_list", anon="minted-2", days_ago=3)
    _seed_visit("launch_april", content="unminted_list", anon="unminted-1")

    context = _auth_context(browser, "utm-links-admin@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/utm-analytics/campaign/launch_april/?range=7d",
        wait_until="domcontentloaded",
    )

    minted_row = page.locator("tbody tr", has_text="ai_hero_list")
    assert minted_row.count() == 1
    assert "AI Hero list" in minted_row.inner_text()
    assert "/events/launch" in minted_row.inner_text()
    assert "View link" in minted_row.inner_text()

    unminted_row = page.locator("tbody tr", has_text="unminted_list")
    assert unminted_row.count() == 1
    assert "No minted link" in unminted_row.inner_text()
    assert page.locator('svg[aria-label="Trend sparkline"]').count() >= 1

    minted_row.locator('a:has-text("View link")').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith(
        f"/studio/utm-analytics/campaign/launch_april/link/{link.pk}/?range=7d"
    )
    context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_sees_conversion_columns_only_when_conversion_data_is_available(django_server, browser):
    from accounts.models import User
    from analytics import aggregations
    from payments.models import ConversionAttribution, Tier

    _clear_analytics_data()
    _ensure_tiers()
    _create_staff_user("utm-conversions-admin@test.com")
    _seed_launch_campaign()
    _seed_visit("launch_april", content="ai_hero_list", anon="paid-1")
    tier = Tier.objects.create(
        slug="e2e_mrr",
        name="E2E MRR",
        level=99,
        price_eur_month=10,
        price_eur_year=120,
    )
    user = User.objects.create_user(email="utm-paid@test.com", password="x")
    ConversionAttribution.objects.create(
        user=user,
        stripe_session_id="cs_e2e_utm_1",
        tier=tier,
        billing_period="monthly",
        amount_eur=10,
        mrr_eur=10,
        first_touch_utm_campaign="launch_april",
        first_touch_utm_content="ai_hero_list",
    )
    connection.close()

    context = _auth_context(browser, "utm-conversions-admin@test.com")
    page = context.new_page()
    for path in (
        "/studio/utm-analytics/",
        "/studio/utm-analytics/campaign/launch_april/",
    ):
        page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
        body = page.content()
        assert "Paid" in body
        assert "Signup -&gt; Paid" in body
        assert "MRR EUR" in body
        assert "EUR 10" in body

    original = aggregations.has_conversion_data
    aggregations.has_conversion_data = lambda: False
    try:
        for path in (
            "/studio/utm-analytics/",
            "/studio/utm-analytics/campaign/launch_april/",
        ):
            page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
            body = page.content()
            assert "Visits" in body
            assert "Signups" in body
            assert "Signup -&gt; Paid" not in body
            assert "MRR EUR" not in body
    finally:
        aggregations.has_conversion_data = original
        context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_staff_sees_empty_states_on_dashboard_and_campaign_detail(django_server, browser):
    _clear_analytics_data()
    _ensure_tiers()
    _create_staff_user("utm-empty-admin@test.com")
    _seed_launch_campaign()

    context = _auth_context(browser, "utm-empty-admin@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/utm-analytics/", wait_until="domcontentloaded")
    body = page.content()
    assert "No visits captured in this window" in body
    assert "UTM Campaigns" in body

    page.goto(
        f"{django_server}/studio/utm-analytics/campaign/launch_april/",
        wait_until="domcontentloaded",
    )
    body = page.content()
    assert "No visits for this campaign in this window" in body
    assert "Try widening the date range" in body
    context.close()
