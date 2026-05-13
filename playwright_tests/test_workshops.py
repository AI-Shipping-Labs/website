"""Playwright E2E tests for the public Workshop surface (issue #296).

Covers the user-flow scenarios in the issue:
- Anonymous visitor browses the catalog and lands on a gated landing page.
- Free user hits the pages paywall on the landing.
- Basic user reads tutorial pages but sees the recording paywall.
- Basic user navigates between tutorial pages with prev/next.
- Main user gets full access (recording embed + unlocked pages).
- Past events card switches link target to /workshops/<slug>.
- Sitemap includes workshop URLs.
- Draft workshop is not publicly accessible.

Usage:
    uv run pytest playwright_tests/test_workshops.py -v
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
    """Delete every Workshop, WorkshopPage, and Event so each scenario
    starts from a known state."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(
    slug='ws',
    title='Production Agents',
    landing=0,
    pages=10,
    recording=20,
    pages_data=None,
    with_event=True,
    recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    materials=None,
    code_repo_url='https://github.com/example/repo',
    description='Workshop description body.',
    instructor='Alexey',
    status='published',
    cover_image_url='',
    tags=None,
):
    """Create a workshop with optional linked event + pages."""
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage
    from events.models import Event

    event = None
    if with_event:
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=title,
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            recording_url=recording_url,
            materials=materials or [],
            published=True,
        )

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        code_repo_url=code_repo_url,
        cover_image_url=cover_image_url,
        tags=tags or [],
        event=event,
    )
    if instructor:
        instructor_obj, _ = Instructor.objects.get_or_create(
            instructor_id=slugify(instructor)[:200] or 'test-instructor',
            defaults={
                'name': instructor,
                'status': 'published',
            },
        )
        WorkshopInstructor.objects.get_or_create(
            workshop=workshop,
            instructor=instructor_obj,
            defaults={'position': 0},
        )

    pages_data = pages_data or [
        ('intro', 'Introduction', '# Welcome\n\nThis is the intro.'),
        ('setup', 'Setup', '## Step 1\n\nInstall dependencies.'),
        ('deploy', 'Deploy', '## Final step\n\nShip it.'),
    ]
    for i, (s, t, body) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=s, title=t,
            sort_order=i, body=body,
        )

    connection.close()
    return workshop


