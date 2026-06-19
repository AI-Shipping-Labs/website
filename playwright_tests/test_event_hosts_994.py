"""Playwright E2E for event hosts (#994).

Usage:
    uv run pytest playwright_tests/test_event_hosts_994.py -v
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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

SEED_SLUGS = ["alexey-grigorev", "valeriia-kuka"]


def _ensure_seed_hosts():
    from events.models import Host

    Host.objects.update_or_create(
        slug="alexey-grigorev",
        defaults={
            "name": "Alexey Grigorev",
            "title": "Chief Agent Officer at AI Shipping Labs",
            "bio": "Software engineer and machine learning practitioner.",
            "email": "alexey@aishippinglabs.com",
            "is_active": True,
        },
    )
    Host.objects.update_or_create(
        slug="valeriia-kuka",
        defaults={
            "name": "Valeriia Kuka",
            "title": "Content Strategist",
            "bio": "Content strategist and technical writer.",
            "email": "valeriia@aishippinglabs.com",
            "is_active": True,
        },
    )


def _reset_state():
    from django.db import connection

    from events.models import Event, EventHost, EventRegistration, EventSeries, Host

    EventRegistration.objects.all().delete()
    EventHost.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    Host.objects.exclude(slug__in=SEED_SLUGS).delete()
    _ensure_seed_hosts()
    connection.close()


def _make_event(slug, title, *, with_hosts=False):
    from django.db import connection

    from events.models import Event, EventHost, Host

    start = datetime.now(timezone.utc) + timedelta(days=30)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description="A live session on **shipping** AI projects.",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        timezone="UTC",
        status="upcoming",
        origin="studio",
        published=True,
        zoom_meeting_id="123",
        zoom_join_url="https://zoom.us/j/123",
    )
    if with_hosts:
        alexey = Host.objects.get(slug="alexey-grigorev")
        valeriia = Host.objects.get(slug="valeriia-kuka")
        EventHost.objects.create(event=event, host=alexey, position=0)
        EventHost.objects.create(event=event, host=valeriia, position=1)
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestEventHosts994:
    @pytest.mark.core
    def test_staff_creates_host_and_it_is_assignable(
        self, django_server, browser,
    ):
        from django.db import connection

        from events.models import Host

        _reset_state()
        _create_staff_user("staff-hosts-994@test.com")
        event = _make_event("assignable-host-event", "Assignable Host Event")

        ctx = _auth_context(browser, "staff-hosts-994@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/studio/hosts/", wait_until="domcontentloaded")
        page.locator('[data-testid="host-new-button"]').click()
        page.wait_for_url(re.compile(r".*/studio/hosts/new$"))
        page.fill('input[name="name"]', "Jordan Lee")
        page.fill('input[name="slug"]', "jordan-lee")
        page.fill('input[name="title"]', "AI Product Engineer")
        page.fill('textarea[name="bio"]', "**Jordan** hosts build sessions.")
        page.fill('input[name="photo_url"]', "https://cdn.example.com/jordan.jpg")
        page.fill('input[name="email"]', "jordan@example.com")
        page.locator("button:has-text('Save Host')").click()
        page.wait_for_url(re.compile(r".*/studio/hosts/$"))

        expect(page.get_by_text("Jordan Lee")).to_be_visible()
        expect(page.get_by_text("AI Product Engineer")).to_be_visible()
        host = Host.objects.get(slug="jordan-lee")
        assert host.title == "AI Product Engineer"

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        hosts_select = page.locator('[data-testid="studio-event-hosts"]')
        expect(hosts_select).to_be_visible()
        expect(hosts_select.locator(f'option[value="{host.pk}"]')).to_have_text(
            "Jordan Lee"
        )

        connection.close()
        ctx.close()

    @pytest.mark.core
    def test_anonymous_visitor_is_redirected_from_studio_hosts(
        self, django_server, page,
    ):
        _reset_state()
        page.goto(f"{django_server}/studio/hosts/", wait_until="domcontentloaded")
        page.wait_for_url(re.compile(r".*/accounts/login/.*"))
        expect(page.locator("body")).to_contain_text("Sign in")

    @pytest.mark.core
    def test_staff_assigns_hosts_and_public_detail_renders_them(
        self, django_server, browser,
    ):
        from django.db import connection

        from events.models import Event, EventHost, Host

        _reset_state()
        _create_staff_user("staff-host-assign-994@test.com")
        event = _make_event("hosted-public-event", "Hosted Public Event")
        alexey = Host.objects.get(slug="alexey-grigorev")
        valeriia = Host.objects.get(slug="valeriia-kuka")
        valeriia.bio = "**Content strategist** and technical writer."
        valeriia.save()
        EventHost.objects.create(event=event, host=valeriia, position=0)
        EventHost.objects.create(event=event, host=alexey, position=1)
        connection.close()

        anon = browser.new_context(viewport={"width": 1280, "height": 900})
        anon_page = anon.new_page()
        event = Event.objects.get(pk=event.pk)
        anon_page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        description = anon_page.locator(".prose").filter(has_text="shipping").first
        hosts_section = anon_page.locator('[data-testid="event-hosts"]')
        expect(description).to_be_visible()
        expect(hosts_section).to_be_visible()
        assert description.bounding_box()["y"] < hosts_section.bounding_box()["y"]
        hosts = anon_page.locator('[data-testid="event-host"]')
        expect(hosts).to_have_count(2)
        expect(hosts.nth(0)).to_contain_text("Valeriia Kuka")
        expect(hosts.nth(0)).to_contain_text("Content Strategist")
        expect(hosts.nth(1)).to_contain_text("Alexey Grigorev")
        expect(hosts.nth(1)).to_contain_text(
            "Chief Agent Officer at AI Shipping Labs"
        )
        expect(hosts.nth(0).locator("img")).to_have_attribute("src", re.compile("valeriia.png"))
        expect(hosts.nth(1).locator("img")).to_have_attribute("src", re.compile("alexey.png"))
        expect(hosts_section).to_contain_text("Software engineer")
        # The markdown bio is rendered into HTML, not shown as raw markdown.
        assert anon_page.locator('[data-testid="event-hosts"] strong').count() >= 1
        expect(anon_page.locator('[data-testid="event-hosts"]')).not_to_contain_text(
            "**"
        )
        anon.close()

    @pytest.mark.core
    def test_single_host_detail_uses_full_width_layout(
        self, django_server, page,
    ):
        from django.db import connection

        from events.models import Event, EventHost, Host

        _reset_state()
        event = _make_event("single-host-public-event", "Single Host Public Event")
        alexey = Host.objects.get(slug="alexey-grigorev")
        EventHost.objects.create(event=event, host=alexey, position=0)
        connection.close()

        event = Event.objects.get(pk=event.pk)
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        hosts_section = page.locator('[data-testid="event-hosts"]')
        host = page.locator('[data-testid="event-host"]')
        expect(hosts_section).to_be_visible()
        expect(host).to_have_count(1)
        expect(host).to_have_attribute("data-layout", "single")
        expect(hosts_section.locator(".sm\\:grid-cols-2")).to_have_count(0)
        expect(host).to_contain_text("Alexey Grigorev")
        expect(host).to_contain_text("Chief Agent Officer at AI Shipping Labs")

    @pytest.mark.core
    def test_event_without_hosts_has_no_empty_hosts_section(
        self, django_server, page,
    ):
        from events.models import Event

        _reset_state()
        event = _make_event("clean-no-hosts-event", "Clean No Hosts Event")
        event = Event.objects.get(pk=event.pk)

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("h1")).to_contain_text("Clean No Hosts Event")
        expect(page.locator('[data-testid="event-hosts"]')).to_have_count(0)
        expect(page.locator("body")).not_to_contain_text("Hosted by")
