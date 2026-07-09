"""Playwright E2E tests for the public Workshop surface (issue #296).

Covers the user-flow scenarios in the issue:
- Anonymous visitor browses the catalog and lands on a gated landing page.
- Anonymous visitor filters the catalog by free/paid access.
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
from playwright_tests.conftest import (
    ensure_tiers,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


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
    workshop_materials=None,
    code_repo_url='https://github.com/example/repo',
    description='Workshop description body.',
    instructor='Alexey',
    status='published',
    cover_image_url='',
    tags=None,
):
    """Create a workshop with optional linked event + pages.

    ``materials`` populates ``Event.materials`` (legacy/recording side).
    ``workshop_materials`` (issue #646) populates the workshop-scoped
    ``Workshop.materials`` field so tests can exercise the unified
    rendering and gating rules.
    """
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage
    from events.models import Event

    event = None
    if with_event:
        # Backdate start_datetime so the events page's time-derived
        # past_filter (issue #713) classifies the event as past. The
        # filter requires either end_datetime <= now OR
        # (end_datetime is null AND start_datetime <= now - 1h).
        # No end_datetime is set here, so subtract 2 hours from now
        # to clear the 1h buffer.
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=title,
            start_datetime=timezone.now() - datetime.timedelta(hours=2),
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
        materials=workshop_materials or [],
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


@pytest.mark.django_db(transaction=True)
class TestVisitorBrowsesCatalog:
    @pytest.mark.core
    def test_anonymous_visitor_understands_offer_and_jumps_to_catalog(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='landing-ws',
            title='Shipping Agents',
            pages=0,
            recording=0,
            tags=['agents'],
        )
        _create_workshop(
            slug='second-landing-ws',
            title='Second Shipping Workshop',
            pages=0,
            recording=0,
            tags=['python'],
        )

        response = page.goto(
            f'{django_server}/workshops',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200

        landing = page.locator('[data-testid="workshops-landing"]')
        assert landing.is_visible()
        landing_text = landing.inner_text()
        assert 'Hands-on AI workshops' in landing_text
        assert 'Practical AI engineering sessions' in landing_text
        assert 'recording' in landing_text
        assert 'step-by-step writeups or tutorial pages' in landing_text
        assert 'runnable code or materials' in landing_text

        assert page.evaluate(
            """() => {
                const landing = document.querySelector(
                    '[data-testid="workshops-landing"]'
                );
                const card = document.querySelector(
                    '[data-testid="workshops-preview"] [data-testid="workshop-card"]'
                );
                return Boolean(
                    landing && card &&
                    (landing.compareDocumentPosition(card) &
                     Node.DOCUMENT_POSITION_FOLLOWING)
                );
            }""",
        )

        page.locator('[data-testid="browse-workshops-cta"]').click()
        page.wait_for_url('**/workshops/catalog')
        assert page.url.endswith('/workshops/catalog')
        assert page.locator('[data-testid="workshop-catalog"]').is_visible()
        assert page.locator('[data-testid="workshops-landing"]').count() == 0
        assert page.locator('[data-testid="workshop-access-filter-all"]').is_visible()
        assert page.locator('article:has(a[href="/workshops/landing-ws"])').is_visible()
        assert page.locator(
            'article:has(a[href="/workshops/second-landing-ws"])',
        ).is_visible()

        card = page.locator('article:has(a[href="/workshops/landing-ws"])')
        assert card.is_visible()
        card.locator('a').first.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/landing-ws')
        body = page.content()
        assert 'data-testid="workshop-title"' in body
        assert 'Shipping Agents' in body
        assert 'data-testid="workshop-pages-list"' in body

    def test_membership_options_cta_lands_on_pricing(
        self, django_server, page,
    ):
        _clear_workshops()
        ensure_tiers()
        _create_workshop()

        page.goto(f'{django_server}/workshops', wait_until='domcontentloaded')
        page.locator('[data-testid="view-membership-options-cta"]').click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/pricing')
        expected_tiers = {'free', 'basic', 'main', 'premium'}
        found_tiers = {
            slug
            for slug in expected_tiers
            if page.locator(f'[data-tier-card="{slug}"]').count() >= 1
        }
        assert found_tiers == expected_tiers

    def test_filtered_catalog_clear_path_stays_on_catalog_route(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='agents-ws',
            title='Agent Workshop',
            pages=0,
            recording=0,
            tags=['agents'],
        )
        _create_workshop(
            slug='python-ws',
            title='Python Workshop',
            pages=0,
            recording=0,
            tags=['python'],
        )

        page.goto(
            f'{django_server}/workshops/catalog?tag=agents',
            wait_until='domcontentloaded',
        )

        assert page.locator('[data-testid="workshops-landing"]').count() == 0
        assert page.locator('[data-testid="workshop-catalog"]').is_visible()
        assert page.locator('[data-testid="workshop-active-filters"]').is_visible()
        assert 'agents' in page.locator(
            '[data-testid="workshop-active-filters"]',
        ).inner_text()
        assert 'Agent Workshop' in page.content()
        assert 'Python Workshop' not in page.content()

        clear_link = page.locator('[data-testid="clear-workshop-filter"]')
        assert clear_link.get_attribute('href') == '/workshops/catalog'
        clear_link.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog')
        body = page.content()
        assert 'Agent Workshop' in body
        assert 'Python Workshop' in body

    @pytest.mark.core
    def test_visitor_filters_free_catalog_and_opens_matching_workshop(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='open-free-main-recording',
            title='Open Free Workshop',
            pages=0,
            recording=20,
            tags=['agents'],
        )
        _create_workshop(
            slug='registered-free',
            title='Registered Free Workshop',
            pages=5,
            recording=5,
            tags=['agents'],
        )
        _create_workshop(
            slug='paid-basic',
            title='Paid Basic Workshop',
            pages=10,
            recording=20,
            tags=['agents'],
        )
        _create_workshop(
            slug='draft-free',
            title='Draft Free Workshop',
            status='draft',
            pages=0,
            recording=0,
            tags=['agents'],
        )

        page.goto(
            f'{django_server}/workshops/catalog',
            wait_until='domcontentloaded',
        )

        all_filter = page.locator('[data-testid="workshop-access-filter-all"]')
        assert all_filter.get_attribute('aria-current') == 'page'
        body = page.content()
        assert 'Open Free Workshop' in body
        assert 'Registered Free Workshop' in body
        assert 'Paid Basic Workshop' in body
        assert 'Draft Free Workshop' not in body

        page.locator('[data-testid="workshop-access-filter-free"]').click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog?access=free')
        free_filter = page.locator('[data-testid="workshop-access-filter-free"]')
        assert free_filter.get_attribute('aria-current') == 'page'

        filtered_body = page.content()
        assert 'Open Free Workshop' in filtered_body
        assert 'Registered Free Workshop' in filtered_body
        assert 'Paid Basic Workshop' not in filtered_body
        assert 'Draft Free Workshop' not in filtered_body

        page.locator('a[href="/workshops/open-free-main-recording"]').first.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/open-free-main-recording')
        assert page.locator('[data-testid="workshop-title"]').inner_text() == (
            'Open Free Workshop'
        )
        recording_lock = page.locator('[data-testid="workshop-video-locked"]')
        assert recording_lock.is_visible()
        assert 'Main or above' in recording_lock.inner_text()

    def test_access_and_tag_filters_preserve_each_other(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='free-agents',
            title='Free Agents Workshop',
            pages=0,
            recording=0,
            tags=['agents'],
        )
        _create_workshop(
            slug='paid-agents',
            title='Paid Agents Workshop',
            pages=10,
            recording=20,
            tags=['agents'],
        )
        _create_workshop(
            slug='paid-python',
            title='Paid Python Workshop',
            pages=20,
            recording=20,
            tags=['python'],
        )

        page.goto(
            f'{django_server}/workshops/catalog?access=free&tag=agents',
            wait_until='domcontentloaded',
        )

        paid_filter = page.locator('[data-testid="workshop-access-filter-paid"]')
        assert paid_filter.get_attribute('href') == (
            '/workshops/catalog?access=paid&tag=agents'
        )
        paid_filter.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog?access=paid&tag=agents')
        paid_agents_body = page.content()
        assert 'Paid Agents Workshop' in paid_agents_body
        assert 'Free Agents Workshop' not in paid_agents_body
        assert 'Paid Python Workshop' not in paid_agents_body

        page.goto(
            f'{django_server}/workshops/catalog?access=paid',
            wait_until='domcontentloaded',
        )
        python_tag = page.locator(
            'article:has(a[href="/workshops/paid-python"]) '
            '[data-testid="workshop-card-tags"] a:has-text("python")',
        )
        assert python_tag.get_attribute('href') == (
            '/workshops/catalog?access=paid&tag=python'
        )
        python_tag.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog?access=paid&tag=python')
        python_body = page.content()
        assert 'Paid Python Workshop' in python_body
        assert 'Paid Agents Workshop' not in python_body
        assert page.locator('[data-testid="workshop-active-access"]').inner_text() == (
            'Paid'
        )

        active_tag = page.locator('[data-testid="workshop-active-tag"]')
        assert active_tag.get_attribute('href') == (
            '/workshops/catalog?access=paid'
        )
        active_tag.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog?access=paid')

        clear_link = page.locator('[data-testid="clear-workshop-filter"]')
        assert clear_link.get_attribute('href') == '/workshops/catalog'
        clear_link.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.endswith('/workshops/catalog')

    @pytest.mark.core
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

        page.goto(
            f'{django_server}/workshops/catalog',
            wait_until='domcontentloaded',
        )
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

        # Title is visible for SEO + a single paywall card.
        assert 'data-testid="workshop-title"' in body
        assert 'data-testid="workshop-pages-paywall"' in body
        assert 'Upgrade to Basic to access this workshop' in body
        assert 'data-testid="workshop-detail-preview"' not in body
        assert 'data-testid="workshop-detail-preview-fallback"' not in body

        # Pricing CTA goes to /pricing
        upgrade_cta = page.locator(
            '[data-testid="workshop-pages-upgrade-cta"]',
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

        body = page.content()
        assert 'data-testid="workshop-detail-preview"' not in body
        assert 'data-testid="workshop-detail-preview-fallback"' not in body
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


@pytest.mark.django_db(transaction=True)
class TestBasicUserReadsPagesButNotRecording:
    def test_basic_user_reads_canonical_tutorial_page(
        self, browser, django_server,
    ):
        """Basic user reads a tutorial via the canonical slug-only URL.

        Issue #1064 made ``/workshops/<slug>/tutorial/<page>`` canonical
        again. Valid dated deep links now 301 to the slug-only URL.
        """
        _clear_workshops()
        workshop = _create_workshop(
            pages_data=[
                (
                    'starting-notebook',
                    'Starting Notebook',
                    '# Starting notebook\n\nOpen the notebook.',
                ),
            ],
        )
        _create_user('basic@test.com', tier_slug='basic')

        canonical_url = (
            f'{django_server}/workshops/{workshop.slug}/'
            f'tutorial/starting-notebook'
        )
        dated_url = (
            f'{django_server}/workshops/2026-04-21-{workshop.slug}/'
            f'tutorial/starting-notebook'
        )

        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()

        # The canonical URL renders directly (no redirect).
        response = page.goto(canonical_url, wait_until='domcontentloaded')
        assert response is not None and response.status == 200
        assert page.url == canonical_url

        body = page.content()
        assert 'Starting Notebook' in body
        assert 'data-testid="page-body"' in body
        assert 'Open the notebook.' in body

        # Issue #1064: dated deep links redirect to the slug-only URL.
        dated_response = page.goto(
            dated_url, wait_until='domcontentloaded',
        )
        assert dated_response is not None
        assert dated_response.status == 200
        assert page.url == canonical_url
        chain = []
        current = dated_response.request
        while current is not None:
            chain.append(current)
            current = current.redirected_from
        statuses = [
            response.status
            for request in chain
            if (response := request.response()) is not None
        ]
        assert 301 in statuses

        ctx.close()

    @pytest.mark.core
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

        # Pages paywall must NOT render.
        assert 'data-testid="workshop-pages-paywall"' not in body
        # Page rows should NOT have lock icons.
        assert 'data-testid="workshop-page-lock-icon"' not in body
        # Video card surfaces the recording-tier lock.
        assert 'data-testid="workshop-video-locked"' in body
        # Code repo link is visible.
        assert 'data-testid="workshop-code-repo-link"' in body

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
        page.goto(
            f'{django_server}/workshops/ws/video',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Recording paywall does NOT render.
        assert 'data-testid="video-paywall"' not in body
        # Either the embedded YouTube iframe or the video_player tag rendered.
        assert (
            'data-testid="video-player"' in body
            or 'iframe' in body.lower()
        )
        # Materials list rendered.
        assert 'data-testid="video-materials"' in body
        assert 'Slides' in body

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Past-events card redirects to workshop writeup.
# ----------------------------------------------------------------------


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
# Scenario 6: Action buttons render below the README and tutorial pages list.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopActionsBelowTutorialPages:
    def test_action_buttons_render_below_description_and_pages_list(
        self, browser, django_server,
    ):
        """Visitor with access sees recording / GitHub actions below the
        README description and tutorial pages list, without a duplicate
        tutorial card."""
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

        # Title is rendered.
        assert page.locator(
            '[data-testid="workshop-title"]',
        ).count() == 1

        # Recording + GitHub actions are present; the old duplicate
        # tutorial CTA is intentionally absent because the page list is
        # the tutorial navigation.
        video_link = page.locator('[data-testid="workshop-video-link"]')
        tutorial_link = page.locator('[data-testid="workshop-tutorial-link"]')
        repo_link = page.locator('[data-testid="workshop-code-repo-link"]')
        description = page.locator('[data-testid="workshop-description"]')
        pages_list = page.locator('[data-testid="workshop-pages-list"]')

        assert video_link.count() == 1
        assert tutorial_link.count() == 0
        assert repo_link.count() == 1
        assert description.count() == 1
        assert pages_list.count() == 1

        # Compare DOM order via vertical position.
        video_box = video_link.bounding_box()
        repo_box = repo_link.bounding_box()
        description_box = description.bounding_box()
        pages_box = pages_list.bounding_box()

        assert video_box is not None
        assert repo_box is not None
        assert description_box is not None
        assert pages_box is not None

        # Description and tutorial page list are read before action cards.
        assert description_box['y'] < pages_box['y']
        assert pages_box['y'] < video_box['y']
        # The code link is grouped under the recording card.
        assert video_box['y'] < repo_box['y']

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestWorkshopWithoutCodeRepoNoEmptySlot:
    def test_no_code_repo_no_empty_button_row(
        self, browser, django_server,
    ):
        """When the workshop has no code_repo_url, the GitHub button
        is absent from the DOM (no empty container)."""
        _clear_workshops()
        _create_workshop(
            slug='ws-nogit',
            landing=0,
            pages=0,
            recording=0,
            code_repo_url='',
        )
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/ws-nogit',
            wait_until='domcontentloaded',
        )

        # GitHub button is not rendered at all.
        repo_link = page.locator('[data-testid="workshop-code-repo-link"]')
        assert repo_link.count() == 0

        # Video card still renders below the description, while the
        # duplicate tutorial card remains absent.
        video_link = page.locator('[data-testid="workshop-video-link"]')
        tutorial_link = page.locator('[data-testid="workshop-tutorial-link"]')
        description = page.locator('[data-testid="workshop-description"]')

        assert video_link.count() == 1
        assert tutorial_link.count() == 0
        assert description.count() == 1

        video_box = video_link.bounding_box()
        description_box = description.bounding_box()

        assert video_box is not None
        assert description_box is not None

        assert description_box['y'] < video_box['y']

        # No empty wrapper sits where the repo button used to be: the
        # template gates the entire `<div class="mb-12">…</div>` wrapper
        # behind `{% if workshop.code_repo_url %}`, so no element with
        # the repo testid exists, and there's no anchor pointing at
        # github.com from the action block.
        github_anchors = page.locator('a[href*="github.com"]').count()
        assert github_anchors == 0

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 7: Draft workshop is hidden everywhere.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDraftWorkshopHidden:
    def test_draft_not_in_catalog_and_404_on_detail(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='draft-ws', title='Hidden Draft Workshop', status='draft',
        )

        page.goto(f'{django_server}/workshops/catalog')
        # 'Hidden' alone collides with Tailwind's `hidden` utility class —
        # use a workshop-specific phrase that wouldn't appear elsewhere.
        assert 'Hidden Draft Workshop' not in page.content()

        response = page.goto(f'{django_server}/workshops/draft-ws')
        assert response is not None and response.status == 404

        response = page.goto(
            f'{django_server}/workshops/draft-ws/tutorial/intro',
        )
        assert response is not None and response.status == 404


# ----------------------------------------------------------------------
# Issue #646: Unified workshop/event materials.
# Workshop-level materials live on Workshop.materials (gated by pages
# level) and fall back to Event.materials (gated by recording level)
# when empty. The shared partial _recording_materials.html is the only
# renderer; the testid differs by page (workshop-materials on the
# landing, video-materials on the video page).
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopMaterialsUnification:
    @pytest.mark.core
    def test_reader_sees_materials_on_landing_without_recording(
        self, django_server, page,
    ):
        """Anonymous visitor sees workshop-level materials on a recording-less
        workshop landing page. The Materials section uses the
        ``workshop-materials`` testid (preserves the existing test
        contract for the landing renderer).
        """
        _clear_workshops()
        _create_workshop(
            slug='materials-only-workshop',
            title='Materials-Only Workshop',
            landing=0, pages=0, recording=0,
            with_event=False,
            workshop_materials=[
                {'title': 'Slides',
                 'url': 'https://example.com/slides.pdf'},
                {'title': 'Repo',
                 'url': 'https://github.com/example/repo',
                 'type': 'code'},
            ],
        )

        page.goto(
            f'{django_server}/workshops/materials-only-workshop',
            wait_until='domcontentloaded',
        )
        body = page.content()
        # Heading and both rows rendered via the shared partial.
        assert 'Materials</h2>' in body
        assert 'data-testid="workshop-materials"' in body
        assert 'Slides' in body
        assert 'Repo' in body
        # External-link links open in a new tab.
        slides_link = page.locator(
            'a:has-text("Slides")',
        ).first
        assert slides_link.get_attribute('href') == (
            'https://example.com/slides.pdf'
        )
        assert slides_link.get_attribute('target') == '_blank'

    @pytest.mark.core
    def test_reader_sees_materials_on_video_when_authorized(
        self, django_server, browser,
    ):
        """A user with main tier (clears recording=20) sees event-side
        materials on the workshop video page under the ``video-materials``
        testid."""
        _clear_workshops()
        _create_workshop(
            slug='recorded-workshop',
            title='Recorded Workshop',
            landing=0, pages=0, recording=20,
            with_event=True,
            materials=[
                {'title': 'Cheat sheet',
                 'url': 'https://example.com/cheat.pdf'},
            ],
        )
        _create_user('main@test.com', tier_slug='main')

        ctx = _auth_context(browser, 'main@test.com')
        p = ctx.new_page()
        p.goto(
            f'{django_server}/workshops/recorded-workshop/video',
            wait_until='domcontentloaded',
        )
        body = p.content()
        assert 'data-testid="video-player"' in body
        assert 'data-testid="video-materials"' in body
        assert 'Cheat sheet' in body
        assert 'https://example.com/cheat.pdf' in body
        ctx.close()

    @pytest.mark.core
    def test_workshop_materials_override_event_materials(
        self, django_server, page,
    ):
        """Workshop.materials shadow the linked event's materials on both
        the landing and the video page."""
        _clear_workshops()
        _create_workshop(
            slug='override-workshop',
            title='Override Workshop',
            landing=0, pages=0, recording=0,
            with_event=True,
            materials=[
                {'title': 'OLD',
                 'url': 'https://example.com/old'},
            ],
            workshop_materials=[
                {'title': 'NEW',
                 'url': 'https://example.com/new'},
            ],
        )

        page.goto(
            f'{django_server}/workshops/override-workshop',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'NEW' in body
        assert 'https://example.com/new' in body
        assert 'OLD' not in body
        assert 'https://example.com/old' not in body

        page.goto(
            f'{django_server}/workshops/override-workshop/video',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'NEW' in body
        assert 'OLD' not in body
        assert 'https://example.com/old' not in body

    @pytest.mark.core
    def test_free_visitor_paywalled_out_of_materials_behind_pages_gate(
        self, django_server, page,
    ):
        """When the pages gate trips, the Materials section must be
        suppressed entirely — the paywall card is the single CTA."""
        _clear_workshops()
        _create_workshop(
            slug='paid-pages-workshop',
            title='Paid Pages Workshop',
            landing=0, pages=10, recording=20,
            with_event=False,
            workshop_materials=[
                {'title': 'Locked',
                 'url': 'https://example.com/locked'},
            ],
        )

        page.goto(
            f'{django_server}/workshops/paid-pages-workshop',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="workshop-title"' in body
        assert 'data-testid="workshop-pages-paywall"' in body
        # Materials block (heading + testid + url) must NOT render.
        assert 'data-testid="workshop-materials"' not in body
        assert 'Materials</h2>' not in body
        assert 'https://example.com/locked' not in body

    @pytest.mark.core
    def test_materials_render_when_only_recording_is_paywalled(
        self, django_server, page,
    ):
        """A workshop with pages=0 and recording>0 lets anonymous visitors
        see workshop-level materials on the video page. The recording
        embed is replaced by a teaser; the workshop-level materials
        still render because they gate against the pages level."""
        _clear_workshops()
        _create_workshop(
            slug='recording-paywall-workshop',
            title='Recording Paywall Workshop',
            landing=0, pages=0, recording=20,
            with_event=True,
            workshop_materials=[
                {'title': 'Workbook',
                 'url': 'https://example.com/workbook'},
            ],
        )

        page.goto(
            f'{django_server}/workshops/recording-paywall-workshop/video',
            wait_until='domcontentloaded',
        )
        body = page.content()
        # Recording is paywalled (either a teaser or a bare paywall card).
        assert 'data-testid="video-player"' not in body
        # Materials section still shows.
        assert 'data-testid="video-materials"' in body
        assert 'Workbook' in body
        assert 'https://example.com/workbook' in body

    def test_no_materials_anywhere_shows_no_materials_section(
        self, django_server, page,
    ):
        """A bare workshop (no workshop-level materials, no event materials)
        renders without a Materials heading on landing or video."""
        _clear_workshops()
        _create_workshop(
            slug='bare-workshop',
            title='Bare Workshop',
            landing=0, pages=0, recording=0,
            with_event=False,
            workshop_materials=[],
        )

        page.goto(
            f'{django_server}/workshops/bare-workshop',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="workshop-materials"' not in body
        assert 'Materials</h2>' not in body

        page.goto(
            f'{django_server}/workshops/bare-workshop/video',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="video-materials"' not in body
        assert 'Materials</h2>' not in body

    def test_linked_event_detail_does_not_render_materials(
        self, django_server, page,
    ):
        """Issue #426 boundary: even when the workshop and the event both
        have materials, the event detail page surfaces only the workshop
        writeup CTA — no inline Materials block."""
        _clear_workshops()
        ws = _create_workshop(
            slug='linked-mat-workshop',
            title='Linked Materials Workshop',
            landing=0, pages=0, recording=0,
            with_event=True,
            materials=[
                {'title': 'EVENT-DOC',
                 'url': 'https://example.com/event-doc'},
            ],
            workshop_materials=[
                {'title': 'WS-DOC',
                 'url': 'https://example.com/ws-doc'},
            ],
        )

        # Issue #673: canonical URL is ``/events/<id>/<slug>``. The
        # helper creates an event linked to this workshop.
        page.goto(
            f'{django_server}{ws.event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="event-workshop-writeup"' in body
        assert '/workshops/linked-mat-workshop' in body
        # No event-level resource section or material URL on the detail page.
        assert 'data-testid="event-post-resources"' not in body
        assert 'data-testid="event-recording-resource"' not in body
        assert 'Materials</h2>' not in body
        assert 'https://example.com/event-doc' not in body
        assert 'https://example.com/ws-doc' not in body

    def test_standalone_event_detail_renders_structured_resources_before_feedback(
        self, django_server, page,
    ):
        """Issue #1037: standalone past events expose explicit recording and
        materials as structured external resource links, not playback UI."""
        from django.utils import timezone

        from events.models import Event, EventFeedback

        _clear_workshops()
        event = Event.objects.create(
            slug='standalone-resources-event',
            title='Standalone Resources Event',
            description='A standalone event with follow-up resources.',
            start_datetime=timezone.now() - datetime.timedelta(days=4),
            end_datetime=timezone.now() - datetime.timedelta(days=4, hours=-1),
            status='completed',
            recording_url='https://youtube.com/watch?v=standalone',
            materials=[
                {
                    'title': 'Session notes',
                    'url': 'https://docs.example.com/session-notes',
                    'type': 'notes',
                },
                {'title': 'Broken material'},
            ],
            timestamps=[{'time_seconds': 30, 'label': 'Intro'}],
            transcript_text='Hidden transcript text',
            core_tools=['Hidden Tool'],
            learning_objectives=['Hidden objective'],
            outcome='Hidden outcome',
        )
        # Issue #1137: the feedback card only renders when it has content.
        # Give the event a rating so the (anonymous) viewer sees the section
        # and we can assert resources render before it.
        EventFeedback.objects.create(
            event=event,
            user=_create_user('rater-resources@test.com', tier_slug='free'),
            rating=4,
        )
        connection.close()

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        resources = page.locator('[data-testid="event-post-resources"]')
        feedback = page.locator('[data-testid="event-feedback-section"]')
        resources.wait_for(state='visible')
        feedback.wait_for(state='visible')

        body = page.content()
        assert 'Post-event resources' in body
        assert 'Watch recording' in body
        assert 'Session notes' in body
        assert 'notes' in body
        assert 'Broken material' not in body
        assert 'data-testid="event-recording-block"' not in body
        assert 'data-testid="video-chapters"' not in body
        assert 'class="video-timestamp' not in body
        assert '<iframe' not in body.lower()
        assert 'Hidden transcript text' not in body
        assert 'Hidden Tool' not in body
        assert 'Hidden objective' not in body
        assert 'Hidden outcome' not in body
        assert '/event-recordings/' not in body

        recording = page.locator('[data-testid="event-recording-resource"]')
        assert recording.get_attribute('href') == 'https://youtube.com/watch?v=standalone'
        assert recording.get_attribute('target') == '_blank'
        assert 'noopener' in (recording.get_attribute('rel') or '')

        material = page.locator('[data-testid="event-material-resource"]')
        assert material.count() == 1
        assert material.first.get_attribute('href') == 'https://docs.example.com/session-notes'
        assert material.first.get_attribute('target') == '_blank'
        assert 'noopener' in (material.first.get_attribute('rel') or '')

        resources_box = resources.bounding_box()
        feedback_box = feedback.bounding_box()
        assert resources_box is not None
        assert feedback_box is not None
        assert resources_box['y'] + resources_box['height'] < feedback_box['y']

    def test_upcoming_event_detail_suppresses_prepopulated_resources(
        self, django_server, page,
    ):
        """Issue #1037: prefilled links stay hidden until the event is past."""
        from django.utils import timezone

        from events.models import Event

        _clear_workshops()
        event = Event.objects.create(
            slug='upcoming-prefilled-resources-event',
            title='Upcoming Prefilled Resources Event',
            description='Register before the live session.',
            start_datetime=timezone.now() + datetime.timedelta(days=4),
            end_datetime=timezone.now() + datetime.timedelta(days=4, hours=1),
            status='upcoming',
            recording_url='https://youtube.com/watch?v=early',
            materials=[
                {
                    'title': 'Early notes',
                    'url': 'https://docs.example.com/early-notes',
                },
            ],
        )
        connection.close()

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'Upcoming Prefilled Resources Event' in body
        assert 'data-testid="event-post-resources"' not in body
        assert 'Watch recording' not in body
        assert 'Early notes' not in body
        assert 'data-testid="event-feedback-section"' not in body

    def test_standalone_event_without_resources_keeps_gap_before_feedback(
        self, django_server, page,
    ):
        """Issue #1037: short description-only past events do not crowd the
        feedback card."""
        from django.utils import timezone

        from events.models import Event, EventFeedback

        _clear_workshops()
        event = Event.objects.create(
            slug='short-description-no-resources-event',
            title='Short Description No Resources Event',
            description='Short description.',
            start_datetime=timezone.now() - datetime.timedelta(days=4),
            end_datetime=timezone.now() - datetime.timedelta(days=4, hours=-1),
            status='completed',
        )
        # Issue #1137: render the feedback card by giving the event a rating
        # so we can measure the gap above it for a short description-only body.
        EventFeedback.objects.create(
            event=event,
            user=_create_user('rater-gap@test.com', tier_slug='free'),
            rating=4,
        )
        connection.close()

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'Short description.' in body
        assert 'data-testid="event-post-resources"' not in body

        description = page.locator('article .prose').first
        feedback = page.locator('[data-testid="event-feedback-section"]')
        feedback.wait_for(state='visible')
        description_box = description.bounding_box()
        feedback_box = feedback.bounding_box()
        assert description_box is not None
        assert feedback_box is not None
        gap = feedback_box['y'] - (description_box['y'] + description_box['height'])
        assert gap >= 32

    def test_staff_audits_resolved_materials_in_studio(
        self, django_server, browser,
    ):
        """Staff visiting the Studio workshop detail page sees the
        resolved materials list with a source label per item."""
        from playwright_tests.conftest import create_staff_user

        _clear_workshops()
        ws_with_workshop_mat = _create_workshop(
            slug='audit-workshop',
            title='Audit Workshop',
            landing=0, pages=0, recording=0,
            with_event=True,
            materials=[
                {'title': 'Recording notes',
                 'url': 'https://example.com/notes'},
            ],
            workshop_materials=[
                {'title': 'Deck',
                 'url': 'https://example.com/deck'},
            ],
        )
        ws_with_event_only = _create_workshop(
            slug='audit-workshop-2',
            title='Audit Workshop 2',
            landing=0, pages=0, recording=0,
            with_event=True,
            materials=[
                {'title': 'EventOnly',
                 'url': 'https://example.com/event-only'},
            ],
            workshop_materials=[],
        )
        create_staff_user(email='studio@test.com')

        ctx = _auth_context(browser, 'studio@test.com')
        p = ctx.new_page()

        p.goto(
            f'{django_server}/studio/workshops/{ws_with_workshop_mat.pk}/',
            wait_until='domcontentloaded',
        )
        body = p.content()
        assert 'data-testid="studio-workshop-materials"' in body
        assert 'Deck' in body
        assert 'from workshop' in body
        # Workshop-level wins — event-only material name is not shown.
        assert 'Recording notes' not in body

        p.goto(
            f'{django_server}/studio/workshops/{ws_with_event_only.pk}/',
            wait_until='domcontentloaded',
        )
        body = p.content()
        assert 'EventOnly' in body
        assert 'from linked event' in body
        ctx.close()
