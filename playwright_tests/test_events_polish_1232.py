"""Browser regressions for Events calendar/list/detail polish (issue #1232)."""

import datetime
import os
from datetime import time, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
pytestmark = pytest.mark.local_only


def _clear_events():
    from content.models import Workshop
    from events.models import Event, EventSeries

    Workshop.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()


def _event(slug, *, start=None, status="upcoming", **kwargs):
    from events.models import Event

    event = Event.objects.create(
        title=slug.replace("-", " ").title(),
        slug=slug,
        start_datetime=start or timezone.now() + timedelta(days=7),
        status=status,
        **kwargs,
    )
    return event


def _calendar_events():
    first = _event(
        "calendar-keyboard-first",
        start=timezone.make_aware(datetime.datetime(2027, 3, 1, 14)),
    )
    second = _event(
        "calendar-keyboard-second",
        start=timezone.make_aware(datetime.datetime(2027, 3, 9, 14)),
        required_level=20,
    )
    return first, second


def _tab_to(page, element_id, *, max_tabs=60):
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement && document.activeElement.id") == element_id:
            return
    raise AssertionError(f"Tab order never reached #{element_id}")


def _vertical_spacing(locator):
    first = locator.nth(0).bounding_box()
    second = locator.nth(1).bounding_box()
    assert first is not None and second is not None
    return second["y"] - (first["y"] + first["height"])


@pytest.mark.django_db(transaction=True)
def test_keyboard_opens_and_closes_calendar_day_with_synchronized_aria(django_server, page):
    _clear_events()
    _calendar_events()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/events/calendar/2027/3", wait_until="domcontentloaded")

    first = page.locator("#calendar-day-2027-3-1")
    first_panel = page.locator("#day-events-2027-3-1")
    empty_day = page.locator('[data-calendar-week="1"] > div').nth(1)
    expect(first).to_have_attribute("role", "button")
    expect(first).to_have_attribute("aria-expanded", "false")
    expect(first).to_have_attribute("aria-controls", "day-events-2027-3-1")
    expect(empty_day).not_to_have_attribute("role", "button")
    expect(empty_day).not_to_have_attribute("tabindex", "0")

    _tab_to(page, "calendar-day-2027-3-1")
    expect(first).to_be_focused()
    page.keyboard.press("Enter")
    expect(first_panel).to_be_visible()
    expect(first).to_have_attribute("aria-expanded", "true")

    page.keyboard.press("Space")
    expect(first_panel).to_be_hidden()
    expect(first).to_have_attribute("aria-expanded", "false")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_keyboard_focus_is_visible_and_calendar_controls_are_44px(django_server, page):
    _clear_events()
    _calendar_events()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/events/calendar/2027/3", wait_until="domcontentloaded")

    first = page.locator("#calendar-day-2027-3-1")
    expect(first).to_be_visible()
    _tab_to(page, "calendar-day-2027-3-1")
    expect(first).to_be_focused()
    page.wait_for_function(
        "element => getComputedStyle(element).boxShadow !== 'none'",
        arg=first.element_handle(),
    )

    toggle = page.locator('[data-testid="events-view-toggle-row"]')
    for label in ("List", "Calendar"):
        control = toggle.get_by_role("link", name=label, exact=True)
        box = control.bounding_box()
        assert box is not None and box["height"] >= 44
        classes = control.get_attribute("class") or ""
        assert "rounded-full" in classes
        assert "px-4" in classes and "py-2" in classes

    for label in ("Previous month", "Next month"):
        box = page.get_by_role("link", name=label).bounding_box()
        assert box is not None and box["height"] >= 44
    today_box = page.get_by_role("link", name="Today", exact=True).bounding_box()
    assert today_box is not None and today_box["height"] >= 44

    toggle.get_by_role("link", name="List", exact=True).click()
    page.wait_for_url("**/events")
    list_toggle = page.locator('[data-testid="events-view-toggle-row"]')
    assert "bg-accent" in (list_toggle.get_by_role("link", name="List", exact=True).get_attribute("class") or "")


