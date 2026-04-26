"""Playwright E2E tests for malformed YAML / frontmatter sync surfacing
(issue #286).

Scenarios:

1. Staff sees a parse error in the sync history after a malformed
   ``course.yaml`` is synced.
2. A previously-synced course survives a malformed yaml push (no
   soft-delete).
3. A broken module yaml does not stop other modules in the same course
   from syncing — the Studio sync history shows just one error entry.

Scenario 4 (two sources sharing a repo_name) was removed in issue #310;
``repo_name`` is now globally UNIQUE.
"""

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
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.db import connection  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_state():
    """Clear sync-affected fixtures so each test starts fresh."""
    from content.models import Course, Module, Unit
    from events.models.event import Event
    from integrations.models import ContentSource, SyncLog

    SyncLog.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Event.objects.all().delete()
    ContentSource.objects.all().delete()
    connection.close()


def _make_course_source():
    from integrations.models import ContentSource

    src = ContentSource.objects.create(
        repo_name='AI-Shipping-Labs/courses',
    )
    connection.close()
    return src


def _run_sync_from_disk(source, repo_dir):
    """Drive a sync from a local disk repo dir without going through GitHub."""
    from integrations.services.github import sync_content_source

    sync_log = sync_content_source(source, repo_dir=repo_dir)
    connection.close()
    return sync_log


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w') as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Scenario 1: Staff sees a parse error in the sync history
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMalformedCourseYamlSurfacesInHistory:
    def test_history_page_shows_parse_error(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')

        source = _make_course_source()

        repo_dir = tempfile.mkdtemp(prefix='e2e-malformed-yaml-')
        try:
            _write(
                repo_dir,
                'machine-learning-zoomcamp/course.yaml',
                'title: [[[invalid\n',
            )
            _run_sync_from_disk(source, repo_dir)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()

        # The sync history page lists batches; expand the first batch and
        # look for the parse error string.
        page.goto(
            f'{django_server}/studio/sync/history/',
            wait_until='domcontentloaded',
        )
        # First batch header is clickable to reveal detail (and errors).
        page.locator('.batch-header').first.click()
        page.wait_for_load_state('domcontentloaded')

        body = page.content()
        # The errors panel renders ``{{ error.file }}: {{ error.error }}``.
        assert 'course.yaml' in body, (
            'Expected course.yaml to appear in the errors panel'
        )
        # The new format includes ``Failed to parse course.yaml`` — assert
        # we're seeing a human-readable message, not a raw traceback line
        # like ``Traceback (most recent call last)``.
        assert 'Failed to parse course.yaml' in body or (
            'expected a mapping' in body
        ), (
            'Expected Failed-to-parse or expected-a-mapping wording '
            'in the rendered errors panel'
        )
        assert 'Traceback' not in body, (
            'Errors panel must not render Python tracebacks'
        )

        connection.close()
        context.close()


# ---------------------------------------------------------------------------
# Scenario 2: Previously-synced course survives a malformed yaml push
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPreviouslySyncedCourseSurvivesMalformedYaml:
    def test_course_remains_visible_after_malformed_resync(
        self, django_server, browser,
    ):
        from content.models import Course

        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        source = _make_course_source()

        # Step 1: sync a valid course so it lands in the DB.
        repo_dir = tempfile.mkdtemp(prefix='e2e-good-course-')
        try:
            cid = str(uuid.uuid4())
            _write(
                repo_dir,
                'machine-learning-zoomcamp/course.yaml',
                (
                    'title: "Machine Learning Zoomcamp"\n'
                    'slug: "machine-learning-zoomcamp"\n'
                    'description: "Free course on ML."\n'
                    f'content_id: "{cid}"\n'
                ),
            )
            _run_sync_from_disk(source, repo_dir)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

        assert Course.objects.filter(
            slug='machine-learning-zoomcamp',
        ).exists()
        connection.close()

        # Step 2: re-sync with a malformed course.yaml.
        repo_dir = tempfile.mkdtemp(prefix='e2e-bad-course-')
        try:
            _write(
                repo_dir,
                'machine-learning-zoomcamp/course.yaml',
                'title: [[[invalid\n',
            )
            sync_log = _run_sync_from_disk(source, repo_dir)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

        # The sync recorded errors but did not soft-delete the course.
        assert sync_log.errors, (
            f'Expected errors recorded; got {sync_log.errors!r}'
        )
        course = Course.objects.get(slug='machine-learning-zoomcamp')
        assert course.status == 'published', (
            'A malformed re-sync must not soft-delete the previously '
            'synced course'
        )
        connection.close()

        # Step 3: visit the public course page as anonymous; it loads.
        page = browser.new_page()
        page.goto(
            f'{django_server}/courses/machine-learning-zoomcamp',
            wait_until='domcontentloaded',
        )
        # 200 OK and the title is rendered.
        assert page.url.rstrip('/').endswith('machine-learning-zoomcamp')
        assert 'Machine Learning Zoomcamp' in page.content()

        # Step 4: course is still listed on /courses.
        page.goto(
            f'{django_server}/courses',
            wait_until='domcontentloaded',
        )
        assert 'Machine Learning Zoomcamp' in page.content()

        connection.close()
        page.close()


# ---------------------------------------------------------------------------
# Scenario 3: A broken module yaml does not stop other modules from syncing
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestBrokenModuleYamlIsolated:
    def test_other_modules_still_sync(self, django_server, browser):
        from content.models import Module

        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        source = _make_course_source()

        repo_dir = tempfile.mkdtemp(prefix='e2e-broken-module-')
        try:
            cid = str(uuid.uuid4())
            _write(
                repo_dir,
                'python-course/course.yaml',
                (
                    'title: "Python Course"\n'
                    'slug: "python-course"\n'
                    'description: "Learn Python"\n'
                    f'content_id: "{cid}"\n'
                ),
            )
            # Module 1: valid.
            _write(
                repo_dir,
                'python-course/01-fundamentals/module.yaml',
                'title: "Fundamentals"\nsort_order: 1\n',
            )
            unit_cid = str(uuid.uuid4())
            _write(
                repo_dir,
                'python-course/01-fundamentals/01-intro.md',
                (
                    '---\n'
                    'title: "Intro"\n'
                    'sort_order: 1\n'
                    f'content_id: "{unit_cid}"\n'
                    '---\n'
                    'Body.\n'
                ),
            )
            # Module 2: malformed module.yaml.
            _write(
                repo_dir,
                'python-course/02-broken/module.yaml',
                'title: [[[invalid\n',
            )
            # Module 3: valid.
            _write(
                repo_dir,
                'python-course/03-advanced/module.yaml',
                'title: "Advanced"\nsort_order: 3\n',
            )
            sync_log = _run_sync_from_disk(source, repo_dir)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

        # Only modules 1 and 3 made it to the DB.
        slugs = sorted(Module.objects.values_list('slug', flat=True))
        assert 'fundamentals' in slugs
        assert 'advanced' in slugs
        assert 'broken' not in slugs
        # Exactly one parse error pointing at 02-broken/module.yaml.
        broken = [
            e for e in sync_log.errors
            if 'module.yaml' in e.get('file', '')
            and '02-broken' in e.get('file', '')
        ]
        assert len(broken) == 1, (
            f'Expected exactly one error for 02-broken/module.yaml; '
            f'got {sync_log.errors!r}'
        )
        connection.close()

        # The Studio sync history page surfaces the same error.
        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        page.goto(
            f'{django_server}/studio/sync/history/',
            wait_until='domcontentloaded',
        )
        page.locator('.batch-header').first.click()
        page.wait_for_load_state('domcontentloaded')
        body = page.content()
        assert '02-broken' in body
        assert 'module.yaml' in body

        connection.close()
        context.close()


# Scenario 4 (TestRegisterTwoSourcesDifferentPaths) was deleted as part of
# issue #310. Two ContentSource rows can no longer share a ``repo_name`` —
# the new schema enforces ``repo_name`` UNIQUE — so the behaviour the test
# exercised is gone. The replacement uniqueness assertion lives in the
# Django integration tests for ContentSource.
