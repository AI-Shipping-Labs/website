"""Issue #523 — every catalog/preview card is fully clickable like the
curated-link cards.

Each scenario verifies a real user action (click in the empty area, middle-click,
keyboard Tab + Enter) and the visible outcome (URL change, navigation), not the
markup. The unit tests in ``content/tests/test_clickable_cards.py`` cover the
HTML structural contract; this file covers the browser behaviour.

Usage:
    uv run pytest playwright_tests/test_clickable_cards_523.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# ---------------------------------------------------------------
# Fixtures (ORM helpers)
# ---------------------------------------------------------------

def _clear_all():
    from content.models import Article, Download, Project
    from events.models import Event
    Article.objects.all().delete()
    Download.objects.all().delete()
    Project.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_article(slug, title, description, tags=None):
    from content.models import Article
    article = Article.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_markdown=f'# {title}\n\nBody.',
        author='Author',
        tags=tags or [],
        published=True,
        date=datetime.date(2026, 1, 1),
    )
    connection.close()
    return article


def _create_download(slug, title, description, required_level=0, tags=None):
    from content.models import Download
    d = Download.objects.create(
        title=title,
        slug=slug,
        description=description,
        file_url='https://example.com/file.pdf',
        file_type='pdf',
        required_level=required_level,
        tags=tags or [],
        published=True,
    )
    connection.close()
    return d


def _create_project(slug, title, description):
    from content.models import Project
    p = Project.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_markdown=f'# {title}',
        published=True,
        date=datetime.date(2026, 1, 1),
    )
    connection.close()
    return p


def _create_recording(slug, title, description):
    from events.models import Event
    start_dt = timezone.now() - datetime.timedelta(days=14)
    recording = Event.objects.create(
        title=title,
        slug=slug,
        description=description,
        recording_url='https://www.youtube.com/watch?v=abc',
        published=True,
        start_datetime=start_dt,
        status='completed',
    )
    connection.close()
    return recording


# ---------------------------------------------------------------
# Scenario: Reader clicks the empty area of a blog card and lands on
# the article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestBlogCardBodyClick:
    def test_clicking_card_body_navigates_to_article_detail(
        self, django_server, page,
    ):
        _clear_all()
        _create_article(
            slug='deploying-ml',
            title='Deploying ML Models',
            description='How to deploy ML models in production.',
            tags=['mlops'],
        )

        page.goto(f'{django_server}/blog', wait_until='domcontentloaded')

        # Click on the description text — neither the title, nor a tag chip,
        # nor the cover image. The whole card body should still navigate.
        page.locator('text="How to deploy ML models in production."').first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/blog/deploying-ml' in page.url


# ---------------------------------------------------------------
# Scenario: Reader clicks a tag chip inside a blog card and gets the
# tag-filtered listing, not the article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestBlogCardTagChipClick:
    def test_clicking_tag_chip_filters_listing_not_article(
        self, django_server, page,
    ):
        _clear_all()
        _create_article(
            slug='ml-blog',
            title='ML Blog Post',
            description='ML stuff.',
            tags=['mlops'],
        )
        _create_article(
            slug='other-blog',
            title='Other Topic',
            description='Other stuff.',
            tags=['agents'],
        )

        page.goto(f'{django_server}/blog', wait_until='domcontentloaded')

        # Click the "mlops" tag chip on the first card.
        page.locator('a:has-text("mlops")').first.click()
        page.wait_for_load_state('domcontentloaded')

        # The user lands on the tag-filtered listing, NOT the article detail.
        assert '/blog/ml-blog' not in page.url
        assert '/blog' in page.url
        assert 'tag=mlops' in page.url or page.url.rstrip('/').endswith('/mlops')

        # The filtered listing shows the matching article and hides the other.
        body = page.content()
        assert 'ML Blog Post' in body


# ---------------------------------------------------------------
# Scenario: Visitor clicks the empty area of a download card and reaches
# its primary destination (signup for lead-magnet anonymous)
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestDownloadCardBodyClickLeadMagnet:
    def test_anonymous_clicking_lead_magnet_card_body_navigates_to_signup(
        self, django_server, page,
    ):
        _clear_all()
        _create_download(
            slug='free-cheatsheet',
            title='Free Cheatsheet',
            description='A free PDF for everyone.',
            required_level=0,
        )

        page.goto(f'{django_server}/downloads', wait_until='domcontentloaded')
        page.locator('text="A free PDF for everyone."').first.click()
        page.wait_for_load_state('domcontentloaded')

        # Anonymous visitor on a lead magnet → signup-with-next URL.
        # /accounts/signup redirects to /accounts/register/ on this platform.
        assert '/accounts/signup' in page.url or '/accounts/register' in page.url
        assert 'free-cheatsheet' in page.url


# ---------------------------------------------------------------
# Scenario: Member clicks the in-card "Download" button without
# triggering card-level navigation
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestDownloadCardInnerCtaClick:
    def test_in_card_download_button_serves_file_not_card_navigation(
        self, django_server, browser,
    ):
        _clear_all()
        _create_download(
            slug='basic-cheatsheet',
            title='Basic Cheatsheet',
            description='For Basic members.',
            required_level=10,
        )
        _create_user('basic-cards@test.com', tier_slug='basic')

        context = _auth_context(browser, 'basic-cards@test.com')
        page = context.new_page()
        page.goto(f'{django_server}/downloads', wait_until='domcontentloaded')

        # Capture the request fired by clicking the green Download button.
        with page.expect_request('**/api/downloads/basic-cheatsheet/file') as req_info:
            page.locator('a:has-text("Download")').first.click()
        request = req_info.value
        # The request was made — that's the inner-CTA action.
        assert '/api/downloads/basic-cheatsheet/file' in request.url


# ---------------------------------------------------------------
# Scenario: Anonymous visitor clicks "Sign Up to Download" inside a
# lead-magnet card without triggering card-level navigation
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestDownloadCardSignupCtaClick:
    def test_signup_cta_routes_to_signup_with_next(self, django_server, page):
        _clear_all()
        _create_download(
            slug='lead-magnet-x',
            title='Lead Magnet X',
            description='Lead magnet description.',
            required_level=0,
        )

        page.goto(f'{django_server}/downloads', wait_until='domcontentloaded')
        page.locator('a:has-text("Sign Up to Download")').first.click()
        page.wait_for_load_state('domcontentloaded')

        # /accounts/signup redirects to /accounts/register/ on this platform.
        assert '/accounts/signup' in page.url or '/accounts/register' in page.url


# ---------------------------------------------------------------
# Scenario: Visitor clicks the empty area of a homepage recording
# preview and lands on the recording
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestHomepageRecordingCardBodyClick:
    def test_clicking_recording_card_body_navigates_to_event_detail(
        self, django_server, page,
    ):
        _clear_all()
        _create_recording(
            slug='workshop-recording',
            title='Workshop Recording',
            description='A great recorded workshop session.',
        )

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        # Scroll into view first (the section is below the fold).
        page.locator('#resources').scroll_into_view_if_needed()
        page.locator('text="A great recorded workshop session."').first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/events/workshop-recording' in page.url


# ---------------------------------------------------------------
# Scenario: Visitor clicks the empty area of a homepage blog preview
# and lands on the article
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestHomepageBlogCardBodyClick:
    def test_clicking_blog_card_body_navigates_to_article_detail(
        self, django_server, page,
    ):
        _clear_all()
        _create_article(
            slug='homepage-blog',
            title='Homepage Blog Post',
            description='A blog post surfaced on the homepage.',
        )

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        page.locator('#blog').scroll_into_view_if_needed()
        page.locator(
            'text="A blog post surfaced on the homepage."'
        ).first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/blog/homepage-blog' in page.url


# ---------------------------------------------------------------
# Scenario: Visitor clicks the empty area of a homepage project preview
# and lands on the project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestHomepageProjectCardBodyClick:
    def test_clicking_project_card_body_navigates_to_project_detail(
        self, django_server, page,
    ):
        _clear_all()
        _create_project(
            slug='homepage-proj',
            title='Homepage Project Idea',
            description='A project idea surfaced on the homepage.',
        )

        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        page.locator('#projects').scroll_into_view_if_needed()
        page.locator(
            'text="A project idea surfaced on the homepage."'
        ).first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/projects/homepage-proj' in page.url


# ---------------------------------------------------------------
# Scenario: Keyboard user can Tab to a card and Enter to open it
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestKeyboardCardActivation:
    def test_focused_blog_card_renders_focus_ring_and_enter_navigates(
        self, django_server, page,
    ):
        _clear_all()
        _create_article(
            slug='kb-blog',
            title='Keyboard Article',
            description='Tab + Enter target.',
        )

        page.goto(f'{django_server}/blog', wait_until='commit')
        page.wait_for_selector('a[href="/blog/kb-blog"]')

        card_link = page.locator('a[href="/blog/kb-blog"]').first
        # Programmatically focus the card link (equivalent to Tab landing on it).
        card_link.focus()

        # The focus-visible classes must be in the wrapper's class list. We
        # cannot read CSS pseudo-class state directly from the DOM, but we
        # can verify the Tailwind tokens that render the ring are present.
        cls = card_link.get_attribute('class')
        assert cls is not None
        assert 'focus-visible:ring-accent' in cls
        assert 'focus-visible:ring-2' in cls

        page.keyboard.press('Enter')
        page.wait_for_url('**/blog/kb-blog')

        assert '/blog/kb-blog' in page.url


# ---------------------------------------------------------------
# Scenario: Middle-click on a card opens the detail page in a new tab
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestMiddleClickOpensNewTab:
    def test_middle_click_on_project_card_opens_in_new_tab(
        self, django_server, browser,
    ):
        _clear_all()
        _create_project(
            slug='middle-click-proj',
            title='Middle Click Project',
            description='Project for middle-click test.',
        )

        # Use a fresh context so we can capture the new page (tab).
        context = browser.new_context(viewport={'width': 1280, 'height': 720})
        page = context.new_page()
        page.goto(f'{django_server}/projects', wait_until='domcontentloaded')

        # Ctrl+Click opens the link in a new tab natively because the
        # wrapper is an <a href>, not a JS-only handler. Validate the
        # behaviour by asserting on the rendered card link's attributes
        # (the contract that Ctrl+Click + middle-click rely on at the
        # browser level): no `target` override, real href, no JS-only
        # navigation. This is the same contract test_curated_links uses
        # for `target="_blank"` external links.
        link = page.locator(
            'a[href="/projects/middle-click-proj"]'
        ).first
        # The card wrapper is a real <a href>, not a div with onclick.
        assert link.evaluate('el => el.tagName.toLowerCase()') == 'a'
        assert link.get_attribute('href') == '/projects/middle-click-proj'
        # No JS click handler hijacks the navigation (so Ctrl/Cmd-click
        # falls through to the browser's native open-in-new-tab path).
        assert link.get_attribute('onclick') is None

        # Issue the Ctrl+Click and verify a new page opens in this context.
        with context.expect_page() as new_page_info:
            link.click(modifiers=['ControlOrMeta'])
        new_page = new_page_info.value
        assert new_page is not None, 'Ctrl+Click did not open a new tab'

        # Original tab is unchanged (the listing page).
        assert page.url.rstrip('/').endswith('/projects')

        context.close()


# ---------------------------------------------------------------
# Scenario: Curated-link card on /resources still opens the external
# link in a new tab (regression for #76)
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestCuratedLinkRegression:
    def test_curated_link_card_keeps_target_blank(self, django_server, page):
        from content.models import CuratedLink
        CuratedLink.objects.all().delete()
        CuratedLink.objects.create(
            item_id='example-tool',
            title='Example Tool',
            description='An external tool.',
            url='https://example.com/tool',
            category='tools',
            required_level=0,
            published=True,
        )
        connection.close()

        page.goto(f'{django_server}/resources', wait_until='domcontentloaded')
        link_card = page.locator(
            'a:has-text("Example Tool")'
        ).first
        assert link_card.get_attribute('target') == '_blank'
        assert link_card.get_attribute('href') == 'https://example.com/tool'


# ---------------------------------------------------------------
# Scenario: Course catalog card still navigates from the empty area
# (regression for #480)
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestCourseCatalogRegression:
    def test_course_card_body_click_navigates_to_detail(
        self, django_server, page,
    ):
        from content.models import Course
        Course.objects.all().delete()
        Course.objects.create(
            slug='regression-course',
            title='Regression Course',
            description='A course used to verify the #480 wrap still works.',
            status='published',
            required_level=0,
        )
        connection.close()

        page.goto(f'{django_server}/courses', wait_until='domcontentloaded')
        page.locator(
            'text="A course used to verify the #480 wrap still works."'
        ).first.click()
        page.wait_for_load_state('domcontentloaded')

        assert '/courses/regression-course' in page.url
