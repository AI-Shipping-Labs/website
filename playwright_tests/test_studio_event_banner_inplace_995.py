"""Playwright E2E for the Studio in-place banner loader (issue #995).

Clicking "Regenerate banner" on a Studio event edit page is progressively
enhanced: the JS handler POSTs the form via fetch, shows a disabled spinner,
polls the ``/studio/events/<id>/banner-status`` endpoint, then swaps the
banner <img> src in place on success (or restores the button + shows the
failure note on failure) WITHOUT a full-page reload.

The banner-generator Lambda and the django-q worker are never run. We enable
the integration via DB settings so the Regenerate button is active, then
stub the two network calls the handler makes (the regenerate POST and the
status poll) with Playwright route interception so the terminal state is
deterministic. We also assert the page never navigated (no full reload) by
tagging ``window`` before the click.

Usage:
    uv run pytest playwright_tests/test_studio_event_banner_inplace_995.py -v
"""

import os
from datetime import datetime, timezone

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

OLD_BANNER_URL = "https://cdn.example.com/banners/event/old.jpg"
NEW_BANNER_URL = "https://cdn.example.com/banners/event/new.jpg"


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
class TestStudioEventBannerInPlaceLoader:

    @pytest.mark.core
    def test_regenerate_swaps_image_without_full_reload(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-inplace1@test.com")
        event = _make_event(
            "evt-inplace-ok", "In-Place Event", auto_banner_url=OLD_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-inplace1@test.com")
        page = ctx.new_page()

        regen_path = f"/studio/events/{event.pk}/regenerate-banner"
        status_path = f"/studio/events/{event.pk}/banner-status"

        # Stub the regenerate POST so no real enqueue happens, and the status
        # poll so the terminal state is deterministic and immediate.
        page.route(
            f"**{regen_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"status": "queued", "task_id": "task-stub"}',
            ),
        )
        page.route(
            f"**{status_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"state": "success", "banner_url": "'
                    + NEW_BANNER_URL
                    + '", "task_detail_url": null}'
                ),
            ),
        )

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        img = page.locator('[data-testid="banner-generator-image"]')
        assert img.get_attribute("src") == OLD_BANNER_URL

        # Tag the window so a full navigation (reload) would wipe the marker.
        page.evaluate("window.__noReload = true")

        page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).click()

        # Spinner appears (disabled in-flight state) — no navigation.
        spinner = page.locator('[data-testid="banner-generator-spinner"]')
        spinner.wait_for(state="visible", timeout=5000)

        # The image swaps to the new (cache-busted) URL in place.
        page.wait_for_function(
            "(u) => { const i = document.querySelector("
            "'[data-testid=\"banner-generator-image\"]'); "
            "return i && i.src.indexOf(u) === 0; }",
            arg=NEW_BANNER_URL,
            timeout=8000,
        )
        new_src = page.locator(
            '[data-testid="banner-generator-image"]'
        ).get_attribute("src")
        assert new_src.startswith(NEW_BANNER_URL)
        assert "?t=" in new_src  # cache-busted

        # The page never did a full reload (marker survives) and the spinner
        # was replaced by the normal button.
        assert page.evaluate("window.__noReload") is True
        assert page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="banner-generator-spinner"]'
        ).count() == 0
        ctx.close()

    @pytest.mark.core
    def test_failed_render_restores_button_and_shows_note(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-inplace2@test.com")
        event = _make_event(
            "evt-inplace-fail", "Fail Event", auto_banner_url=OLD_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-inplace2@test.com")
        page = ctx.new_page()

        regen_path = f"/studio/events/{event.pk}/regenerate-banner"
        status_path = f"/studio/events/{event.pk}/banner-status"
        page.route(
            f"**{regen_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"status": "queued", "task_id": "task-stub"}',
            ),
        )
        page.route(
            f"**{status_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"state": "failed", "banner_url": "'
                    + OLD_BANNER_URL
                    + '", "task_detail_url": "/studio/worker/task/abc"}'
                ),
            ),
        )

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).click()

        # The spinner shows, then the button is restored on the failed poll.
        page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).wait_for(state="visible", timeout=8000)
        assert page.locator(
            '[data-testid="banner-generator-spinner"]'
        ).count() == 0
        # The failure note is surfaced (not stuck on a spinner).
        live = page.locator('[data-testid="banner-generator-live-status"]')
        assert "failed" in live.text_content().lower()
        # The image was NOT swapped to a cache-busted variant on failure.
        assert page.locator(
            '[data-testid="banner-generator-image"]'
        ).get_attribute("src") == OLD_BANNER_URL
        ctx.close()
