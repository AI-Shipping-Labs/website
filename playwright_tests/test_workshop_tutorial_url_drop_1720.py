"""Playwright E2E tests for dropping /tutorial/ from workshop page URLs (#1720).

Covers the ten scenarios groomed in the issue:

1. Anonymous reader follows a workshop's open tutorial page to the new
   URL shape by clicking the page row on the landing page.
2. Reader navigates between tutorial pages using Next/Previous.
3. Old bookmarked tutorial URL now 404s instead of redirecting.
4. Old dated legacy tutorial link still works and lands on the new
   canonical shape.
5. Gated visitor is teased on the new URL shape and finds the upgrade
   path.
6. Operator's sync fails loudly on a page slugged "video" and content
   stays untouched.
7. Workshop-linked-from-course reader follows a cross-content link to
   the new shape.
8. Reader follows an in-page markdown link between sibling tutorial
   pages.
9. Signed-out reader on a gated tutorial page signs in and returns to
   the same page.
10. Search engine crawler finds tutorial pages at the new URL shape.

Usage:
    uv run pytest playwright_tests/test_workshop_tutorial_url_drop_1720.py -v
"""

import datetime
import os
import shutil
import tempfile
import uuid

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, in-process sync) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

DEFAULT_PASSWORD = 'TestPass123!'

LEVEL_BASIC = 10
LEVEL_MAIN = 20


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    from integrations.models import ContentSource

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    # Wipe sync sources so each sync-driven scenario starts clean.
    ContentSource.objects.filter(
        repo_name='AI-Shipping-Labs/workshops-content',
    ).delete()
    connection.close()


def _create_workshop(
    *,
    slug,
    title='Tutorial URL Workshop',
    landing=0,
    pages=0,
    recording=0,
    description='# Workshop\n\nDescription body.',
    pages_data=None,
):
    """Create a published workshop with pages (ORM fixture, no sync).

    ``pages_data`` is an iterable of ``(slug, title, body)`` tuples.
    Defaults to a single page when omitted.
    """
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
    )
    pages_data = pages_data or [
        ('intro', 'Intro', '# Intro\n\nBody.'),
    ]
    for i, (page_slug, page_title, body) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=page_slug, title=page_title,
            sort_order=i, body=body,
        )
    connection.close()
    return workshop


def _sync_workshop_repo(files):
    """Write ``files`` (rel_path -> contents) into a temp repo and sync it.

    Returns the ``SyncLog`` so tests can assert on errors.
    """
    from integrations.models import ContentSource
    from integrations.services.github import sync_content_source

    source, _ = ContentSource.objects.get_or_create(
        repo_name='AI-Shipping-Labs/workshops-content',
        defaults={'is_private': False},
    )

    temp_dir = tempfile.mkdtemp(prefix='e2e-workshop-tutorial-url-')
    try:
        for rel_path, content in files.items():
            full = os.path.join(temp_dir, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)

        sync_log = sync_content_source(source, repo_dir=temp_dir)
        connection.close()
        return sync_log
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _workshop_yaml(
    *, slug, title='Sync Workshop', pages_required_level=0,
    landing_required_level=0,
):
    return (
        f'content_id: {uuid.uuid4()}\n'
        f'slug: {slug}\n'
        f'title: "{title}"\n'
        f'date: 2026-04-21\n'
        f'pages_required_level: {pages_required_level}\n'
        f'landing_required_level: {landing_required_level}\n'
        'instructor_name: Alexey\n'
    )


def _page_md(*, title, body=''):
    return f'---\ntitle: "{title}"\n---\n{body}'


# ---------------------------------------------------------------------
# Scenario 1: Anonymous reader follows an open tutorial page from the
# landing page's pages list to the new URL shape.
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestAnonymousFollowsOpenPageFromLanding:
    @browser_journey
    def test_landing_page_row_click_lands_on_new_shape(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='landing-click-ws',
            pages_data=[('setup', 'Setup', '# Setup\n\nLANDINGCLICKMARKER.')],
        )

        page.goto(
            f'{django_server}/workshops/landing-click-ws',
            wait_until='domcontentloaded',
        )
        row = page.locator('a[href="/workshops/landing-click-ws/setup"]')
        assert row.count() == 1

        row.first.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url == f'{django_server}/workshops/landing-click-ws/setup'
        assert 'LANDINGCLICKMARKER' in page.content()


