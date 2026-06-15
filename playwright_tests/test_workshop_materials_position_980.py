"""Playwright E2E for issue #980: lift the Materials/links section higher
on workshop landing and recording pages so members notice it.

Reposition is template-only (the shared `_recording_materials.html`
fragment, the view context, and `Workshop.resolved_materials` are
unchanged). These tests assert the new DOM ordering relative to the rest
of the page body, plus the empty-safe / gated clean-render cases:

- Recording page `/workshops/<slug>/video`: Materials renders directly
  below the video player and ABOVE the transcript block.
- Landing page `/workshops/<slug>`: Materials renders directly below the
  description and ABOVE the Tutorial pages list and the actions card.
- No-materials workshop: neither page renders a Materials heading or an
  empty card.
- Below-tier visitor: Materials is suppressed and the paywall is the CTA.

DOM ordering is asserted with `Node.compareDocumentPosition` on the live
page (not HTML string-matching), per _docs/testing-guidelines.md.

Usage:
    uv run pytest playwright_tests/test_workshop_materials_position_980.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Local-only: DB seeding + session-cookie auth against the in-process
# runserver. Cannot run against a deployed environment.
pytestmark = pytest.mark.local_only

# Node.DOCUMENT_POSITION_FOLLOWING == 4: set on the result of
# a.compareDocumentPosition(b) when b FOLLOWS a in document order.
_FOLLOWS_JS = (
    "([a, b]) => {"
    " const x = document.querySelector(a);"
    " const y = document.querySelector(b);"
    " if (!x || !y) return null;"
    " return Boolean("
    "   x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING"
    " );"
    "}"
)


def _b_follows_a(page, a_selector, b_selector):
    """True iff b_selector appears AFTER a_selector in document order."""
    return page.evaluate(_FOLLOWS_JS, [a_selector, b_selector])


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(
    *,
    slug='ws980',
    materials,
    recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    transcript_text='This is a very long transcript. ' * 200,
    pages_required_level=0,
    recording_required_level=0,
    with_page=True,
):
    """Create a published workshop + linked event with a recording.

    The event carries a long transcript so the "Materials buried below the
    transcript" regression is meaningful. A tutorial page is added so the
    landing "Tutorial pages" list renders.
    """
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import (
        Instructor,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    from events.models import Event

    event = Event.objects.create(
        slug=f'{slug}-event',
        title='WS 980 Event',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url=recording_url,
        transcript_text=transcript_text,
        materials=[],
        published=True,
    )
    workshop = Workshop.objects.create(
        slug=slug,
        title='Materials Position Workshop',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=pages_required_level,
        recording_required_level=recording_required_level,
        description='Workshop description body.',
        materials=materials,
        event=event,
    )
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify('Alexey')[:200] or 'test-instructor',
        defaults={'name': 'Alexey', 'status': 'published'},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop, instructor=instructor,
        defaults={'position': 0},
    )
    if with_page:
        WorkshopPage.objects.create(
            workshop=workshop, slug='page-a', title='Page A',
            sort_order=1, body='Page A body',
        )
    connection.close()
    return workshop


SLIDES = {'title': 'Slides', 'url': 'https://example.com/slides.pdf'}


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestVideoMaterialsAboveTranscript:
    """Recording page: Materials sits under the player, above transcript."""

    def test_materials_above_transcript_and_below_player(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(slug='vid-mat', materials=[SLIDES])
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}/video',
            wait_until='domcontentloaded',
        )

        materials = page.locator('[data-testid="video-materials"]')
        transcript = page.locator('[data-testid="video-transcript"]')
        player = page.locator('[data-testid="video-player"]')
        assert materials.count() == 1
        assert transcript.count() == 1
        assert player.count() == 1

        # The Slides link is present, opens in a new tab, and is functional.
        slides_link = materials.get_by_role(
            'link', name='Slides', exact=True,
        )
        assert slides_link.count() == 1
        assert slides_link.get_attribute('href') == (
            'https://example.com/slides.pdf'
        )
        assert slides_link.get_attribute('target') == '_blank'
        assert slides_link.get_attribute('rel') == 'noopener noreferrer'

        # Player -> Materials -> transcript ordering in the DOM.
        assert _b_follows_a(
            page, '[data-testid="video-player"]',
            '[data-testid="video-materials"]',
        ) is True, 'Materials must come AFTER the player'
        assert _b_follows_a(
            page, '[data-testid="video-materials"]',
            '[data-testid="video-transcript"]',
        ) is True, 'Materials must come BEFORE the transcript'

        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestLandingMaterialsBelowDescription:
    """Landing page: Materials under the description, above pages + actions."""

    def test_materials_below_description_above_pages_and_actions(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(slug='land-mat', materials=[SLIDES])
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}',
            wait_until='domcontentloaded',
        )

        materials = page.locator('[data-testid="workshop-materials"]')
        assert materials.count() == 1
        assert page.locator(
            '[data-testid="workshop-description"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="workshop-pages-list"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="workshop-actions"]',
        ).count() == 1

        # The Slides link is present, points to the right URL, new tab.
        slides_link = materials.get_by_role(
            'link', name='Slides', exact=True,
        )
        assert slides_link.count() == 1
        assert slides_link.get_attribute('href') == (
            'https://example.com/slides.pdf'
        )
        assert slides_link.get_attribute('target') == '_blank'

        # description -> Materials -> pages list -> actions ordering.
        assert _b_follows_a(
            page, '[data-testid="workshop-description"]',
            '[data-testid="workshop-materials"]',
        ) is True, 'Materials must come AFTER the description'
        assert _b_follows_a(
            page, '[data-testid="workshop-materials"]',
            '[data-testid="workshop-pages-list"]',
        ) is True, 'Materials must come BEFORE the Tutorial pages list'
        assert _b_follows_a(
            page, '[data-testid="workshop-materials"]',
            '[data-testid="workshop-actions"]',
        ) is True, 'Materials must come BEFORE the actions card'

        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNoMaterialsRendersClean:
    """A workshop with no materials shows no heading and no empty card."""

    def test_landing_and_video_clean_without_materials(
        self, browser, django_server,
    ):
        _clear_workshops()
        workshop = _create_workshop(slug='no-mat', materials=[])
        _create_user('main@test.com', tier_slug='main')
        url_key = workshop.url_key

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()

        # Landing page: no materials testid, no Materials heading.
        page.goto(
            f'{django_server}/workshops/{url_key}',
            wait_until='domcontentloaded',
        )
        assert page.locator(
            '[data-testid="workshop-materials"]',
        ).count() == 0
        assert page.get_by_role(
            'heading', name='Materials', exact=True,
        ).count() == 0
        # The description still renders normally.
        assert page.locator(
            '[data-testid="workshop-description"]',
        ).count() == 1

        # Recording page: no materials, player + transcript still render.
        page.goto(
            f'{django_server}/workshops/{url_key}/video',
            wait_until='domcontentloaded',
        )
        assert page.locator(
            '[data-testid="video-materials"]',
        ).count() == 0
        assert page.get_by_role(
            'heading', name='Materials', exact=True,
        ).count() == 0
        assert page.locator('[data-testid="video-player"]').count() == 1
        assert page.locator(
            '[data-testid="video-transcript"]',
        ).count() == 1

        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMaterialsGateRespectedAfterMove:
    """Below-tier visitor: Materials hidden, paywall is the CTA."""

    def test_landing_below_pages_gate_hides_materials_shows_paywall(
        self, browser, django_server,
    ):
        _clear_workshops()
        # Pages gate is Basic (10); recording gate Main (20). An anonymous
        # visitor (level 0) clears neither, so Materials must be suppressed
        # and the pages paywall is the single CTA.
        workshop = _create_workshop(
            slug='gated-mat',
            materials=[SLIDES],
            pages_required_level=10,
            recording_required_level=20,
        )
        url_key = workshop.url_key

        # Anonymous context (no auth).
        from playwright_tests.conftest import VIEWPORT
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/{url_key}',
            wait_until='domcontentloaded',
        )

        assert page.locator(
            '[data-testid="workshop-materials"]',
        ).count() == 0
        assert page.get_by_role(
            'heading', name='Materials', exact=True,
        ).count() == 0
        # The pages paywall is the visible CTA.
        assert page.locator(
            '[data-testid="workshop-pages-paywall"]',
        ).count() == 1
        # And the slides URL never leaks into the page.
        assert 'https://example.com/slides.pdf' not in page.content()

        ctx.close()
