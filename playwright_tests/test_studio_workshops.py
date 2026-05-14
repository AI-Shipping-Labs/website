"""
Playwright E2E tests for the Studio workshop management surface (issue #297).

Scenarios:
1. Sidebar navigation — "Workshops" entry appears between Recordings and
   Downloads and links to the list page.
2. List + filter — staff filters by status and sees only matching rows.
3. List + search — staff searches by title and sees only matching rows.
4. Detail page renders fields, the linked event, and the page list.
5. Edit form happy path — saving updates the workshop and redirects.
6. Edit form invariant rejection — invalid 3-gate combo blocks save.
7. Re-sync — staff clicks "Re-sync workshops" and lands on /studio/sync/
   with a queued message.

Read-only behaviour for yaml-sourced fields and access-control redirects
are exercised in ``studio/tests/test_workshops.py`` per the testing
guidelines (one authoritative test per behaviour, prefer the cheaper
layer when possible).
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
    ensure_tiers as _ensure_tiers,
)
from playwright_tests.conftest import (
    expand_studio_sidebar_section as _expand_studio_sidebar_section,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_workshops():
    """Delete all workshops, pages, events to start each test from zero."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    from integrations.models import ContentSource, SyncLog

    SyncLog.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.filter(kind='workshop').delete()
    ContentSource.objects.filter(repo_name='AI-Shipping-Labs/workshops-content').delete()
    connection.close()


def _create_workshop(
    slug='demo',
    title='Demo Workshop',
    status='published',
    landing=0,
    pages=10,
    recording=20,
    date=None,
    with_event=False,
    page_titles=None,
):
    """Create a Workshop (and optionally a linked Event + pages) for a test."""
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage
    from events.models import Event

    event = None
    if with_event:
        from django.utils import timezone as dj_tz
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=f'{title} (event)',
            kind='workshop',
            start_datetime=dj_tz.now(),
            status='completed',
        )

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=date or datetime.date(2026, 4, 21),
        description='Hands-on intro.',
        tags=['agents'],
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        source_repo='AI-Shipping-Labs/workshops-content',
        source_path=f'2026/{slug}/workshop.yaml',
        source_commit='abc1234def5678901234567890123456789abcde',
        event=event,
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
        workshop=workshop,
        instructor=instructor,
        defaults={'position': 0},
    )

    if page_titles:
        for i, t in enumerate(page_titles, start=1):
            WorkshopPage.objects.create(
                workshop=workshop,
                slug=t.lower().replace(' ', '-'),
                title=t,
                sort_order=i,
                body=f'# {t}\n\n...',
                source_path=f'2026/{slug}/{t.lower()}.md',
            )
    connection.close()
    return workshop


def _create_workshop_source():
    """Insert a ContentSource for the workshops repo."""
    from integrations.models import ContentSource

    src, _ = ContentSource.objects.get_or_create(
        repo_name='AI-Shipping-Labs/workshops-content',
        defaults={'is_private': False},
    )
    connection.close()
    return src


# ---------------------------------------------------------------
# Scenario 1: Sidebar navigation
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopSidebar:
    def test_sidebar_link_navigates_to_list(self, django_server, browser):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')
        _create_workshop(slug='rag-app', title='Building a RAG app')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(f'{django_server}/studio/', wait_until='domcontentloaded')

        # The link is visible in the sidebar once the Content section is open.
        _expand_studio_sidebar_section(page, "content")
        link = page.locator('#studio-sidebar-nav a[href="/studio/workshops/"]')
        assert link.count() == 1, 'Workshops link missing from sidebar'

        link.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url.rstrip('/').endswith('/studio/workshops')
        # The workshop row is rendered.
        assert 'Building a RAG app' in page.content()

        context.close()


# ---------------------------------------------------------------
# Scenario 2: List filter by status
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopListFilter:
    def test_filter_by_published(self, django_server, browser):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')

        # 2 drafts + 3 published.
        for i in range(2):
            _create_workshop(
                slug=f'draft-{i}', title=f'DraftWorkshop{i}',
                status='draft', date=datetime.date(2026, 1, 1 + i),
            )
        for i in range(3):
            _create_workshop(
                slug=f'pub-{i}', title=f'PublishedWorkshop{i}',
                status='published', date=datetime.date(2026, 2, 1 + i),
            )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/',
            wait_until='domcontentloaded',
        )
        body = page.content()
        for i in range(2):
            assert f'DraftWorkshop{i}' in body
        for i in range(3):
            assert f'PublishedWorkshop{i}' in body

        # Filter to published.
        page.goto(
            f'{django_server}/studio/workshops/?status=published',
            wait_until='domcontentloaded',
        )
        body = page.content()
        for i in range(3):
            assert f'PublishedWorkshop{i}' in body
        for i in range(2):
            # Drafts must not appear in any visible row.
            row_locator = page.locator(
                f'[data-testid="workshop-row"]:has-text("DraftWorkshop{i}")'
            )
            assert row_locator.count() == 0

        context.close()


# ---------------------------------------------------------------
# Scenario 3: List search by title
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopListSearch:
    def test_search_filters_to_match(self, django_server, browser):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')

        _create_workshop(
            slug='rag-basics', title='RAG basics',
            date=datetime.date(2026, 1, 1),
        )
        _create_workshop(
            slug='fine-tuning', title='Fine-tuning LLMs',
            date=datetime.date(2026, 2, 1),
        )
        _create_workshop(
            slug='agents-101', title='Agents 101',
            date=datetime.date(2026, 3, 1),
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/?q=rag',
            wait_until='domcontentloaded',
        )
        rows = page.locator('[data-testid="workshop-row"]')
        assert rows.count() == 1
        assert 'RAG basics' in rows.first.inner_text()

        context.close()