# ----------------------------------------------------------------------
# Scenario 1: Anonymous visitor discovers the catalog and the gated
# landing page (no SEO body fully behind a wall — title still visible).
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestVisitorBrowsesCatalog:
    def test_visitor_sees_catalog_and_lands_on_paywalled_landing(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()
        _create_workshop(
            slug='visual-workshop',
            title='Visual Systems',
            pages=0,
            recording=0,
            with_event=False,
            cover_image_url='https://example.com/workshop-cover.jpg',
        )

        page.goto(f'{django_server}/workshops', wait_until='domcontentloaded')
        body = page.content()

        # Catalog renders the workshop card with title, instructor, date,
        # tier badge. Issue #481: badge reads "Basic or above" not "Basic+".
        assert 'Production Agents' in body
        assert 'data-testid="workshop-tier-badge"' in body
        assert 'Basic or above' in body
        assert 'Basic+' not in body

        production_card = page.locator(
            'article:has(a[href="/workshops/ws"])',
        )
        production_fallback = production_card.locator(
            '[data-testid="workshop-card-preview-fallback"]',
        )
        assert production_fallback.count() == 1
        assert 'Production Agents' not in production_fallback.inner_text()
        assert 'Alexey' not in production_fallback.inner_text()
        assert 'Apr 21, 2026' not in production_fallback.inner_text()
        assert production_card.locator('.h-12.w-12').count() == 0

        visual_image = page.locator(
            'img[src="https://example.com/workshop-cover.jpg"]',
        )
        assert visual_image.count() == 1
        assert visual_image.get_attribute("alt") == (
            "Cover image for Visual Systems"
        )
        assert visual_image.get_attribute("loading") == "lazy"

        # Click the workshop card to land on the landing page.
        page.locator('a:has-text("Production Agents")').first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/workshops/ws' in page.url
        body = page.content()

        # Issue #618: title still SEO-indexable, but the wholesale
        # pages paywall card is gone — the locked tutorial card lives
        # in the right pane. The cover-image hero is also retired.
        assert 'data-testid="workshop-title"' in body
        assert 'data-testid="workshop-tutorial-locked"' in body
        assert 'Upgrade to Basic' in body
        # Locked tutorial card has a "View Pricing" CTA pointing at /pricing.
        upgrade_cta = page.locator(
            '[data-testid="workshop-tutorial-locked-cta"]',
        )
        assert upgrade_cta.get_attribute('href') == '/pricing'

    def test_mobile_catalog_cards_do_not_duplicate_metadata_or_overflow(
        self, django_server, page, tmp_path,
    ):
        _clear_workshops()
        title = (
            'Building Reliable AI Agent Workshops with Retrieval, '
            'Evaluation, and Deployment'
        )
        _create_workshop(
            slug='mobile-workshop',
            title=title,
            instructor='Alexey Grigorev with a Long Instructor Label',
            tags=[
                'ai-agents',
                'retrieval-augmented-generation',
                'python',
                'deployment',
            ],
            with_event=True,
        )

        page.set_viewport_size({'width': 320, 'height': 844})
        page.goto(f'{django_server}/workshops', wait_until='domcontentloaded')

        assert page.evaluate(
            '() => document.documentElement.scrollWidth <= '
            'document.documentElement.clientWidth',
        )
        card = page.locator('article:has(a[href="/workshops/mobile-workshop"])')
        fallback = card.locator('[data-testid="workshop-card-preview-fallback"]')
        assert fallback.count() == 1
        assert title not in fallback.inner_text()
        assert 'Alexey Grigorev' not in fallback.inner_text()
        assert 'retrieval-augmented-generation' not in fallback.inner_text()

        card.locator('a').first.screenshot(
            path=str(tmp_path / 'issue-480-workshops-mobile-card.png'),
        )
        card.locator('a').first.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/workshops/mobile-workshop' in page.url

        # Issue #618: detail page no longer renders the cover-image hero.
        # The page must still fit horizontally on a 320px viewport.
        assert page.evaluate(
            '() => document.documentElement.scrollWidth <= '
            'document.documentElement.clientWidth',
        )
        page.locator('main').screenshot(
            path=str(tmp_path / 'issue-480-workshops-mobile-detail.png'),
        )


# ----------------------------------------------------------------------
# Scenario 2: Basic user — pages unlocked, recording locked.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestBasicUserReadsPagesButNotRecording:
    def test_basic_user_legacy_link_redirects_to_canonical_tutorial(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            pages_data=[
                (
                    'starting-notebook',
                    'Starting Notebook',
                    '# Starting notebook\n\nOpen the notebook.',
                ),
            ],
        )
        _create_user('basic@test.com', tier_slug='basic')

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        response = page.goto(
            f'{django_server}/workshops/ws/starting-notebook',
            wait_until='domcontentloaded',
        )

        assert response is not None and response.status == 200
        assert (
            page.url
            == f'{django_server}/workshops/ws/tutorial/starting-notebook'
        )
        body = page.content()
        assert 'Starting Notebook' in body
        assert 'data-testid="page-body"' in body
        assert 'Open the notebook.' in body

        ctx.close()

    def test_basic_user_sees_unlocked_pages_and_locked_recording(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop()
        _create_user('basic@test.com', tier_slug='basic')

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Issue #618: wholesale pages paywall is gone — Basic user
        # passes pages and reads the body inline. The locked recording
        # surfaces only via the discreet header link.
        assert 'data-testid="workshop-pages-paywall"' not in body
        assert 'data-testid="workshop-tutorial-locked"' not in body
        # Outline tutorial-page rows have NO 🔒 (tutorials are unlocked).
        assert 'data-testid="workshop-outline-page-lock"' not in body
        # Locked recording header link surfaces the upgrade target.
        assert 'data-testid="workshop-recording-locked-header-link"' in body
        # Code repo lives in the outline Materials section now.
        assert 'data-testid="workshop-outline-material-row"' in body

        # Click the first tutorial page row.
        page.locator(
            'a:has-text("Introduction")',
        ).first.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/workshops/ws/tutorial/intro' in page.url

        body = page.content()
        # Body renders, sidebar highlights current page.
        assert 'data-testid="page-body"' in body
        assert 'data-testid="sidebar-current-page"' in body
        assert 'data-testid="page-paywall"' not in body

        ctx.close()

    def test_basic_user_navigates_prev_next(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop()
        _create_user('basic@test.com', tier_slug='basic')

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        # First page: Next visible, Prev absent.
        page.goto(
            f'{django_server}/workshops/ws/tutorial/intro',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="page-next-btn"' in body
        assert 'data-testid="page-prev-btn"' not in body

        # Middle page: Both visible.
        page.goto(
            f'{django_server}/workshops/ws/tutorial/setup',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="page-next-btn"' in body
        assert 'data-testid="page-prev-btn"' in body

        # Last page: Prev visible, Next absent.
        page.goto(
            f'{django_server}/workshops/ws/tutorial/deploy',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="page-prev-btn"' in body
        assert 'data-testid="page-next-btn"' not in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: Main user — full access (recording embed renders).
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMainUserHasFullAccess:
    def test_main_user_sees_recording_and_pages(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            materials=[
                {
                    'title': 'Slides',
                    'url': 'https://example.com/slides.pdf',
                    'type': 'pdf',
                },
            ],
        )
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        # Issue #618: /workshops/<slug>/video 301-redirects to the new
        # course-player layout. Navigate directly to the new URL.
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Recording locked header link does NOT render (Main user has access).
        assert 'data-testid="workshop-recording-locked-header-link"' not in body
        # Player pane and JS module both render.
        assert 'data-testid="workshop-player-pane"' in body
        assert 'data-testid="workshop-player-script"' in body
        # Materials list lives in the outline now.
        assert 'data-testid="workshop-outline-materials"' in body
        assert 'Slides' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Past-events card redirects to workshop writeup.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestEventsPastCardLinksToWorkshop:
    def test_past_event_card_links_to_workshop(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='ws',
            title='Production Agents',
            landing=0,
            pages=0,
            recording=0,
        )

        page.goto(
            f'{django_server}/events?filter=past',
            wait_until='domcontentloaded',
        )
        body = page.content()

        assert 'data-testid="past-card-workshop-badge"' in body
        # The past card link points to /workshops/<slug>.
        link = page.locator('[data-testid="past-card-workshop-link"]').first
        assert link.get_attribute('href') == '/workshops/ws'


# ----------------------------------------------------------------------
# Scenario 5: Sitemap exposes published workshop landing + pages.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestWorkshopSitemap:
    def test_sitemap_lists_workshop_urls(self, django_server, page):
        _clear_workshops()
        _create_workshop(
            slug='ws-sitemap',
            title='Sitemap WS',
            pages_data=[('only-page', 'Only', 'body')],
        )

        page.goto(f'{django_server}/sitemap.xml')
        # response.text() doesn't exist on the page object — read content.
        body = page.content()
        assert '/workshops/ws-sitemap' in body
        assert '/workshops/ws-sitemap/tutorial/only-page' in body


# ----------------------------------------------------------------------
# Scenario 6: Action buttons render above the description and the
# tutorial pages list (issue #360).
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestPlayerShellLayout:
    def test_player_shell_renders_outline_and_tutorial_pane(
        self, browser, django_server,
    ):
        """Issue #618: the player-shell layout renders the outline (left)
        and the tutorial pane (right) for a Main user with access."""
        _clear_workshops()
        _create_workshop(
            slug='ws',
            landing=0,
            pages=0,
            recording=0,
            code_repo_url='https://github.com/example/repo',
        )
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws',
            wait_until='domcontentloaded',
        )

        # Title still renders.
        assert page.locator(
            '[data-testid="workshop-title"]',
        ).count() == 1

        # Two-pane shell renders.
        assert page.locator(
            '[data-testid="workshop-player-grid"]',
        ).count() == 1

        # Outline contains the tutorial pages section and the materials
        # section (which holds the Code repository row).
        outline = page.locator('[data-testid="workshop-outline"]')
        assert outline.count() == 1
        assert outline.locator(
            '[data-testid="workshop-outline-tutorial-pages"]',
        ).count() == 1
        assert outline.locator(
            '[data-testid="workshop-outline-materials"]',
        ).count() == 1
        assert outline.locator(
            '[data-testid="workshop-outline-material-row"]',
        ).count() >= 1

        # Tutorial pane renders the active page body.
        tutorial_pane = page.locator(
            '[data-testid="workshop-tutorial-pane"]',
        )
        assert tutorial_pane.count() == 1

        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestWorkshopWithoutCodeRepoNoMaterialsRow:
    def test_no_code_repo_no_repo_row_in_outline(
        self, browser, django_server,
    ):
        """When the workshop has no code_repo_url and no event materials,
        the Materials section is omitted entirely (no empty container)."""
        _clear_workshops()
        _create_workshop(
            slug='ws-nogit',
            landing=0,
            pages=0,
            recording=0,
            code_repo_url='',
            with_event=False,
        )
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws-nogit',
            wait_until='domcontentloaded',
        )

        # No GitHub link anywhere in the outline.
        github_anchors = page.locator(
            '[data-testid="workshop-outline"] a[href*="github.com"]',
        ).count()
        assert github_anchors == 0

        # Materials section absent (no empty container).
        assert page.locator(
            '[data-testid="workshop-outline-materials"]',
        ).count() == 0

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Draft workshop is hidden everywhere.
# ----------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDraftWorkshopHidden:
    def test_draft_not_in_catalog_and_404_on_detail(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='draft-ws', title='Hidden Draft Workshop', status='draft',
        )

        page.goto(f'{django_server}/workshops')
        # 'Hidden' alone collides with Tailwind's `hidden` utility class —
        # use a workshop-specific phrase that wouldn't appear elsewhere.
        assert 'Hidden Draft Workshop' not in page.content()

        response = page.goto(f'{django_server}/workshops/draft-ws')
        assert response is not None and response.status == 404

        response = page.goto(
            f'{django_server}/workshops/draft-ws/tutorial/intro',
        )
        assert response is not None and response.status == 404