# ---------------------------------------------------------------------
# Scenario 2: Reader navigates between tutorial pages using Next/Previous
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestPrevNextReaderNavigation:
    @browser_journey
    def test_next_and_prev_links_use_new_shape(self, django_server, browser):
        _clear_workshops()
        _create_workshop(
            slug='nav-ws', pages=LEVEL_MAIN, recording=LEVEL_MAIN,
            pages_data=[
                ('one', 'Page One', '# Page One\n\nBody one.'),
                ('two', 'Page Two', '# Page Two\n\nBody two.'),
                ('three', 'Page Three', '# Page Three\n\nBody three.'),
            ],
        )
        _create_user(
            email='nav-main@test.com', tier_slug='main',
            email_verified=True,
        )
        ctx = _auth_context(browser, 'nav-main@test.com')
        page = ctx.new_page()

        page.goto(
            f'{django_server}/workshops/nav-ws/one',
            wait_until='domcontentloaded',
        )
        next_btn = page.locator('[data-testid="page-next-btn"]')
        assert next_btn.get_attribute('href') == '/workshops/nav-ws/two'

        next_btn.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == f'{django_server}/workshops/nav-ws/two'
        assert 'Body two.' in page.content()

        prev_btn = page.locator('[data-testid="page-prev-btn"]')
        assert prev_btn.get_attribute('href') == '/workshops/nav-ws/one'
        prev_btn.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == f'{django_server}/workshops/nav-ws/one'
        assert 'Body one.' in page.content()
        ctx.close()


