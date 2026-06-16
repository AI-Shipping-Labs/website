"""Playwright E2E for Studio event required_level behavior (issue #997).

Usage:
    uv run pytest playwright_tests/test_studio_event_required_level_997.py -v
"""

import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_event(slug, title, **overrides):
    from django.db import connection

    from events.models import Event

    defaults = {
        "slug": slug,
        "title": title,
        "description": "A focused event about shipping AI systems.",
        "start_datetime": datetime.now(timezone.utc) + timedelta(days=21),
        "end_datetime": datetime.now(timezone.utc) + timedelta(days=21, hours=1),
        "timezone": "UTC",
        "status": "upcoming",
        "origin": "studio",
        "published": True,
        "required_level": 0,
    }
    defaults.update(overrides)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestStudioEventRequiredLevel997:
    @pytest.mark.core
    def test_admin_gates_event_and_membership_access_updates(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-997@test.com")
        _create_user("free@test.com", tier_slug="free")
        _create_user("main@test.com", tier_slug="main")
        event = _make_event("studio-required-level-997", "Studio Gate 997")

        admin_ctx = _auth_context(browser, "staff-997@test.com")
        admin_page = admin_ctx.new_page()
        edit_url = f"{django_server}/studio/events/{event.pk}/edit"
        admin_page.goto(edit_url, wait_until="domcontentloaded")

        required_level = admin_page.locator('select[name="required_level"]')
        expect(required_level).to_be_enabled()
        assert required_level.input_value() == "0"
        expect(required_level.locator("option")).to_have_text([
            "Free (0)",
            "Basic (10)",
            "Main (20)",
            "Premium (30)",
        ])

        admin_page.on("dialog", lambda dialog: dialog.accept())
        required_level.select_option("20")
        admin_page.locator('[data-testid="sticky-save-action"]').click()
        admin_page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        expect(admin_page.locator('[data-testid="event-state-panel"]')).to_contain_text(
            "Level 20",
        )
        admin_page.reload(wait_until="domcontentloaded")
        assert admin_page.locator(
            'select[name="required_level"]',
        ).input_value() == "20"

        event_path = event.get_absolute_url()
        admin_ctx.close()

        free_ctx = _auth_context(browser, "free@test.com")
        free_page = free_ctx.new_page()
        free_page.goto(f"{django_server}{event_path}", wait_until="domcontentloaded")
        expect(free_page.locator('[data-testid="event-required-tier-label"]')).to_contain_text(
            "Main membership or above",
        )
        expect(free_page.locator("#register-btn")).to_have_count(0)
        free_ctx.close()

        main_ctx = _auth_context(browser, "main@test.com")
        main_page = main_ctx.new_page()
        main_page.goto(f"{django_server}{event_path}", wait_until="domcontentloaded")
        expect(main_page.locator("#register-btn")).to_be_visible()
        expect(main_page.locator('[data-testid="event-required-tier-label"]')).to_have_count(0)
        main_ctx.close()

        admin_ctx = _auth_context(browser, "staff-997@test.com")
        admin_page = admin_ctx.new_page()
        admin_page.goto(edit_url, wait_until="domcontentloaded")
        admin_page.on("dialog", lambda dialog: dialog.accept())
        admin_page.locator('select[name="required_level"]').select_option("0")
        admin_page.locator('[data-testid="sticky-save-action"]').click()
        admin_page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        expect(admin_page.locator('[data-testid="event-state-panel"]')).to_contain_text(
            "Level 0",
        )
        admin_ctx.close()

        free_ctx = _auth_context(browser, "free@test.com")
        free_page = free_ctx.new_page()
        free_page.goto(f"{django_server}{event_path}", wait_until="domcontentloaded")
        expect(free_page.locator("#register-btn")).to_be_visible()
        expect(free_page.locator('[data-testid="event-required-tier-label"]')).to_have_count(0)
        free_ctx.close()

    @pytest.mark.core
    def test_admin_creates_event_with_premium_required_level(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-create-997@test.com")

        ctx = _auth_context(browser, "staff-create-997@test.com")
        page = ctx.new_page()
        future_date = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")
        page.goto(f"{django_server}/studio/events/new", wait_until="domcontentloaded")
        page.fill('input[name="title"]', "Premium Studio Event 997")
        page.fill('input[name="event_date"]', future_date)
        page.fill('input[name="event_time"]', "18:00")
        page.select_option('select[name="required_level"]', "30")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        expect(page.locator('[data-testid="event-state-panel"]')).to_contain_text(
            "Level 30",
        )
        assert page.locator('select[name="required_level"]').input_value() == "30"
        ctx.close()

    @pytest.mark.core
    def test_synced_event_required_level_is_read_only(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-synced-997@test.com")
        event = _make_event(
            "synced-required-level-997",
            "Synced Gate 997",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/synced-required-level-997.md",
            source_commit="abc1234def5678901234567890123456789abcde",
            required_level=20,
        )

        ctx = _auth_context(browser, "staff-synced-997@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        expect(page.locator('[data-testid="origin-panel"]')).to_contain_text(
            "Synced from GitHub",
        )
        required_level = page.locator('select[name="required_level"]')
        expect(required_level).to_be_disabled()
        assert required_level.input_value() == "20"
        ctx.close()
