"""Browser coverage for legacy recording redirects (issue #1381)."""

import datetime
import os
import re

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _clear_recordings():
    from content.models import Workshop
    from events.models import Event

    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _past_event(slug, *, required_level=0, recording_url=None):
    from events.models import Event

    start = timezone.now() - datetime.timedelta(days=7)
    event = Event.objects.create(
        title=slug.replace('-', ' ').title(),
        slug=slug,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1),
        status='completed',
        published=True,
        required_level=required_level,
        recording_url=(
            recording_url
            if recording_url is not None
            else f'https://video.example.test/{slug}'
        ),
    )
    connection.close()
    return event


def _published_workshop(event, *, slug, recording_required_level=0):
    from content.models import Workshop

    workshop = Workshop.objects.create(
        slug=slug,
        title=event.title,
        date=event.start_datetime.date(),
        status='published',
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=recording_required_level,
        event=event,
    )
    connection.close()
    return workshop


@pytest.mark.django_db(transaction=True)
def test_legacy_recording_landing_redirects_to_past_catalog(django_server, page):
    _clear_recordings()
    response = page.request.get(
        f'{django_server}/event-recordings?filter=upcoming&tag=agents'
        '&tag=python&page=2&utm_source=bookmark',
        max_redirects=0,
    )

    assert response.status == 301
    assert response.headers['location'] == (
        '/events?filter=past&tag=agents&tag=python&page=2&utm_source=bookmark'
    )

    page.goto(f"{django_server}{response.headers['location']}")
    expect(page).to_have_url(
        re.compile(r'.*/events\?filter=past&tag=agents&tag=python&page=2&utm_source=bookmark$')
    )
    expect(page.locator('body')).to_contain_text('Past event recordings')


@pytest.mark.django_db(transaction=True)
def test_legacy_standalone_redirect_is_direct_and_reaches_event(django_server, page):
    _clear_recordings()
    event = _past_event('standalone-legacy-recording')

    response = page.request.get(
        f'{django_server}/event-recordings/{event.slug}',
        max_redirects=0,
    )

    assert response.status == 301
    assert response.headers['location'] == event.get_absolute_url()
    page.goto(f'{django_server}/event-recordings/{event.slug}')
    expect(page).to_have_url(re.compile(rf'.*{re.escape(event.get_absolute_url())}$'))
    expect(page.locator('h1')).to_contain_text(event.title)


@pytest.mark.django_db(transaction=True)
def test_legacy_workshop_redirect_is_direct_to_video(django_server, page):
    _clear_recordings()
    event = _past_event('retired-event-slug')
    workshop = _published_workshop(event, slug='current-workshop-slug')

    response = page.request.get(
        f'{django_server}/event-recordings/{event.slug}?utm_source=search',
        max_redirects=0,
    )

    assert response.status == 301
    assert response.headers['location'] == (
        f'{workshop.get_absolute_url()}/video?utm_source=search'
    )
    page.goto(f'{django_server}/event-recordings/{event.slug}?utm_source=search')
    expect(page).to_have_url(
        re.compile(rf'.*/workshops/{workshop.slug}/video\?utm_source=search$')
    )
    expect(page.locator('h1')).to_contain_text(event.title)


@pytest.mark.django_db(transaction=True)
def test_legacy_redirect_keeps_gated_workshop_destination_authoritative(
    django_server,
    browser,
):
    _clear_recordings()
    _create_user('basic-legacy-1381@example.com', tier_slug='basic')
    raw_recording_url = 'https://private.example.test/gated-recording.mp4'
    event = _past_event(
        'gated-legacy-recording',
        recording_url=raw_recording_url,
    )
    workshop = _published_workshop(
        event,
        slug='gated-current-workshop',
        recording_required_level=20,
    )
    context = _auth_context(browser, 'basic-legacy-1381@example.com')
    page = context.new_page()
    try:
        response = page.goto(
            f'{django_server}/event-recordings/{event.slug}',
            wait_until='domcontentloaded',
        )

        assert response.status == 403
        expect(page).to_have_url(
            re.compile(rf'.*/workshops/{workshop.slug}/video$')
        )
        expect(page.locator('[data-testid="video-paywall"]')).to_be_visible()
        expect(page.locator('[data-testid="video-paywall"]')).to_contain_text(
            'Upgrade to Main to watch the recording'
        )
        assert raw_recording_url not in page.content()
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_unmapped_legacy_recording_is_a_genuine_404(django_server, page):
    _clear_recordings()

    response = page.request.get(
        f'{django_server}/event-recordings/does-not-exist',
        max_redirects=0,
    )

    assert response.status == 404
    assert 'location' not in response.headers
