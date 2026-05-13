"""Playwright E2E tests for the per-object Re-sync source button (issue #281).

Scenarios:
1. Operator re-syncs an article from its edit page; flash names the repo
   and content type and the dashboard reflects the queued state.
2. Operator re-syncs from a unit edit page; the click resolves to the
   parent course's source (units inherit course source).
3. A manually-created article (no source_repo) hides both the synced
   origin panel and the Re-sync button.
4. Worker-down warning is surfaced on the flash when the django_q
   worker is not alive.
5. Re-sync from a workshop detail page hits the workshop ContentSource.
6. Non-staff users cannot trigger a re-sync (403, no SyncLog row).
7. The view 404s for a nonexistent object.
8. Re-sync errors gracefully when no matching ContentSource exists for
   the object's source_repo + content_type.
"""

import datetime
import os
from unittest import mock

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
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _reset_state():
    """Delete all sync-affected fixtures so each test starts fresh."""
    from content.models import (
        Article,
        Course,
        Module,
        Unit,
        Workshop,
        WorkshopPage,
    )
    from integrations.models import ContentSource, SyncLog

    SyncLog.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    ContentSource.objects.all().delete()
    connection.close()


def _create_article(slug='blog-1', title='Blog Post', synced=True):
    from content.models import Article

    kwargs = {
        'title': title,
        'slug': slug,
        'date': datetime.date(2026, 1, 1),
        'published': True,
    }
    if synced:
        kwargs['source_repo'] = 'AI-Shipping-Labs/content'
        kwargs['source_path'] = f'blog/{slug}.md'
        kwargs['source_commit'] = 'abc1234def5678901234567890123456789abcde'
    a = Article.objects.create(**kwargs)
    connection.close()
    return a


def _create_course_with_unit(course_slug='course-x'):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title='Course X',
        slug=course_slug,
        source_repo='AI-Shipping-Labs/content',
        source_path=f'courses/{course_slug}/course.yaml',
        source_commit='aaa1111bbb22223333444455556666777788888',
    )
    mod = Module.objects.create(
        course=course, title='Mod 1', slug='mod-1', sort_order=1,
    )
    unit = Unit.objects.create(
        module=mod,
        title='Lesson 1', slug='lesson-1', sort_order=1,
        source_repo='AI-Shipping-Labs/content',
        source_path=f'courses/{course_slug}/mod-1/lesson-1.md',
    )
    connection.close()
    return course, mod, unit


def _create_workshop():
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor

    ws = Workshop.objects.create(
        slug='ws-1',
        title='Workshop One',
        date=datetime.date(2026, 4, 21),
        description='Hands-on intro.',
        tags=['agents'],
        status='published',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        source_repo='AI-Shipping-Labs/workshops-content',
        source_path='2026/ws-1/workshop.yaml',
        source_commit='ccc1234def5678901234567890123456789abcde',
    )
    instructor_name = 'Alice'
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200] or 'test-instructor',
        defaults={
            'name': instructor_name,
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=ws,
        instructor=instructor,
        defaults={'position': 0},
    )
    connection.close()
    return ws


def _create_source(repo_name, content_type=None, content_path=''):
    """Create a ContentSource by repo_name.

    ``content_type`` and ``content_path`` are accepted for call-site
    compatibility but ignored — issue #310 dropped per-type rows in
    favour of one row per repo.
    """
    from integrations.models import ContentSource

    src, _ = ContentSource.objects.get_or_create(
        repo_name=repo_name,
        defaults={'is_private': False},
    )
    connection.close()
    return src


