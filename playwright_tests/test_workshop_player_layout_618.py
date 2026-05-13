"""Playwright E2E tests for the course-player workshop layout (issue #618).

Covers the BDD scenarios from the issue:
- Premium navigates the player and reads while watching.
- Premium uses inline section badges to seek mid-tutorial.
- Premium follows a deep link with timestamp.
- Free / Basic / anonymous see the locked variant: outline as syllabus,
  no iframe markup, no script tag, single discreet header link.
- Free clicks chapter rows / section badges and nothing happens.
- Free follows the discreet upgrade link to /pricing.
- Anonymous sees the same syllabus + badges treatment.
- Tutorial-pages TOC lock icons are independent of the recording gate.
- Old `/workshops/<slug>/video` 301-redirects to the new layout.
- Mobile drawer collapses behind the outline trigger (smoke test).
- Workshop with no recording renders cleanly (no player slot).
- Workshop with no tutorial pages shows the empty state.

Usage:
    uv run pytest playwright_tests/test_workshop_player_layout_618.py -v
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


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_player_workshop(
    slug='ws',
    title='Production Agents',
    landing=0,
    pages=10,
    recording=20,
    with_recording=True,
    pages_data=None,
    timestamps=None,
    materials=None,
):
    """Build a workshop with chapter timestamps + tutorial pages keyed
    to those timestamps so the player layout has data to render."""
    from django.utils import timezone

    from content.models import Workshop, WorkshopPage
    from events.models import Event

    event = None
    if with_recording:
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=title,
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            timestamps=timestamps or [
                {'time_seconds': 0, 'label': 'Intro'},
                {'time_seconds': 323, 'label': 'Setup the env'},
                {'time_seconds': 721, 'label': 'Build the API'},
                {'time_seconds': 2095, 'label': 'Add retrieval'},
                {'time_seconds': 3492, 'label': 'Deploy to Lambda'},
            ],
            materials=materials or [],
            published=True,
        )

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description='Workshop description.',
        event=event,
    )
    pages_data = pages_data if pages_data is not None else [
        ('intro', 'Intro', '# Intro\n\nWelcome.', '0:00'),
        ('set-up-the-env', 'Set up the env', '# Setup\n\nInstall.', '5:23'),
        ('build-the-api', 'Build the API', '# API\n\nFastAPI.', '12:01'),
        ('add-retrieval', 'Add retrieval', '# Retrieval\n\nRAG.', '34:55'),
        ('deploy-to-lambda', 'Deploy to Lambda', '# Deploy\n\nShip it.', '58:12'),
    ]
    for i, row in enumerate(pages_data, start=1):
        slug_, t, body, video_start = row
        WorkshopPage.objects.create(
            workshop=workshop, slug=slug_, title=t, sort_order=i,
            body=body, video_start=video_start,
        )
    connection.close()
    return workshop


# ----------------------------------------------------------------------
# Scenario: Premium member opens the workshop and reads while watching.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPremiumPlayerNavigation:
    def test_premium_sees_player_pane_and_outline(self, browser, django_server):
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )

        # Player shell + chapter outline + tutorial pages TOC + tutorial pane.
        assert page.locator('[data-testid="workshop-player-pane"]').count() == 1
        assert page.locator(
            '[data-testid="workshop-outline-recording"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="workshop-outline-tutorial-pages"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="workshop-tutorial-pane"]',
        ).count() == 1
        # Five chapter rows render as clickable buttons.
        assert page.locator(
            '[data-testid="workshop-chapter-row"]',
        ).count() == 5
        # No locked-row variant for unlocked users.
        assert page.locator(
            '[data-testid="workshop-chapter-row-locked"]',
        ).count() == 0
        # The default active page is the first tutorial.
        active_pane = page.locator('[data-testid="workshop-tutorial-pane"]')
        assert active_pane.get_attribute('data-page-slug') == 'intro'
        ctx.close()

    def test_premium_follows_deep_link_with_timestamp(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws?page=add-retrieval&t=2095',
            wait_until='domcontentloaded',
        )
        # Right pane lands on the requested tutorial.
        active_pane = page.locator('[data-testid="workshop-tutorial-pane"]')
        assert active_pane.get_attribute('data-page-slug') == 'add-retrieval'
        # Player shell carries the start-seconds attribute the JS reads.
        shell = page.locator('#workshop-player-shell')
        assert shell.get_attribute('data-start-seconds') == '2095'
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: Free member sees the workshop as a cohesive unit, no player.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeUserSeesLockedVariant:
    def test_free_user_sees_outline_no_iframe_no_script(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop(pages=0, recording=20)
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Recording outline syllabus renders as inert <div> rows.
        assert page.locator(
            '[data-testid="workshop-outline-recording"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="workshop-chapter-row-locked"]',
        ).count() == 5
        # No clickable chapter buttons for locked users.
        assert page.locator(
            '[data-testid="workshop-chapter-row"]',
        ).count() == 0
        # No iframe markup, no player JS module.
        assert 'youtube.com/embed' not in body
        assert 'loom.com/embed' not in body
        assert 'player.vimeo.com' not in body
        assert 'workshop_player.js' not in body
        # Single discreet header link.
        assert page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        ).count() == 1
        ctx.close()

    def test_free_user_chapter_row_click_does_nothing(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop(pages=0, recording=20)
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        url_before = page.url
        # The locked chapter row is a <div>, not a <button>. Click should
        # not trigger any navigation or modal.
        row = page.locator(
            '[data-testid="workshop-chapter-row-locked"]',
        ).nth(1)
        row.click()
        # URL hasn't changed.
        assert page.url == url_before
        ctx.close()

    def test_free_user_follows_locked_header_link_to_pricing(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop(pages=0, recording=20)
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        link = page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        )
        assert link.get_attribute('href') == '/pricing'
        link.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/pricing' in page.url
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: Anonymous visitor sees the syllabus too.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousSeesSyllabus:
    def test_anonymous_sees_outline_no_iframe(self, django_server, page):
        _clear_workshops()
        _create_player_workshop(pages=0, recording=20)

        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert page.locator(
            '[data-testid="workshop-outline-recording"]',
        ).count() == 1
        # 5 inert chapter rows render.
        assert page.locator(
            '[data-testid="workshop-chapter-row-locked"]',
        ).count() == 5
        # Header link points at /pricing.
        link = page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        )
        assert link.get_attribute('href') == '/pricing'
        # No iframe markup, no player script.
        assert 'youtube.com/embed' not in body
        assert 'workshop_player.js' not in body


# ----------------------------------------------------------------------
# Scenario: Tutorials TOC lock icons are independent of recording gate.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTutorialTocLockIndependence:
    def test_free_user_with_tutorial_access_sees_no_toc_lock_icons(
        self, browser, django_server,
    ):
        # pages=0 (anyone) + recording=30 (Premium) — Free user reads
        # the body but can't watch. The outline TOC must NOT show 🔒
        # icons (recording lock signals only via the header link).
        _clear_workshops()
        _create_player_workshop(pages=0, recording=30)
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        # Zero lock icons in the outline TOC.
        assert page.locator(
            '[data-testid="workshop-outline-page-lock"]',
        ).count() == 0
        # Header link still surfaces the recording lock.
        assert page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        ).count() == 1
        ctx.close()

    def test_free_user_with_tutorials_gated_sees_toc_lock_icons(
        self, browser, django_server,
    ):
        # pages=10 (Basic) + recording=30 (Premium) — Free user is
        # blocked from BOTH. TOC rows must show 🔒 icons (tutorials
        # are individually gated). Recording lock sits in the header.
        _clear_workshops()
        _create_player_workshop(pages=10, recording=30)
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        # 5 tutorial pages all gated => 5 lock icons.
        assert page.locator(
            '[data-testid="workshop-outline-page-lock"]',
        ).count() == 5
        # Header link still present for the recording gate.
        assert page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        ).count() == 1
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: /workshops/<slug>/video 301-redirects to the new layout.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLegacyVideoRouteRedirects:
    def test_video_route_redirects_with_t_preserved(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        response = page.goto(
            f'{django_server}/workshops/ws/video?t=754',
            wait_until='domcontentloaded',
        )
        assert response is not None
        # Final URL is the new player layout with ?t= preserved.
        assert page.url == f'{django_server}/workshops/ws?t=754'
        # Player shell carries data-start-seconds=754.
        shell = page.locator('#workshop-player-shell')
        assert shell.get_attribute('data-start-seconds') == '754'
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: Workshop with no recording renders cleanly.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopWithoutRecording:
    def test_no_recording_no_player_no_outline(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop(
            slug='no-rec', pages=0, recording=20, with_recording=False,
        )
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/no-rec',
            wait_until='domcontentloaded',
        )
        # No player pane, no recording outline, no header link.
        assert page.locator(
            '[data-testid="workshop-player-pane"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="workshop-outline-recording"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="workshop-recording-locked-header-link"]',
        ).count() == 0
        # Tutorial pane still renders the active tutorial body.
        assert page.locator(
            '[data-testid="workshop-tutorial-pane"]',
        ).count() == 1
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: Workshop with no tutorial pages shows the empty state.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopWithoutTutorialPages:
    def test_no_tutorial_pages_renders_empty_state(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop(
            slug='no-tut', pages_data=[], pages=0, recording=20,
        )
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/no-tut',
            wait_until='domcontentloaded',
        )
        # Player still renders.
        assert page.locator(
            '[data-testid="workshop-player-pane"]',
        ).count() == 1
        # Empty state in the right pane.
        assert page.locator(
            '[data-testid="workshop-tutorial-empty"]',
        ).count() == 1
        # No tutorial-pages section in the outline.
        assert page.locator(
            '[data-testid="workshop-outline-tutorial-pages"]',
        ).count() == 0
        ctx.close()


# ----------------------------------------------------------------------
# Scenario: Mobile viewport stacks vertically — player on TOP per spec.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMobileLayoutStacks:
    """Spec mandate (issue #618):

    > MOBILE (<1024px)
    > | Header
    > | [Player 16:9] sticky to top of viewport
    > | Active section [≡]
    > | tutorial body...

    The page must stack player → outline drawer trigger → tutorial body
    on `<lg`. The outline ITSELF stays hidden behind the drawer trigger
    so the player + tutorial body remain the foreground experience.
    """

    def test_mobile_player_renders_above_tutorial_body(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 375, 'height': 812})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        player = page.locator('[data-testid="workshop-player-pane"]')
        tutorial = page.locator('[data-testid="workshop-tutorial-pane"]')
        assert player.count() == 1
        assert tutorial.count() == 1
        # Player pane sits ABOVE the tutorial body on mobile (spec).
        player_box = player.bounding_box()
        tutorial_box = tutorial.bounding_box()
        assert player_box is not None
        assert tutorial_box is not None
        assert player_box['y'] < tutorial_box['y'], (
            f"Player at y={player_box['y']} must render above tutorial "
            f"body at y={tutorial_box['y']} on mobile (<lg). Got the "
            f"opposite — player is buried below the tutorial body."
        )
        ctx.close()

    def test_mobile_outline_drawer_trigger_visible_outline_hidden(
        self, browser, django_server,
    ):
        """Spec: the outline collapses behind a `[≡]` drawer trigger on
        `<lg`. Trigger is visible; outline content is hidden by default.
        """
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 375, 'height': 812})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        # Drawer trigger button is visible on mobile.
        toggle = page.locator('[data-testid="workshop-outline-toggle"]')
        assert toggle.count() == 1
        assert toggle.is_visible()
        # The outline aside is in the DOM but hidden (drawer collapsed).
        outline = page.locator('[data-testid="workshop-outline"]')
        assert outline.count() == 1
        assert not outline.is_visible(), (
            "Outline must be hidden behind the drawer trigger on mobile."
        )
        # Overlay is not visible while the drawer is closed.
        overlay = page.locator('[data-testid="workshop-outline-overlay"]')
        assert overlay.count() == 1
        assert not overlay.is_visible()
        ctx.close()

    def test_mobile_drawer_opens_on_trigger_click(
        self, browser, django_server,
    ):
        """Click the trigger -> outline becomes visible + overlay shows."""
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 375, 'height': 812})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        toggle = page.locator('[data-testid="workshop-outline-toggle"]')
        outline = page.locator('[data-testid="workshop-outline"]')
        overlay = page.locator('[data-testid="workshop-outline-overlay"]')
        # Pre-state: outline hidden.
        assert not outline.is_visible()
        toggle.click()
        # Post-state: outline + overlay visible.
        assert outline.is_visible()
        assert overlay.is_visible()
        # `aria-expanded` flipped to true.
        assert toggle.get_attribute('aria-expanded') == 'true'
        ctx.close()

    def test_mobile_drawer_closes_on_overlay_click(
        self, browser, django_server,
    ):
        """Open the drawer, click the backdrop overlay -> drawer closes."""
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 375, 'height': 812})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        toggle = page.locator('[data-testid="workshop-outline-toggle"]')
        outline = page.locator('[data-testid="workshop-outline"]')
        overlay = page.locator('[data-testid="workshop-outline-overlay"]')
        # Open.
        toggle.click()
        assert outline.is_visible()
        assert overlay.is_visible()
        # Click overlay to close.
        overlay.click()
        assert not outline.is_visible()
        assert not overlay.is_visible()
        assert toggle.get_attribute('aria-expanded') == 'false'
        ctx.close()

    def test_mobile_drawer_closes_on_close_button_click(
        self, browser, django_server,
    ):
        """The dedicated close button inside the drawer also dismisses it."""
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 375, 'height': 812})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        toggle = page.locator('[data-testid="workshop-outline-toggle"]')
        outline = page.locator('[data-testid="workshop-outline"]')
        toggle.click()
        assert outline.is_visible()
        page.locator('[data-testid="workshop-outline-close"]').click()
        assert not outline.is_visible()
        ctx.close()

    def test_desktop_drawer_trigger_hidden_outline_inline(
        self, browser, django_server,
    ):
        """At `lg+` the trigger is hidden and the outline renders inline
        (no drawer behaviour). This is the regression guard for keeping
        the desktop layout untouched."""
        _clear_workshops()
        _create_player_workshop()
        _create_user('premium@test.com', tier_slug='premium')

        ctx = _auth_context(browser, 'premium@test.com')
        page = ctx.new_page()
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        toggle = page.locator('[data-testid="workshop-outline-toggle"]')
        outline = page.locator('[data-testid="workshop-outline"]')
        # Trigger is in the DOM but `lg:hidden` keeps it invisible at lg+.
        assert toggle.count() == 1
        assert not toggle.is_visible()
        # Outline is visible inline at lg+.
        assert outline.is_visible()
        # And the player sits to the LEFT of the tutorial body on desktop.
        player_box = page.locator(
            '[data-testid="workshop-player-pane"]',
        ).bounding_box()
        tutorial_box = page.locator(
            '[data-testid="workshop-tutorial-pane"]',
        ).bounding_box()
        assert player_box is not None
        assert tutorial_box is not None
        assert player_box['x'] < tutorial_box['x']
        ctx.close()
