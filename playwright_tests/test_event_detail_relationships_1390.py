"""Browser journeys for public Event relationship backlinks (#1390)."""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = pytest.mark.local_only


def _clear_relationship_fixtures():
    from bookclub.models import Book
    from events.models import Event, EventSeries
    from plans.models import Sprint

    Book.objects.filter(slug__contains='relationship-1390').delete()
    Sprint.objects.filter(slug__contains='relationship-1390').delete()
    Event.objects.filter(slug__contains='relationship-1390').delete()
    EventSeries.objects.filter(slug__contains='relationship-1390').delete()


def _create_series(*, slug='relationship-1390-series', is_active=True):
    from events.models import EventSeries

    return EventSeries.objects.create(
        name='AI Builders Series',
        slug=slug,
        cadence='none',
        day_of_week=None,
        start_time=None,
        timezone='Europe/Berlin',
        required_level=0,
        is_active=is_active,
    )


def _create_event(*, series=None, slug='relationship-1390-session'):
    from events.models import Event

    start = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        title='Build in public session',
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='upcoming',
        required_level=0,
        event_series=series,
    )


def _create_book(series, *, title='Reliable Machine Learning'):
    from bookclub.models import Book

    return Book.objects.create(
        title=title,
        slug=f'{title.lower().replace(" ", "-")}-relationship-1390',
        author='Test Author',
        status='upcoming',
        required_level=0,
        start_date=timezone.localdate(),
        event_series=series,
    )


def _create_sprint(series, *, name='August Shipping Sprint'):
    from plans.models import Sprint

    return Sprint.objects.create(
        name=name,
        slug=f'{name.lower().replace(" ", "-")}-relationship-1390',
        start_date=timezone.localdate(),
        duration_weeks=6,
        status='active',
        min_tier_level=0,
        event_series=series,
    )


def _seed_public_relationships():
    _clear_relationship_fixtures()
    series = _create_series()
    event = _create_event(series=series)
    book = _create_book(series)
    sprint = _create_sprint(series)
    connection.close()
    return event, series, book, sprint


@pytest.mark.django_db(transaction=True)
def test_visitor_can_keyboard_open_each_canonical_relationship(
    django_server, browser,
):
    event, series, book, sprint = _seed_public_relationships()
    relationships = [
        (f'Event series · {series.name}', series.get_absolute_url(), series.name),
        (f'Book Club · {book.title}', book.get_absolute_url(), book.title),
        (
            f'Community Sprint · {sprint.name}',
            sprint.get_absolute_url(),
            sprint.name,
        ),
    ]

    context = browser.new_context()
    try:
        page = context.new_page()
        for accessible_name, destination, heading in relationships:
            page.goto(
                f'{django_server}{event.get_absolute_url()}',
                wait_until='domcontentloaded',
            )
            link = page.get_by_role('link', name=accessible_name, exact=True)
            expect(link).to_be_visible()
            expect(link).to_have_attribute('href', destination)
            link.focus()
            expect(link).to_be_focused()
            page.keyboard.press('Enter')
            page.wait_for_url(re.compile(rf'.*{re.escape(destination)}$'))
            expect(
                page.get_by_role('heading', name=heading, exact=True).first,
            ).to_be_visible()
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_hidden_unrelated_and_standalone_relationships_are_absent(
    django_server, browser,
):
    event, series, _book, _sprint = _seed_public_relationships()
    from bookclub.models import Book
    from plans.models import Sprint

    hidden_names = [
        'Hidden Draft Book relationship-1390',
        'Hidden Cancelled Book relationship-1390',
        'Hidden Draft Sprint relationship-1390',
        'Hidden Cancelled Sprint relationship-1390',
        'Unrelated Book relationship-1390',
        'Unrelated Sprint relationship-1390',
    ]
    for status, name in zip(('draft', 'cancelled'), hidden_names[:2]):
        Book.objects.create(
            title=name,
            slug=name.lower().replace(' ', '-'),
            author='Test Author',
            status=status,
            required_level=0,
            start_date=timezone.localdate(),
            event_series=series,
        )
    for status, name in zip(('draft', 'cancelled'), hidden_names[2:4]):
        Sprint.objects.create(
            name=name,
            slug=name.lower().replace(' ', '-'),
            start_date=timezone.localdate(),
            duration_weeks=6,
            status=status,
            min_tier_level=0,
            event_series=series,
        )
    unrelated_series = _create_series(slug='unrelated-relationship-1390-series')
    _create_event(
        series=unrelated_series, slug='unrelated-relationship-1390-session',
    )
    _create_book(unrelated_series, title=hidden_names[4])
    _create_sprint(unrelated_series, name=hidden_names[5])
    standalone = _create_event(slug='standalone-relationship-1390-session')
    connection.close()

    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        relationship_context = page.locator(
            '[data-testid="event-relationship-context"]',
        )
        expect(relationship_context).to_be_visible()
        for hidden_name in hidden_names:
            assert hidden_name not in relationship_context.inner_text()

        page.goto(
            f'{django_server}{standalone.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        assert page.locator(
            '[data-testid="event-relationship-context"]',
        ).count() == 0
    finally:
        context.close()
