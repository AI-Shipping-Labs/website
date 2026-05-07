"""Playwright E2E tests for issues #363 and #426: event detail is announcement-only.

The event detail page (``/events/<slug>``) is the announcement / handoff page
for an event; the canonical recording playback lives on the linked Workshop's
landing/video pages.

Issue #363 introduced the "Full workshop writeup" CTA on workshop-linked
events. Issue #426 retired the inline recording fallback entirely, so even
completed events without a linked workshop now render announcement-only — no
inline player, materials, transcript, chapters, or recording paywall.

Three scenarios:
1. Workshop-linked completed event hands off to the workshop and renders no
   recording UI.
2. Completed event with no linked workshop also renders no recording UI
   (announcement-only) and has no workshop CTA.
3. Upcoming workshop-linked event suppresses recording UI but keeps the
   Register flow.

Usage:
    uv run pytest playwright_tests/test_event_workshop_handoff.py -v
"""

import datetime
import os
from datetime import timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402


def _clear_events_and_workshops():
    """Reset the events + workshops tables so each scenario is isolated."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    slug,
    title,
    description='Event announcement copy.',
    start_datetime=None,
    status='completed',
    kind='workshop',
    recording_url='',
    timestamps=None,
    core_tools=None,
    learning_objectives=None,
    outcome='',
    materials=None,
    required_level=0,
    published=True,
):
    """Create an Event row directly via ORM."""
    from events.models import Event
    if start_datetime is None:
        start_datetime = timezone.now() - timedelta(days=7)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description=description,
        start_datetime=start_datetime,
        status=status,
        kind=kind,
        recording_url=recording_url,
        timestamps=timestamps or [],
        core_tools=core_tools or [],
        learning_objectives=learning_objectives or [],
        outcome=outcome,
        materials=materials or [],
        required_level=required_level,
        published=published,
    )
    connection.close()
    return event


def _create_workshop_linked_to(
    event,
    *,
    slug,
    title,
    landing=0,
    pages=0,
    recording=0,
    description='Workshop writeup body.',
    code_repo_url='https://github.com/example/repo',
    status='published',
):
    """Create a Workshop row linked to ``event`` via the OneToOneField."""
    from content.models import Workshop, WorkshopPage
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        instructor_name='Alexey',
        code_repo_url=code_repo_url,
        event=event,
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='intro', title='Introduction',
        sort_order=1, body='# Welcome\n\nWorkshop intro.',
    )
    connection.close()
    return workshop


# ----------------------------------------------------------------------
# Scenario 1: completed workshop-linked event hands off to the workshop
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopLinkedEventHandsOff:
    """Member opens a completed workshop-linked event; recording UI is
    suppressed and the writeup CTA carries the page."""

    def test_completed_workshop_linked_event_hands_off_to_workshop(
        self, django_server, browser,
    ):
        _clear_events_and_workshops()
        _create_user('main@test.com', tier_slug='main')

        event = _create_event(
            slug='linked-event',
            title='Linked Workshop Event',
            description='Announcement-only copy on the event page.',
            status='completed',
            kind='workshop',
            recording_url='https://www.youtube.com/watch?v=LINKED',
            timestamps=[{'time_seconds': 0, 'label': 'Welcome'}],
            core_tools=['Cursor'],
            learning_objectives=['Build an MVP'],
            outcome='You will have shipped an MVP.',
            materials=[
                {'title': 'Slides', 'url': 'https://example.com/slides.pdf'},
            ],
            required_level=0,
        )
        _create_workshop_linked_to(
            event,
            slug='linked-workshop',
            title='Linked Workshop',
        )

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()

        # Step 1: Land on the event detail page.
        page.goto(
            f'{django_server}/events/linked-event',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # The event title and announcement description still render.
        assert 'Linked Workshop Event' in body
        assert 'Announcement-only copy on the event page.' in body

        # The "Full workshop writeup" CTA is the canonical hand-off.
        cta_panel = page.locator('[data-testid="event-workshop-writeup"]')
        assert cta_panel.count() == 1
        cta_link = page.locator(
            '[data-testid="event-workshop-writeup-link"]'
        )
        assert cta_link.count() == 1
        assert cta_link.first.get_attribute('href') == (
            '/workshops/linked-workshop'
        )

        # Recording UI must NOT render: no inline recording wrapper, no
        # video iframe in the main column, no recording-only headings.
        main = page.locator('main')
        main_html = main.inner_html()
        assert 'data-testid="event-recording-block"' not in main_html
        assert '<iframe' not in main_html.lower()
        assert 'data-source="youtube"' not in main_html
        assert "What You'll Learn" not in main_html
        assert 'Expected Outcome' not in main_html
        assert 'Materials</h2>' not in main_html
        # Core Tools chips and the Cursor tag must both be absent.
        assert '>Core Tools<' not in main_html
        assert '>Cursor<' not in main_html

        # Step 2: Follow the writeup CTA.
        cta_link.first.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/workshops/linked-workshop' in page.url

        # Step 3: From the workshop landing, click "Watch the recording".
        watch = page.locator('a:has-text("Watch the recording")')
        assert watch.count() >= 1
        watch.first.click()
        page.wait_for_load_state('domcontentloaded')

        # The recording lives on the workshop video page, not the event.
        assert '/workshops/linked-workshop/video' in page.url
        video_html = page.locator('main').inner_html()
        assert (
            'data-source="youtube"' in video_html
            or '<iframe' in video_html.lower()
        )

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 2: completed event with no linked workshop is announcement-only
# (issue #426 retired the inline recording fallback).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestOrphanEventIsAnnouncementOnly:
    """Anonymous visitor opens a completed event with no linked Workshop.

    The event page is announcement-only: title, description, and tags
    render, but no inline recording UI. There is also no workshop writeup
    CTA, since no Workshop is linked.
    """

    def test_orphan_event_omits_inline_recording_ui(
        self, django_server, page,
    ):
        _clear_events_and_workshops()
        _create_event(
            slug='orphan-event',
            title='Orphan Past Event',
            description='An older session never promoted to a workshop.',
            status='completed',
            kind='standard',
            recording_url='https://www.youtube.com/watch?v=ORPHAN',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            core_tools=['ChatGPT'],
            learning_objectives=['Understand RAG'],
            outcome='You will know how RAG works.',
            materials=[
                {'title': 'Notes', 'url': 'https://example.com/notes.pdf'},
            ],
            required_level=0,
        )

        page.goto(
            f'{django_server}/events/orphan-event',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Announcement copy renders.
        assert 'Orphan Past Event' in body
        assert 'An older session never promoted to a workshop.' in body

        # No inline recording block, no video player, no recording-only
        # sections, no transcript, no materials.
        recording_block = page.locator(
            '[data-testid="event-recording-block"]'
        )
        assert recording_block.count() == 0

        main_html = page.locator('main').inner_html()
        assert 'data-source="youtube"' not in main_html
        assert '<iframe' not in main_html.lower()
        assert 'Core Tools' not in body
        assert 'ChatGPT' not in body
        assert "What You'll Learn" not in body
        assert 'Understand RAG' not in body
        assert 'Materials</h2>' not in main_html
        assert 'https://example.com/notes.pdf' not in body

        # No workshop writeup CTA — there is no workshop to link to.
        assert (
            page.locator(
                '[data-testid="event-workshop-writeup"]'
            ).count() == 0
        )


# ----------------------------------------------------------------------
# Scenario 3: upcoming workshop-linked event suppresses recording UI but
# keeps the Register flow.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUpcomingWorkshopLinkedEventRegisters:
    """Member visits an upcoming workshop-linked event before the
    recording exists and registers."""

    def test_upcoming_event_suppresses_recording_keeps_register(
        self, django_server, browser,
    ):
        _clear_events_and_workshops()
        _create_user('main@test.com', tier_slug='main')

        event = _create_event(
            slug='upcoming-linked',
            title='Upcoming Workshop Event',
            description='Coming up next week.',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            kind='workshop',
            recording_url='',  # no recording yet
            materials=[],
            required_level=0,
        )
        _create_workshop_linked_to(
            event,
            slug='upcoming-workshop',
            title='Upcoming Workshop',
        )

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()

        page.goto(
            f'{django_server}/events/upcoming-linked',
            wait_until='domcontentloaded',
        )

        # Register button is visible (upcoming + has_access).
        register_btn = page.locator('#register-btn')
        assert register_btn.count() == 1

        # Workshop writeup CTA is visible — even pre-event, the workshop
        # writeup is the destination for materials.
        cta_panel = page.locator(
            '[data-testid="event-workshop-writeup"]'
        )
        assert cta_panel.count() == 1

        # No video player and no Materials section — recording UI is
        # suppressed because the workshop is linked.
        main_html = page.locator('main').inner_html()
        assert '<iframe' not in main_html.lower()
        assert 'data-source="youtube"' not in main_html
        assert 'Materials</h2>' not in main_html
        assert 'data-testid="event-recording-block"' not in main_html

        # Step 2: Click Register.
        register_btn.click()

        # The JS calls fetch then window.location.reload(); wait for the
        # confirmation text to appear after the reload.
        page.wait_for_selector(
            'text="You\'re registered!"',
            timeout=10000,
        )

        body = page.content()
        assert "You're registered!" in body

        ctx.close()
