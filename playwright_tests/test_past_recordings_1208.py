import datetime
import os
import re

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _past_start(days_ago):
    event_date = datetime.date.today() - datetime.timedelta(days=days_ago)
    return timezone.make_aware(
        datetime.datetime.combine(event_date, datetime.time(12, 0))
    )


def _clear_events():
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_standalone_event(
    title,
    slug,
    *,
    days_ago=30,
    recording_url="",
    recording_s3_url="",
    recording_embed_url="",
    required_level=0,
    tags=None,
):
    from events.models import Event

    start = _past_start(days_ago)
    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        status="completed",
        published=True,
        recording_url=recording_url,
        recording_s3_url=recording_s3_url,
        recording_embed_url=recording_embed_url,
        required_level=required_level,
        tags=tags or [],
    )
    connection.close()
    return event


def _create_workshop_event(
    title,
    slug,
    *,
    days_ago=30,
    recording_url="",
    recording_s3_url="",
    recording_embed_url="",
    recording_required_level=0,
    event_required_level=0,
    tags=None,
):
    from content.models import Workshop, WorkshopPage

    event = _create_standalone_event(
        title,
        slug,
        days_ago=days_ago,
        recording_url=recording_url,
        recording_s3_url=recording_s3_url,
        recording_embed_url=recording_embed_url,
        required_level=event_required_level,
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
        recording_required_level=recording_required_level,
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


def _card(page, title):
    return page.locator(f'[data-testid="past-recording-card"]:has-text("{title}")')


@pytest.mark.django_db(transaction=True)
def test_past_recordings_list_ctas_gating_and_s3_safety(django_server, browser):
    _clear_events()
    _create_user("basic-1208@test.com", tier_slug="basic")

    standalone = _create_standalone_event(
        "Standalone Past Recording",
        "standalone-past-recording",
        recording_url="https://youtube.com/watch?v=standalone1208",
        tags=["standalone"],
    )
    _create_workshop_event(
        "Linked Workshop Recording",
        "linked-workshop-recording",
        recording_url="https://youtube.com/watch?v=linked1208",
        tags=["workshops"],
    )
    s3_event, _ = _create_workshop_event(
        "S3 Only Workshop Recording",
        "s3-only-workshop-recording",
        recording_s3_url=(
            "https://private-recordings.s3.amazonaws.com/events/secret.mp4"
            "?X-Amz-Signature=abc123"
        ),
        tags=["agents"],
    )
    _create_workshop_event(
        "Main Gated Workshop Recording",
        "main-gated-workshop-recording",
        recording_url="https://youtube.com/watch?v=gated1208",
        recording_required_level=20,
        event_required_level=30,
        tags=["gated"],
    )
    _create_standalone_event(
        "No Recording Past Event",
        "no-recording-past-event",
    )

    context = _auth_context(browser, "basic-1208@test.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="past-recording-card"]')).to_have_count(4)
        expect(page.locator("body")).not_to_contain_text("No Recording Past Event")

        standalone_card = _card(page, "Standalone Past Recording")
        expect(standalone_card).to_be_visible()
        standalone_cta = standalone_card.locator(
            '[data-testid="past-card-recording-cta"]'
        )
        href = standalone_cta.get_attribute("href") or ""
        assert re.search(r"/events/\d+/standalone-past-recording$", href)
        standalone_cta.click()
        expect(page).to_have_url(
            re.compile(rf".*{standalone.get_absolute_url()}$")
        )

        page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
        linked_card = _card(page, "Linked Workshop Recording")
        expect(linked_card.locator('[data-testid="past-card-workshop-badge"]')).to_be_visible()
        expect(
            linked_card.locator('[data-testid="past-card-workshop-link"]')
        ).to_have_attribute("href", "/workshops/linked-workshop-recording")
        linked_card.locator('[data-testid="past-card-recording-cta"]').click()
        expect(page).to_have_url(
            re.compile(r".*/workshops/linked-workshop-recording/video$")
        )

        page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
        html = page.content()
        assert "amazonaws.com" not in html
        assert "X-Amz-Signature" not in html
        s3_card = _card(page, "S3 Only Workshop Recording")
        expect(s3_card).to_be_visible()
        s3_card.locator('[data-testid="past-card-recording-cta"]').click()
        expect(page).to_have_url(
            re.compile(r".*/workshops/s3-only-workshop-recording/video$")
        )
        video_html = page.content()
        assert (
            f"/events/{s3_event.id}/s3-only-workshop-recording/recording.mp4"
            in video_html
        )
        assert "amazonaws.com" not in video_html
        assert "X-Amz-Signature" not in video_html

        page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
        gated_card = _card(page, "Main Gated Workshop Recording")
        expect(gated_card.locator('[data-testid="past-card-recording-tier"]')).to_contain_text(
            "Main or above"
        )
        expect(gated_card.locator('[data-testid="past-card-recording-tier"]')).not_to_contain_text(
            "Premium"
        )
        gated_card.locator('[data-testid="past-card-recording-cta"]').click()
        expect(page).to_have_url(
            re.compile(r".*/workshops/main-gated-workshop-recording/video$")
        )
        expect(page.locator('[data-testid="video-paywall"]')).to_be_visible()
        expect(page.locator('[data-testid="video-paywall"]')).to_contain_text(
            "Upgrade to Main to watch the recording"
        )
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_past_recordings_tag_filter_includes_s3_only_workshops(django_server, page):
    _clear_events()
    _create_workshop_event(
        "Agents S3 Workshop",
        "agents-s3-workshop",
        recording_s3_url="https://storage.example.test/agents.mp4",
        tags=["agents"],
    )
    _create_standalone_event(
        "Python Standalone Recording",
        "python-standalone-recording",
        recording_url="https://youtube.com/watch?v=python1208",
        tags=["python"],
    )
    _create_standalone_event(
        "Agents Event Without Recording",
        "agents-event-without-recording",
        tags=["agents"],
    )

    page.goto(f"{django_server}/events?filter=past&tag=agents", wait_until="domcontentloaded")

    expect(_card(page, "Agents S3 Workshop")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Python Standalone Recording")
    expect(page.locator("body")).not_to_contain_text("Agents Event Without Recording")
    assert "amazonaws.com" not in page.content()

    page.locator('a:has-text("Clear")').click()
    expect(page).to_have_url(re.compile(r".*/events\?filter=past$"))
    expect(_card(page, "Agents S3 Workshop")).to_be_visible()
    expect(_card(page, "Python Standalone Recording")).to_be_visible()