# ---------------------------------------------------------------------
# Scenario 3: Old bookmarked tutorial URL now 404s, no redirect
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestOldBookmarkedUrl404s:
    @browser_journey
    def test_bare_slug_tutorial_path_404s_with_no_redirect(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='old-shape-ws',
            pages_data=[('setup', 'Setup', '# Setup\n\nBody.')],
        )

        response = page.goto(
            f'{django_server}/workshops/old-shape-ws/tutorial/setup',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 404
        # No redirect chain to the new shape — the owner's decision was
        # explicitly no redirects for the removed canonical shape.
        assert response.url == (
            f'{django_server}/workshops/old-shape-ws/tutorial/setup'
        )


# ---------------------------------------------------------------------
# Scenario 4: Old dated legacy link still works, lands on new shape
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDatedLegacyLinkStillRedirects:
    @browser_journey
    def test_dated_tutorial_link_redirects_to_new_shape(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='dated-ws',
            pages_data=[('setup', 'Setup', '# Setup\n\nDATEDREDIRECTMARKER.')],
        )

        # The dated legacy route (content/urls.py) still matches its own
        # /tutorial/ shape unchanged; only the target it resolves to (via
        # WorkshopPage.get_absolute_url()) drops the segment.
        response = page.goto(
            f'{django_server}/workshops/2026-04-21-dated-ws/tutorial/setup',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        assert page.url == f'{django_server}/workshops/dated-ws/setup'
        assert 'DATEDREDIRECTMARKER' in page.content()


# ---------------------------------------------------------------------
# Scenario 5: Gated visitor is teased on the new shape, finds the upgrade path
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestGatedVisitorTeaserAndUpgradePath:
    @browser_journey
    def test_basic_member_on_main_gated_page_sees_upgrade_cta(
        self, django_server, browser,
    ):
        _clear_workshops()
        _create_workshop(
            slug='gated-shape-ws', pages=LEVEL_MAIN, recording=LEVEL_MAIN,
            pages_data=[('lesson', 'Lesson', '# Lesson\n\nBody.')],
        )
        _create_user(
            email='basic-gated@test.com', tier_slug='basic',
            email_verified=True,
        )
        ctx = _auth_context(browser, 'basic-gated@test.com')
        page = ctx.new_page()

        response = page.goto(
            f'{django_server}/workshops/gated-shape-ws/lesson',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 403
        # Request URL kept the new shape — no redirect to gate.
        assert page.url == f'{django_server}/workshops/gated-shape-ws/lesson'

        assert page.locator('[data-testid="page-paywall"]').count() == 1
        assert page.locator('[data-testid="teaser-body"]').count() == 1
        cta = page.locator('[data-testid="page-upgrade-cta"]')
        assert cta.get_attribute('href') == '/membership'

        cta.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == f'{django_server}/membership'
        assert page.locator('[data-tier-card="main"]').count() == 1
        ctx.close()


# ---------------------------------------------------------------------
# Scenario 6: Sync fails loudly, per file, on a video-slugged page
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSyncFailsLoudlyOnVideoSlug:
    @browser_journey
    def test_video_slugged_page_is_skipped_and_reported(
        self, django_server, browser,
    ):
        _clear_workshops()
        folder = '2026-04-21-reserved-slug-ws'
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='reserved-slug-ws', title='Reserved Slug Workshop',
            ),
            # Filename derives slug 'video' — reserved (issue #1720).
            f'{folder}/02-video.md': _page_md(
                title='Video', body='This page must not sync.',
            ),
            # A normal sibling page must still sync fine.
            f'{folder}/01-intro.md': _page_md(
                title='Intro', body='Normal page body.',
            ),
        })

        video_errors = [
            e for e in (sync_log.errors or [])
            if 'video' in e.get('error', '').lower()
            and '02-video.md' in e.get('file', '')
        ]
        assert len(video_errors) == 1, sync_log.errors

        from content.models import Workshop, WorkshopPage

        workshop = Workshop.objects.get(slug='reserved-slug-ws')
        assert not WorkshopPage.objects.filter(
            workshop=workshop, slug='video',
        ).exists()
        assert WorkshopPage.objects.filter(
            workshop=workshop, slug='intro',
        ).exists()
        connection.close()

        # Step: the error is surfaced loudly in Studio, naming the file
        # and the reason — not just relying on silent route ordering.
        _create_staff_user('sync-audit@test.com')
        ctx = _auth_context(browser, 'sync-audit@test.com')
        studio_page = ctx.new_page()
        studio_page.goto(
            f'{django_server}/studio/sync/history/',
            wait_until='domcontentloaded',
        )
        body = studio_page.content()
        assert '02-video.md' in body
        assert 'reserved' in body.lower()
        ctx.close()

        # Step: any visitor on the workshop landing sees no page row for
        # the skipped file — it was never created, not silently reachable
        # under a broken URL.
        visitor = browser.new_page()
        visitor.goto(
            f'{django_server}/workshops/reserved-slug-ws',
            wait_until='domcontentloaded',
        )
        assert visitor.locator('a[href="/workshops/reserved-slug-ws/video"]').count() == 0
        assert visitor.locator('a[href="/workshops/reserved-slug-ws/intro"]').count() == 1
        visitor.close()