# ---------------------------------------------------------------
# Scenario 1: Re-sync an article from its edit page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestArticleResyncFromEditPage:
    def test_resync_button_queues_article_sync(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        article = _create_article()
        _create_source('AI-Shipping-Labs/content', 'article')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )

        # Origin controls all visible.
        body = page.content()
        assert 'data-testid="origin-panel"' in body
        assert 'data-testid="synced-banner"' not in body
        assert 'Edit on GitHub' in body
        assert 'View on site' in body
        assert 'data-testid="resync-source-button"' in body

        with mock.patch(
            'django_q.tasks.async_task', return_value='task-resync-article',
        ):
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        # Reloaded on the same edit URL (HTTP_REFERER honoured).
        assert page.url.rstrip('/').endswith(
            f'/studio/articles/{article.pk}/edit',
        )

        # Flash banner mentions the repo and content type.
        body = page.content()
        assert 'AI-Shipping-Labs/content' in body
        assert 'article' in body
        assert 'Sync queued' in body

        connection.close()
        context.close()

    @pytest.mark.core
    def test_dashboard_shows_queued_after_resync(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        article = _create_article()
        _create_source('AI-Shipping-Labs/content', 'article')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )
        with mock.patch(
            'django_q.tasks.async_task', return_value='task-resync-article',
        ):
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        # Visit the sync dashboard — the source should show queued.
        page.goto(
            f'{django_server}/studio/sync/',
            wait_until='domcontentloaded',
        )
        body = page.content()
        # Queued pill rendered for the just-clicked source.
        assert '>queued<' in body

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 2: Re-sync from a unit edit page (inherits course)
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestUnitPageInheritsCourseSource:
    def test_unit_page_resync_uses_course_content_type(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        course, _mod, unit = _create_course_with_unit()
        _create_source('AI-Shipping-Labs/content', 'course')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/units/{unit.pk}/edit',
            wait_until='domcontentloaded',
        )

        # The banner visible on the unit edit page targets the parent course.
        body = page.content()
        assert (
            f'/studio/sync/object/course/{course.pk}/trigger/' in body
        ), 'Unit page should target the parent course for re-sync'

        with mock.patch(
            'django_q.tasks.async_task', return_value='task-resync-course',
        ):
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        body = page.content()
        # Flash names the COURSE content type, not "unit".
        assert 'course' in body
        assert 'AI-Shipping-Labs/content' in body
        assert 'Sync queued' in body

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 3: Manually-created article hides the button
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestManualArticleHidesResync:
    def test_manual_article_no_origin_panel_no_button(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        article = _create_article(synced=False)

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )

        body = page.content()
        # No origin panel, no Re-sync button anywhere.
        assert 'data-testid="origin-panel"' not in body
        assert 'data-testid="synced-banner"' not in body
        assert 'data-testid="resync-source-button"' not in body
        assert 'Re-sync source' not in body

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 4: Worker-down warning surfaces on the flash
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestResyncWorkerDownWarning:
    def test_worker_down_flash_is_warning(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        article = _create_article()
        _create_source('AI-Shipping-Labs/content', 'article')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )

        # Stub async_task AND force the worker-status helper to report
        # an absent worker so the warning suffix kicks in.
        with mock.patch(
            'django_q.tasks.async_task', return_value='task-resync-article',
        ), mock.patch(
            'studio.views.sync.get_worker_status',
            return_value={'expect_worker': True, 'alive': False},
        ):
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        body = page.content()
        assert 'worker is not running' in body
        assert 'manage.py qcluster' in body

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 5: Re-sync from a workshop detail page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestWorkshopResyncFromDetail:
    def test_workshop_detail_resync_targets_workshop_source(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        ws = _create_workshop()
        source = _create_source(
            'AI-Shipping-Labs/workshops-content', 'workshop',
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{ws.pk}/',
            wait_until='domcontentloaded',
        )

        body = page.content()
        # Origin panel visible.
        assert 'data-testid="origin-panel"' in body
        assert 'Synced from GitHub' in body
        assert 'data-testid="resync-source-button"' in body

        with mock.patch(
            'django_q.tasks.async_task', return_value='task-resync-workshop',
        ):
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        body = page.content()
        # Flash names the workshop content type.
        assert 'workshop' in body
        assert 'AI-Shipping-Labs/workshops-content' in body
        assert 'Sync queued' in body

        # A queued SyncLog row was created for the workshop source.
        from integrations.models import SyncLog
        log = SyncLog.objects.get(source=source)
        assert log.status == 'queued'

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 6: Non-staff cannot trigger a re-sync
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestResyncNonStaffBlocked:
    def test_non_staff_post_returns_403_no_synclog(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_user('main@test.com', tier_slug='main')
        article = _create_article()
        _create_source('AI-Shipping-Labs/content', 'article')

        context = _auth_context(browser, 'main@test.com')
        page = context.new_page()
        # Visit a public page first so a real csrftoken cookie is set in
        # the browser context. Otherwise Django's CSRF middleware rejects
        # the direct POST before ``staff_required`` has a chance to run.
        page.goto(
            f'{django_server}/',
            wait_until='domcontentloaded',
        )
        cookies = context.cookies(django_server)
        csrf_value = next(
            (c['value'] for c in cookies if c['name'] == 'csrftoken'),
            'e2e-test-csrf-token-value',
        )

        # Direct POST as a member: server returns 403 from staff_required.
        response = context.request.post(
            f'{django_server}'
            f'/studio/sync/object/article/{article.pk}/trigger/',
            data={'csrfmiddlewaretoken': csrf_value},
            headers={'X-CSRFToken': csrf_value, 'Referer': django_server},
        )
        assert response.status == 403, (
            f'expected 403 for member, got {response.status}'
        )

        # No SyncLog row was created.
        from integrations.models import SyncLog
        assert SyncLog.objects.count() == 0

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 7: 404 for nonexistent object
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestResync404Missing:
    def test_post_to_missing_object_returns_404(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        # Need at least one synced object so the studio dashboard renders
        # a CSRF token cookie of the right length when we visit it. Without
        # this the direct POST below trips Django's CSRF check before the
        # 404 logic gets a chance to run.
        article = _create_article(slug='dummy')
        _create_source('AI-Shipping-Labs/content', 'article')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        # Visit any studio page to set a real csrftoken cookie.
        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )
        # Read the real CSRF cookie value from the browser context.
        cookies = context.cookies(django_server)
        csrf_value = next(
            (c['value'] for c in cookies if c['name'] == 'csrftoken'),
            'e2e-test-csrf-token-value',
        )

        response = context.request.post(
            f'{django_server}/studio/sync/object/article/999999/trigger/',
            data={'csrfmiddlewaretoken': csrf_value},
            headers={'X-CSRFToken': csrf_value, 'Referer': django_server},
        )
        assert response.status == 404

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 8: Missing ContentSource flashes graceful error
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestResyncMissingContentSource:
    def test_missing_source_flashes_red_error(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        # Article points at a repo we never registered.
        from content.models import Article
        article = Article.objects.create(
            title='Orphan',
            slug='orphan',
            date=datetime.date(2026, 1, 1),
            published=True,
            source_repo='Old-Org/old-repo',
            source_path='blog/orphan.md',
            source_commit='abc1234def5678901234567890123456789abcde',
        )
        connection.close()

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/articles/{article.pk}/edit',
            wait_until='domcontentloaded',
        )

        # Banner shows the orphan source_repo so the operator sees the issue.
        body = page.content()
        assert 'Old-Org/old-repo' in body
        assert 'data-testid="resync-source-button"' in body

        with mock.patch('django_q.tasks.async_task') as mock_async:
            page.locator('[data-testid="resync-source-button"]').click()
            page.wait_for_load_state('domcontentloaded')

        # The mock was never called because the view bailed before enqueueing.
        assert mock_async.call_count == 0

        body = page.content()
        # A graceful error flash explaining the missing source.
        assert 'No content source is configured' in body
        assert 'Old-Org/old-repo' in body
        assert 'article' in body

        # No SyncLog row was created.
        from integrations.models import SyncLog
        assert SyncLog.objects.count() == 0

        connection.close()
        context.close()
