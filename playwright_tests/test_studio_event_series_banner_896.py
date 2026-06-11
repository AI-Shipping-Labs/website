"""Playwright E2E for Studio event-series auto-banners (issue #896).

Covers the Studio author journey (create queues a banner; the detail page
shows the placeholder / image and a Regenerate control) and the public
series link-preview surface (header banner image, OG/Twitter image, site
default fallback, og:title from the series name).

The banner-generator Lambda is never hit: we configure the integration
settings directly and set ``auto_banner_url`` on the fixture, per
testing-guidelines. Thanks to issue #885 the in-process server binds an
ephemeral port (``django_server`` fixture), so this can run alongside
other E2E suites without colliding.

Usage:
    uv run pytest playwright_tests/test_studio_event_series_banner_896.py -v
"""

import os
import re
from datetime import datetime, time, timedelta

import pytest

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

# Local-only: seeds the DB directly and injects session cookies.
pytestmark = pytest.mark.local_only

BANNER_URL = "https://cdn.example.com/banners/event_series/fixture.jpg"


def _reset_state():
    from django.db import connection
    from django_q.models import OrmQ, Task

    from events.models import Event, EventRegistration, EventSeries
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    OrmQ.objects.all().delete()
    Task.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    IntegrationSetting.objects.filter(
        key__startswith="BANNER_GENERATOR_",
    ).delete()
    clear_config_cache()
    connection.close()


def _enable_banner_generator():
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    for key, value in (
        ("BANNER_GENERATOR_FUNCTION_URL", "https://lambda.example.com/"),
        ("BANNER_GENERATOR_AUTH_TOKEN", "token-abc"),
        ("AWS_S3_CONTENT_BUCKET", "content-bucket"),
        ("CONTENT_CDN_BASE", "https://cdn.example.com"),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                "value": value,
                "is_secret": False,
                "group": "banner_generator",
                "description": "",
            },
        )
    clear_config_cache()
    connection.close()


def _make_series(slug, name, **overrides):
    from django.db import connection

    from events.models import EventSeries

    defaults = dict(
        slug=slug,
        name=name,
        description="Weekly office hours for shipping AI agents.",
        cadence="weekly",
        cadence_weeks=1,
        day_of_week=2,
        start_time=time(18, 0),
        timezone="Europe/Berlin",
    )
    defaults.update(overrides)
    series = EventSeries.objects.create(**defaults)
    connection.close()
    return series


@pytest.mark.django_db(transaction=True)
class TestStudioSeriesBannerAuthorJourney:

    @pytest.mark.core
    def test_create_series_shows_banner_section_placeholder(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-series-banner1@test.com")
        ctx = _auth_context(browser, "staff-series-banner1@test.com")
        page = ctx.new_page()

        future = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")
        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="name"]', "AI Agents Office Hours")
        page.fill('input[name="start_date"]', future)
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="occurrences"]', "4")
        page.locator('[data-testid="sticky-save-action"]').click()

        page.wait_for_url(re.compile(r".*/studio/event-series/\d+/$"))
        # The shared generated-banner section renders on the detail page.
        assert page.locator(
            '[data-testid="banner-generator-section"]'
        ).count() == 1
        # A Regenerate control is present (enabled button or in-flight variant).
        regenerate_controls = (
            page.locator('[data-testid="banner-generator-regenerate-button"]').count()
            + page.locator(
                '[data-testid="banner-generator-regenerate-button-disabled-inflight"]'
            ).count()
        )
        assert regenerate_controls == 1
        ctx.close()

    @pytest.mark.core
    def test_regenerate_from_detail_flashes_confirmation(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-series-banner2@test.com")
        series = _make_series(
            "series-banner-regen", "Regen Series", auto_banner_url=BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-series-banner2@test.com")
        page = ctx.new_page()

        detail_url = f"{django_server}/studio/event-series/{series.pk}/"
        page.goto(detail_url, wait_until="domcontentloaded")
        # The existing banner image is shown, not the placeholder.
        img = page.locator('[data-testid="banner-generator-image"]')
        assert img.count() == 1
        assert img.get_attribute("src") == BANNER_URL
        assert page.locator(
            '[data-testid="banner-generator-placeholder"]'
        ).count() == 0

        page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series.pk}/$"),
        )
        assert "Banner regeneration queued" in page.content()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestPublicSeriesBannerPreview:

    def _og_image(self, page):
        return page.locator(
            'meta[property="og:image"]'
        ).first.get_attribute("content")

    def _twitter_image(self, page):
        return page.locator(
            'meta[name="twitter:image"]'
        ).first.get_attribute("content")

    @pytest.mark.core
    def test_header_banner_and_og_use_auto_banner(self, django_server, page):
        _reset_state()
        _ensure_tiers()
        series = _make_series(
            "series-auto-banner", "Auto Banner Series",
            auto_banner_url=BANNER_URL,
        )
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        # Header banner image renders.
        banner = page.locator('[data-testid="series-banner"] img')
        assert banner.count() == 1
        assert banner.get_attribute("src") == BANNER_URL
        # OG/Twitter image point at the banner.
        assert self._og_image(page) == BANNER_URL
        assert self._twitter_image(page) == BANNER_URL
        # og:title reflects the series name, not the generic site title.
        og_title = page.locator(
            'meta[property="og:title"]'
        ).first.get_attribute("content")
        assert og_title == "Auto Banner Series"

    def test_no_banner_renders_cleanly_with_default_og(
        self, django_server, page,
    ):
        _reset_state()
        _ensure_tiers()
        series = _make_series(
            "series-no-banner", "Plain Series", auto_banner_url="",
        )
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        # No header banner box and no broken image.
        assert page.locator('[data-testid="series-banner"]').count() == 0
        # OG falls back to the site default.
        og = self._og_image(page)
        assert "/static/ai-shipping-labs.jpg" in og
        assert og  # never empty
        # The page still renders (series name heading present).
        assert page.locator('[data-testid="series-name"]').first.is_visible()