# ---------------------------------------------------------------
# Scenario 4: Detail page shows fields, linked event, pages
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopDetail:
    def test_detail_renders_full_workshop(self, django_server, browser):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')

        ws = _create_workshop(
            slug='demo-ws', title='Demo Workshop',
            with_event=True,
            page_titles=['Setup', 'Build', 'Deploy'],
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{ws.pk}/',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Heading & key fields
        assert 'Demo Workshop' in body
        assert 'Hands-on intro' in body
        assert 'Alice' in body  # instructor
        assert 'agents' in body  # tag

        # Three gates visible
        assert 'data-testid="landing-gate"' in body
        assert 'data-testid="pages-gate"' in body
        assert 'data-testid="recording-gate"' in body

        # Synced metadata
        assert 'AI-Shipping-Labs/workshops-content' in body
        assert '2026/demo-ws/workshop.yaml' in body

        # Linked event card with edit link
        assert 'Demo Workshop (event)' in body
        edit_event = page.locator('[data-testid="edit-event-link"]')
        assert edit_event.count() == 1
        href = edit_event.first.get_attribute('href')
        assert '/studio/events/' in href

        # Pages table in sort order with GitHub source links
        page_rows = page.locator(
            '[data-testid="workshop-pages-rows"] tr'
        )
        assert page_rows.count() == 3
        first_row_text = page_rows.nth(0).inner_text()
        assert 'Setup' in first_row_text
        last_row_text = page_rows.nth(2).inner_text()
        assert 'Deploy' in last_row_text

        # GitHub source links visible.
        source_links = page.locator('[data-testid="page-source-link"]')
        assert source_links.count() == 3
        first_href = source_links.first.get_attribute('href')
        assert first_href.startswith(
            'https://github.com/AI-Shipping-Labs/workshops-content/blob/main/'
        )

        # Page bodies are not exposed in the detail page — only metadata.
        assert '...' not in (
            source_links.first.inner_text() or ''
        )

        context.close()


# ---------------------------------------------------------------
# Scenario 5: Edit form happy path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopEditHappyPath:
    @pytest.mark.core
    def test_edit_status_and_save(self, django_server, browser):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')

        ws = _create_workshop(
            slug='draft-ws', title='Draft Workshop',
            status='draft', landing=0, pages=10, recording=20,
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{ws.pk}/edit',
            wait_until='domcontentloaded',
        )

        # Editable form is visible.
        assert page.locator(
            '[data-testid="workshop-edit-form"]'
        ).count() == 1

        # Yaml-sourced fields are NOT inside the form. We can assert that
        # by checking there is no <input name="title"> in the form region.
        assert page.locator(
            '[data-testid="workshop-edit-form"] input[name="title"]'
        ).count() == 0

        # Change status to published and save.
        page.select_option(
            '[data-testid="status-select"]', value='published',
        )
        page.locator('[data-testid="save-workshop-btn"]').click()
        page.wait_for_load_state('domcontentloaded')

        # Lands on detail page.
        assert page.url.rstrip('/').endswith(f'/studio/workshops/{ws.pk}')

        # Database reflects the change.
        ws.refresh_from_db()
        assert ws.status == 'published'

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 6: Edit form invariant rejection
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopEditInvariant:
    def test_invalid_recording_below_pages_is_rejected(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')

        ws = _create_workshop(
            slug='gated-ws', title='Gated Workshop',
            status='published',
            landing=0, pages=20, recording=20,
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{ws.pk}/edit',
            wait_until='domcontentloaded',
        )

        # Lower the recording gate below the pages gate — invalid.
        page.select_option(
            '[data-testid="recording-gate-select"]', value='10',
        )
        page.locator('[data-testid="save-workshop-btn"]').click()
        page.wait_for_load_state('domcontentloaded')

        # Still on the edit page (no redirect).
        assert page.url.rstrip('/').endswith(f'/studio/workshops/{ws.pk}/edit')

        # Inline error visible near the recording gate.
        err = page.locator('[data-testid="error-recording"]')
        assert err.count() == 1
        assert 'Recording gate must be at least' in err.first.inner_text()

        # DB unchanged.
        ws.refresh_from_db()
        assert ws.recording_required_level == 20

        connection.close()
        context.close()


# ---------------------------------------------------------------
# Scenario 7: Re-sync trigger
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopResync:
    def test_resync_button_redirects_to_sync_dashboard(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_workshops()
        _create_staff_user('admin@test.com')
        _create_workshop_source()
        _create_workshop(slug='resync-target', title='Resync Target')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        page.goto(
            f'{django_server}/studio/workshops/',
            wait_until='domcontentloaded',
        )

        # Patch async_task so we don't actually enqueue against django-q.
        with mock.patch(
            'django_q.tasks.async_task', return_value='task-e2e',
        ):
            page.locator('[data-testid="workshop-resync-btn"]').click()
            page.wait_for_load_state('domcontentloaded')

        # Lands on the sync dashboard.
        assert '/studio/sync/' in page.url

        # Flash mentions the queued workshop sync.
        assert 'Workshop sync queued' in page.content()

        connection.close()
        context.close()