# ---------------------------------------------------------------------
# Scenario 7: Workshop-linked-from-course reader follows a cross-content
# link to the new shape
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCourseUnitLinksToWorkshopPage:
    @browser_journey
    def test_course_unit_link_to_workshop_page_uses_new_shape(
        self, django_server, browser,
    ):
        _clear_workshops()
        workshop = _create_workshop(
            slug='linked-from-course-ws',
            pages_data=[('setup', 'Setup', '# Setup\n\nCOURSELINKEDMARKER.')],
        )

        from content.models import Course, Module, Unit

        course = Course.objects.create(
            slug='course-links-to-workshop', title='Course Links Workshop',
            status='published', required_level=0,
        )
        module = Module.objects.create(
            course=course, slug='module-one', title='Module One',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module, slug='unit-one', title='Unit One', sort_order=1,
            body=(
                'See the [related workshop](/workshops/'
                f'{workshop.slug}/setup) for hands-on practice.'
            ),
        )
        connection.close()

        _create_user(
            email='course-main@test.com', tier_slug='main',
            email_verified=True,
        )
        ctx = _auth_context(browser, 'course-main@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/courses/{course.slug}/{module.slug}/{unit.slug}',
            wait_until='domcontentloaded',
        )
        link = page.locator('a:has-text("related workshop")').first
        assert link.get_attribute('href') == '/workshops/linked-from-course-ws/setup'

        link.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == (
            f'{django_server}/workshops/linked-from-course-ws/setup'
        )
        assert 'COURSELINKEDMARKER' in page.content()
        ctx.close()


# ---------------------------------------------------------------------
# Scenario 8: In-page markdown link between sibling tutorial pages
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSiblingPageLinkResolution:
    @browser_journey
    def test_sibling_md_link_resolves_to_new_shape(
        self, django_server, page,
    ):
        _clear_workshops()
        folder = '2026-04-21-sibling-link-ws'
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='sibling-link-ws', title='Sibling Link Workshop',
            ),
            f'{folder}/01-intro.md': _page_md(
                title='Intro',
                body='Continue to [10-qa.md](10-qa.md) next.',
            ),
            f'{folder}/10-qa.md': _page_md(
                title='Q&A', body='SIBLINGQAMARKER body.',
            ),
        })
        non_info = [
            e for e in (sync_log.errors or []) if e.get('severity') != 'info'
        ]
        assert non_info == [], non_info

        page.goto(
            f'{django_server}/workshops/sibling-link-ws/intro',
            wait_until='domcontentloaded',
        )
        # Bare-filename link text is title-substituted (issue #301).
        link = page.locator('a:has-text("Q&A")').first
        assert link.get_attribute('href') == '/workshops/sibling-link-ws/qa'

        link.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url == f'{django_server}/workshops/sibling-link-ws/qa'
        assert 'SIBLINGQAMARKER' in page.content()


# ---------------------------------------------------------------------
# Scenario 9: Sign-in round trip lands back on the same tutorial-less URL
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSignInRoundTripOnNewShape:
    @browser_journey
    def test_signin_returns_to_same_new_shape_url(self, django_server, page):
        _clear_workshops()
        _create_workshop(
            slug='signin-shape-ws', pages=5, recording=10,
            pages_data=[('intro', 'Intro', '# Intro\n\nSIGNINSHAPEMARKER.')],
        )
        _create_user(
            email='signin-shape@test.com', tier_slug='free',
            email_verified=True, password=DEFAULT_PASSWORD,
        )

        target_url = f'{django_server}/workshops/signin-shape-ws/intro'
        page.goto(target_url, wait_until='domcontentloaded')
        page.locator('[data-testid="teaser-signin-cta"]').click()
        page.wait_for_load_state('domcontentloaded')
        assert '/accounts/login/' in page.url
        assert 'next=' in page.url

        page.fill('#login-email', 'signin-shape@test.com')
        page.fill('#login-password', DEFAULT_PASSWORD)
        page.click('#login-submit')
        page.wait_for_url('**/workshops/signin-shape-ws/intro')

        assert page.url == target_url
        assert 'SIGNINSHAPEMARKER' in page.content()


# ---------------------------------------------------------------------
# Scenario 10: Sitemap entries use the new shape
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSitemapUsesNewShape:
    @browser_journey
    def test_sitemap_contains_new_shape_and_omits_tutorial_and_dated(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='sitemap-shape-ws',
            pages_data=[('intro', 'Intro', '# Intro\n\nBody.')],
        )

        response = page.goto(
            f'{django_server}/sitemap.xml', wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        body = response.text()

        assert '/workshops/sitemap-shape-ws/intro' in body
        assert '/tutorial/' not in body
        assert '2026-04-21-sitemap-shape-ws' not in body