@pytest.mark.django_db(transaction=True)
def test_opening_second_week_day_closes_first_and_panel_precedes_next_week(django_server, page):
    _clear_events()
    _, second_event = _calendar_events()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/events/calendar/2027/3", wait_until="domcontentloaded")

    first = page.locator("#calendar-day-2027-3-1")
    second = page.locator("#calendar-day-2027-3-9")
    first.focus()
    page.keyboard.press("Enter")
    second.focus()
    page.keyboard.press("Space")

    expect(first).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#day-events-2027-3-1")).to_be_hidden()
    expect(second).to_have_attribute("aria-expanded", "true")
    second_panel = page.locator("#day-events-2027-3-9")
    expect(second_panel).to_be_visible()
    assert page.locator("[data-day-panel]:not(.hidden)").count() == 1
    assert second_panel.evaluate(
        """panel => {
            const nextWeek = document.querySelector('[data-calendar-week="3"]');
            return Boolean(panel.compareDocumentPosition(nextWeek) & Node.DOCUMENT_POSITION_FOLLOWING);
        }"""
    )

    panel_link = second_panel.get_by_role("link", name=second_event.title)
    expect(panel_link).to_have_attribute("href", second_event.get_absolute_url())
    expect(second_panel.get_by_text("Main or above", exact=True)).to_be_visible()
    panel_link.click()
    page.wait_for_url(f"**{second_event.get_absolute_url()}")


