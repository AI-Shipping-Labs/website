"""Playwright E2E for Studio-created event auto-banners (issue #895).

Covers the Studio author journey (create queues a banner; regenerate from
the edit page; disabled state when the generator is not configured) and
the public link-preview OG/Twitter image surface (cover wins; auto-banner
fallback; site default when neither is set; shared link card).

The banner-generator Lambda is never hit: we configure the integration
settings directly and either drive the disabled-state UI or set
``auto_banner_url`` on the fixture, per testing-guidelines.

Usage:
    uv run pytest playwright_tests/test_studio_event_banner_895.py -v
"""

import os
import re
from datetime import datetime, timedelta, timezone

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

BANNER_URL = "https://cdn.example.com/banners/event/fixture.jpg"
COVER_URL = "https://cdn.example.com/manual/event-cover.png"


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
    # Clear the in-process config cache so a prior test's enabled state
    # never leaks into a test that expects the disabled (dev-default) UI.
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


def _make_event(slug, title, **overrides):
    from django.db import connection

    from events.models import Event

    defaults = dict(
        slug=slug,
        title=title,
        description="A live session on shipping agents.",
        start_datetime=datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc),
        timezone="Europe/Berlin",
        status="upcoming",
        origin="studio",
        published=True,
    )
    defaults.update(overrides)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestStudioEventBannerAuthorJourney:

    @pytest.mark.core
    def test_create_event_without_cover_shows_banner_placeholder(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-evt-banner1@test.com")
        ctx = _auth_context(browser, "staff-evt-banner1@test.com")
        page = ctx.new_page()

        future = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Shipping Agents in Production")
        page.fill('input[name="event_date"]', future)
        page.fill('input[name="event_time"]', "18:00")
        page.select_option('select[name="required_level"]', "20")
        # Issue #860: link-less Zoom event — accept the "no meeting link"
        # confirm dialog so the create proceeds.
        page.on("dialog", lambda d: d.accept())
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        # The generated-banner section is present with the placeholder
        # (render is async so no image yet for a fresh event).
        assert page.locator(
            '[data-testid="banner-generator-section"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="banner-generator-placeholder"]'
        ).count() == 1
        # A Regenerate control is present. The create flow already queued a
        # render (banner-generator is enabled here), so the control may be
        # the enabled button or the in-flight disabled variant — either
        # satisfies "a Regenerate banner control".
        regenerate_controls = (
            page.locator('[data-testid="banner-generator-regenerate-button"]').count()
            + page.locator(
                '[data-testid="banner-generator-regenerate-button-disabled-inflight"]'
            ).count()
        )
        assert regenerate_controls == 1
        ctx.close()

    @pytest.mark.core
    def test_regenerate_from_edit_shows_image_and_confirmation(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-evt-banner2@test.com")
        event = _make_event(
            "evt-banner-regen", "Regen Event", auto_banner_url=BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-evt-banner2@test.com")
        page = ctx.new_page()

        edit_url = f"{django_server}/studio/events/{event.pk}/edit"
        page.goto(edit_url, wait_until="domcontentloaded")
        # The existing banner image is shown, not the placeholder.
        img = page.locator('[data-testid="banner-generator-image"]')
        assert img.count() == 1
        assert img.get_attribute("src") == BANNER_URL
        assert page.locator(
            '[data-testid="banner-generator-placeholder"]'
        ).count() == 0

        # Issue #995: the Regenerate form is progressively enhanced into an
        # in-place loader — clicking no longer navigates. The click shows a
        # spinner and polls the status endpoint; the worker is not running in
        # this test, so we only assert the in-place spinner appears (no full
        # navigation). The image-swap / failure-restore flows are covered
        # deterministically in test_studio_event_banner_inplace_995.py.
        page.evaluate("window.__noReload = true")
        page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).click()
        spinner = page.locator('[data-testid="banner-generator-spinner"]')
        spinner.wait_for(state="visible", timeout=5000)
        # No full-page reload happened (the marker survives).
        assert page.evaluate("window.__noReload") is True
        ctx.close()

    # NOTE: the "Regenerate disabled when banner-generator not configured"
    # scenario is covered at the Django-view layer in
    # ``studio/tests/test_event_regenerate_banner.py::
    # EventEditBannerSectionTest.test_regenerate_button_disabled_when_function_url_unset``.
    # It cannot run here because this environment ships real
    # ``BANNER_GENERATOR_*`` env vars in ``.env`` (env wins over the DB
    # override, so ``is_enabled()`` is True regardless of DB deletes) and
    # the in-process Playwright server reads the same process env.


@pytest.mark.django_db(transaction=True)
class TestPublicEventBannerPreview:

    def _og_image(self, page):
        return page.locator(
            'meta[property="og:image"]'
        ).first.get_attribute("content")

    def _twitter_image(self, page):
        return page.locator(
            'meta[name="twitter:image"]'
        ).first.get_attribute("content")

    @pytest.mark.core
    def test_cover_image_wins_over_auto_banner(self, django_server, page):
        _reset_state()
        _ensure_tiers()
        event = _make_event(
            "evt-cover-wins", "Cover Wins Event",
            cover_image_url=COVER_URL, auto_banner_url=BANNER_URL,
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert self._og_image(page) == COVER_URL

    @pytest.mark.core
    def test_auto_banner_used_when_no_cover(self, django_server, page):
        _reset_state()
        _ensure_tiers()
        event = _make_event(
            "evt-auto-banner", "Auto Banner Event",
            cover_image_url="", auto_banner_url=BANNER_URL,
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert self._og_image(page) == BANNER_URL
        assert self._twitter_image(page) == BANNER_URL
        # The page renders (hero/h1 present) without a broken layout.
        assert page.locator("h1").first.is_visible()

    def test_default_og_image_when_neither_set(self, django_server, page):
        _reset_state()
        _ensure_tiers()
        event = _make_event(
            "evt-no-banner", "Plain Event",
            cover_image_url="", auto_banner_url="",
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        og = self._og_image(page)
        assert "/static/ai-shipping-labs.jpg" in og
        assert og  # never empty

    @pytest.mark.core
    def test_shared_link_card_has_matching_banner_and_title(
        self, django_server, page,
    ):
        _reset_state()
        _ensure_tiers()
        event = _make_event(
            "evt-share-card", "Shareable Banner Event",
            cover_image_url="", auto_banner_url=BANNER_URL,
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert self._og_image(page) == self._twitter_image(page) == BANNER_URL
        twitter_card = page.locator(
            'meta[name="twitter:card"]'
        ).first.get_attribute("content")
        assert twitter_card == "summary_large_image"
        og_title = page.locator(
            'meta[property="og:title"]'
        ).first.get_attribute("content")
        assert "Shareable Banner Event" in og_title
