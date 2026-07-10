"""Playwright coverage for the restored /community launch landing page."""

import datetime
import os
import uuid

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


def _clear_events():
    from django.db import connection

    from events.models import Event

    Event.objects.all().delete()
    connection.close()


def _create_launch_event():
    from django.db import connection

    from events.models import Event

    start = timezone.now() - datetime.timedelta(days=4)
    event = Event.objects.create(
        title="AI Shipping Labs Community Launch",
        slug="community-launch",
        description="Original event detail copy.",
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        status="completed",
        published=True,
        recording_url="https://www.youtube.com/watch?v=community-launch",
        recap_html=(
            '<section id="launch-story">'
            "<h2>What happened at the AI Shipping Labs Community Launch</h2>"
            "<p>Builders saw how the community helps them ship real AI projects.</p>"
            '<a href="/pricing">Start building with the community</a>'
            "</section>"
        ),
    )
    path = event.get_absolute_url()
    connection.close()
    return path


def _open_mobile_menu(page):
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)


def _unique_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def test_anonymous_desktop_nav_opens_community_overview(django_server, page):
    _clear_events()
    _create_launch_event()

    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.get_by_test_id("nav-community-trigger").hover()
    menu = page.get_by_test_id("nav-community-menu")
    menu.wait_for(state="visible")
    links = menu.evaluate(
        """
        el => [...el.querySelectorAll('a[data-testid]')]
            .map(a => [a.getAttribute('data-testid'), a.getAttribute('href')])
        """
    )
    assert links[:4] == [
        ["nav-community-link-overview", "/community"],
        ["nav-community-link-membership", "/pricing"],
        ["nav-community-link-sprints", "/sprints"],
        ["nav-community-link-events", "/events"],
    ]

    page.get_by_test_id("nav-community-link-overview").click()
    page.wait_for_url("**/community")

    body = page.content()
    assert "AI Shipping Labs Community Launch" in body
    assert "What happened at the AI Shipping Labs Community Launch" in body
    assert "Builders saw how the community helps them ship real AI projects." in body
    assert page.get_by_test_id("community-landing-subscribe-cta").get_attribute("href") == "/subscribe"
    assert page.get_by_test_id("community-landing-register-cta").get_attribute("href") == "/register"
    assert page.get_by_test_id("community-landing-pricing-cta").get_attribute("href") == "/pricing"


def test_community_landing_omits_event_detail_framing(django_server, page):
    _clear_events()
    _create_launch_event()

    response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    assert response.status == 200

    assert page.locator("#site-header").count() == 1
    assert page.locator("footer").count() == 1
    body = page.content()
    for forbidden in [
        "Back to Events",
        'data-testid="event-registration-card"',
        'data-testid="event-feedback-section"',
        'data-testid="event-attendee-count"',
        'data-testid="event-add-to-calendar"',
        'data-testid="event-anonymous-email-form"',
        'data-testid="event-post-resources"',
    ]:
        assert forbidden not in body


def test_mobile_nav_overview_link_first_and_still_lists_community_surfaces(
    django_server, browser
):
    _clear_events()
    _create_launch_event()

    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")

        _open_mobile_menu(page)
        page.get_by_test_id("mobile-nav-community-trigger").click()
        menu = page.get_by_test_id("mobile-nav-community-menu")
        menu.wait_for(state="visible")
        links = menu.evaluate(
            """
            el => [...el.querySelectorAll('a[data-testid]')]
                .map(a => [a.getAttribute('data-testid'), a.getAttribute('href')])
            """
        )
        assert links[:4] == [
            ["mobile-nav-community-link-overview", "/community"],
            ["mobile-nav-community-link-membership", "/pricing"],
            ["mobile-nav-community-link-sprints", "/sprints"],
            ["mobile-nav-community-link-events", "/events"],
        ]

        page.get_by_test_id("mobile-nav-community-link-overview").click()
        page.wait_for_url("**/community")
        assert page.get_by_test_id("community-landing-heading").is_visible()
    finally:
        context.close()


def test_free_member_can_open_community_landing_directly(
    django_server, browser, django_db_blocker
):
    _clear_events()
    _create_launch_event()
    email = _unique_email("free-community")
    with django_db_blocker.unblock():
        create_user(email, tier_slug="free")

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
        assert response.status == 200
        assert "/accounts/login/" not in page.url
        assert page.get_by_test_id("community-landing-pricing-cta").is_visible()
        assert "upgrade required" not in page.content().lower()
    finally:
        context.close()


def test_main_member_uses_menu_without_member_only_redirect(
    django_server, browser, django_db_blocker
):
    _clear_events()
    _create_launch_event()
    email = _unique_email("main-community")
    with django_db_blocker.unblock():
        create_user(email, tier_slug="main")

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.get_by_test_id("nav-community-trigger").hover()
        page.get_by_test_id("nav-community-link-overview").click()
        page.wait_for_url("**/community")

        assert page.url.endswith("/community")
        assert "/community/slack" not in page.url
        assert "/events/" not in page.url
        assert page.get_by_test_id("community-recap-content").is_visible()
    finally:
        context.close()


def test_existing_past_recording_discovery_still_opens_event_detail(
    django_server, page
):
    _clear_events()
    event_path = _create_launch_event()

    page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
    card = page.get_by_test_id("past-recording-card").filter(
        has_text="AI Shipping Labs Community Launch"
    )
    assert card.count() == 1
    card.get_by_test_id("past-card-event-link").click()
    page.wait_for_url(f"**{event_path}")

    body = page.content()
    assert "Back to Events" in body
    assert "What happened at the AI Shipping Labs Community Launch" in body


def test_community_landing_metadata_is_specific_and_canonical(django_server, page):
    _clear_events()
    _create_launch_event()

    response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")
    assert response.status == 200

    assert page.title() == "AI Shipping Labs Community Launch | AI Shipping Labs"
    description = page.locator('meta[name="description"]').get_attribute("content")
    assert "AI Shipping Labs Community Launch recap" in description
    canonical = page.locator('link[rel="canonical"]').get_attribute("href")
    assert canonical == "https://aishippinglabs.com/community"


def test_missing_launch_content_returns_404(django_server, page):
    _clear_events()

    response = page.goto(f"{django_server}/community", wait_until="domcontentloaded")

    assert response.status == 404