@pytest.mark.django_db(transaction=True)
def test_next_month_crosses_december_year_boundary(django_server, page):
    _clear_events()
    page.goto(f"{django_server}/events/calendar/2027/12", wait_until="domcontentloaded")

    page.get_by_role("link", name="Next month").click()

    page.wait_for_url("**/events/calendar/2028/1")
    expect(page.locator('[data-testid="calendar-heading"]')).to_have_text("January 2028")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_mobile_calendar_stays_chronological_agenda_with_canonical_links(django_server, page):
    _clear_events()
    first, second = _calendar_events()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/events/calendar/2027/3", wait_until="domcontentloaded")

    agenda = page.locator(".sm\\:hidden").filter(has=page.get_by_text(first.title))
    expect(agenda).to_be_visible()
    expect(agenda.get_by_role("link", name=first.title)).to_have_attribute("href", first.get_absolute_url())
    expect(agenda.get_by_role("link", name=second.title)).to_have_attribute("href", second.get_absolute_url())
    assert agenda.locator('[role="button"][data-calendar-day]').count() == 0
    titles = agenda.locator("a > div.font-medium").all_inner_texts()
    assert titles == [first.title, second.title]
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_mixed_event_cards_share_compact_padding_and_16px_stack_rhythm(django_server, page):
    from events.models import EventSeries

    _clear_events()
    now = timezone.now()
    standalone = _event("single-upcoming-1232", start=now + timedelta(days=1))
    series = EventSeries.objects.create(name="Build Series 1232", slug="build-series-1232", start_time=time(18))
    for position, days in enumerate((2, 9), start=1):
        _event(
            f"series-occurrence-{position}-1232",
            start=now + timedelta(days=days),
            event_series=series,
            series_position=position,
        )
    _event(
        "ordinary-past-1232",
        start=now - timedelta(days=3),
        status="completed",
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    stack = page.locator('[data-testid="upcoming-events-stack"]')
    stack_cards = stack.locator(":scope > article")
    expect(stack_cards.nth(1)).to_have_css("margin-top", "16px")
    assert _vertical_spacing(stack_cards) == pytest.approx(16)
    for card in (
        page.locator('[data-testid="upcoming-event-card"]'),
        page.locator('[data-testid="event-series-card"]'),
        page.locator('[data-testid="past-event-card"]'),
    ):
        expect(card.first).to_have_css("padding-top", "20px")
    standalone_link = page.locator(f'a[href="{standalone.get_absolute_url()}"]').first
    expect(standalone_link).to_be_visible()
    standalone_link.click()
    page.wait_for_url(f"**{standalone.get_absolute_url()}")
    page.go_back(wait_until="domcontentloaded")
    series_link = page.locator(f'a[href="{series.get_absolute_url()}"]').first
    expect(series_link).to_be_visible()
    series_link.click()
    page.wait_for_url(f"**{series.get_absolute_url()}")


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_past_recording_cards_keep_actions_and_compact_rhythm(django_server, page):
    from content.models import Workshop

    _clear_events()
    now = timezone.now()
    standalone = _event(
        "standalone-recording-1232",
        start=now - timedelta(days=5),
        status="completed",
        recording_url="https://example.com/standalone-video",
    )
    linked = _event(
        "workshop-recording-1232",
        start=now - timedelta(days=4),
        status="completed",
        kind="workshop",
        recording_s3_url="https://example.com/workshop-video.mp4",
    )
    workshop = Workshop.objects.create(
        slug="workshop-recording-handoff-1232",
        title="Workshop Recording Handoff 1232",
        date=now.date(),
        status="published",
        event=linked,
    )
    page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")

    stack = page.locator('[data-testid="past-recordings-stack"]')
    cards = stack.locator('[data-testid="past-recording-card"]')
    expect(cards).to_have_count(2)
    expect(cards.nth(1)).to_have_css("margin-top", "16px")
    assert _vertical_spacing(cards) == pytest.approx(16)
    expect(stack.locator(f'a[href="{standalone.get_absolute_url()}"]').first).to_be_visible()
    expect(stack.locator(f'a[href="{workshop.get_absolute_url()}"]').first).to_be_visible()
    standalone_action = stack.locator(
        f'a[data-testid="past-card-recording-cta"][href="{standalone.get_absolute_url()}"]'
    )
    standalone_action.click()
    page.wait_for_url(f"**{standalone.get_absolute_url()}")
    page.go_back(wait_until="domcontentloaded")
    workshop_action = page.locator(
        f'a[data-testid="past-card-recording-cta"][href="/workshops/{workshop.url_key}/video"]'
    )
    expect(workshop_action).to_be_visible()
    workshop_action.click()
    page.wait_for_url(f"**/workshops/{workshop.url_key}/video")


@pytest.mark.django_db(transaction=True)
def test_past_detail_closure_is_truthful_for_plain_gated_recap_and_workshop_states(django_server, browser, page):
    from content.models import Workshop

    _clear_events()
    ensure_tiers()
    now = timezone.now()
    plain = _event("plain-ended-event-1232", start=now - timedelta(days=5), status="completed")
    gated = _event(
        "gated-recording-event-1232",
        start=now - timedelta(days=4),
        status="completed",
        required_level=20,
        recording_s3_url="https://protected.example.com/hidden.mp4",
    )
    recap = _event(
        "recapped-event-1232",
        start=now - timedelta(days=3),
        status="completed",
        recap_html="<h2>Recap 1232</h2>",
    )
    workshop_event = _event(
        "workshop-event-1232",
        start=now - timedelta(days=2),
        status="completed",
        kind="workshop",
    )
    workshop = Workshop.objects.create(
        slug="workshop-handoff-1232",
        title="Workshop Handoff 1232",
        date=now.date(),
        status="published",
        event=workshop_event,
    )
    closure = "This event has ended. No recording is available."

    page.goto(f"{django_server}{plain.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.get_by_text(closure, exact=True)).to_be_visible()
    expect(page.locator('[data-testid="event-status-pill"]')).to_contain_text("Past")

    create_user("free-polish-1232@example.com", tier_slug="free")
    context = auth_context(browser, "free-polish-1232@example.com")
    member_page = context.new_page()
    member_page.goto(f"{django_server}{gated.get_absolute_url()}", wait_until="domcontentloaded")
    expect(member_page.get_by_text(closure, exact=True)).to_have_count(0)
    assert "https://protected.example.com/hidden.mp4" not in member_page.content()

    page.goto(f"{django_server}{recap.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.get_by_text("Recap 1232", exact=True)).to_be_visible()
    expect(page.get_by_text(closure, exact=True)).to_have_count(0)

    page.goto(
        f"{django_server}{workshop_event.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_text(closure, exact=True)).to_have_count(0)
    expect(page.locator('[data-testid="event-workshop-writeup-link"]')).to_have_attribute(
        "href", workshop.get_absolute_url()
    )


@pytest.mark.visual_regression
@pytest.mark.django_db(transaction=True)
def test_past_status_badge_uses_neutral_rendered_colors(django_server, page):
    _clear_events()
    past = _event(
        "neutral-past-badge-1232",
        start=timezone.now() - timedelta(days=2),
        status="completed",
    )
    page.goto(f"{django_server}{past.get_absolute_url()}", wait_until="domcontentloaded")

    badge = page.locator('[data-testid="event-status-pill"]')
    expect(badge).to_be_visible()
    classes = badge.get_attribute("class") or ""
    assert "bg-secondary" in classes
    assert "text-muted-foreground" in classes
    assert "green" not in classes
