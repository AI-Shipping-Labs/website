import datetime
import os
import re

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _past_start(days_ago):
    event_date = datetime.date.today() - datetime.timedelta(days=days_ago)
    return timezone.make_aware(
        datetime.datetime.combine(event_date, datetime.time(12, 0))
    )


def _clear_recordings_data():
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_standalone_event(title, slug, *, days_ago=30, tags=None):
    from events.models import Event

    start = _past_start(days_ago)
    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        status="completed",
        published=True,
        recording_url=f"https://video.example.test/{slug}",
        tags=tags or [],
    )
    connection.close()
    return event


def _create_workshop_event(title, slug, *, days_ago=20, tags=None):
    from content.models import Workshop, WorkshopPage

    event = _create_standalone_event(
        title,
        slug,
        days_ago=days_ago,
        tags=tags,
    )
    event.kind = "workshop"
    event.save(update_fields=["kind", "updated_at"])
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        description=f"{title} workshop writeup.",
        date=event.start_datetime.date(),
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        event=event,
    )
    WorkshopPage.objects.create(
        workshop=workshop,
        slug="intro",
        title="Intro",
        sort_order=1,
        body="# Intro\n\nWorkshop intro.",
    )
    connection.close()
    return event, workshop


def _past_card(page, title):
    return page.locator(f'[data-testid="past-recording-card"]:has-text("{title}")')


def test_header_opens_past_recordings_and_workshop_handoff(
    django_server, page
):
    _clear_recordings_data()
    standalone = _create_standalone_event(
        "Homepage Standalone Recording",
        "homepage-standalone-recording",
        days_ago=5,
        tags=["python"],
    )
    _create_workshop_event(
        "Homepage Workshop Recording",
        "homepage-workshop-recording",
        days_ago=3,
        tags=["agents"],
    )

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    expect(page.get_by_test_id("home-past-recordings-section")).to_have_count(0)
    expect(page.locator(f'a[href="{standalone.get_absolute_url()}"]')).to_have_count(0)
    page.get_by_test_id("nav-community-trigger").hover()
    archive_link = page.get_by_test_id("nav-community-link-past-recordings")
    expect(archive_link).to_be_visible()
    expect(archive_link).to_have_attribute("href", "/events?filter=past")
    archive_link.click()
    expect(page).to_have_url(re.compile(r".*/events\?filter=past$"))
    expect(page.get_by_test_id("events-filter-past")).to_have_attribute(
        "aria-selected",
        "true",
    )
    expect(_past_card(page, "Homepage Standalone Recording")).to_be_visible()
    workshop_card = _past_card(page, "Homepage Workshop Recording")
    expect(workshop_card).to_be_visible()

    workshop_card.locator('[data-testid="past-card-workshop-link"]').click()
    expect(page).to_have_url(re.compile(r".*/workshops/homepage-workshop-recording$"))
    page.get_by_test_id("workshop-video-link").click()
    expect(page).to_have_url(
        re.compile(r".*/workshops/homepage-workshop-recording/video$")
    )
    expect(page.get_by_test_id("video-title")).to_contain_text(
        "Homepage Workshop Recording"
    )
