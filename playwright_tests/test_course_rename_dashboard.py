"""Playwright regression test for issue #366.

Renaming a course's slug must NOT leave the dashboard's Continue
Learning widget rendering URLs against the OLD slug. The dashboard
walks ``Enrollment.course`` and ``UserCourseProgress.unit.module.course``
at render time, so a rename that updates the existing Course row
(rather than creating a new orphan) must surface immediately on the
next page load.

Setup mirrors a content sync rename (same ``content_id``, new
``slug``): we simulate it with a direct DB write, which is the
end state any sync-driven rename would produce.
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone


def _clear_course_data():
    from content.models import (
        Course,
        Enrollment,
        Module,
        Unit,
        UserCourseProgress,
    )
    UserCourseProgress.objects.all().delete()
    Enrollment.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _seed_in_progress_course(user, slug):
    """Create a 3-unit published course with the user enrolled + 1 unit done."""
    from content.models import (
        Course,
        Enrollment,
        Module,
        Unit,
        UserCourseProgress,
    )

    course = Course.objects.create(
        title='Python Fundamentals',
        slug=slug,
        status='published',
        required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Intro', slug='intro', sort_order=0,
    )
    units = [
        Unit.objects.create(
            module=module, title=f'Unit {i+1}',
            slug=f'unit-{i+1}', sort_order=i, body='Body.',
        )
        for i in range(3)
    ]
    Enrollment.objects.create(user=user, course=course)
    UserCourseProgress.objects.create(
        user=user, unit=units[0], completed_at=timezone.now(),
    )
    connection.close()
    return course


@pytest.mark.django_db(transaction=True)
class TestCourseRenameDashboardLink:
    """Dashboard's Continue link tracks the live slug after rename."""

    def test_dashboard_link_follows_renamed_slug(
        self, django_server, browser,
    ):
        """A user enrolled in a course at slug A sees the dashboard's
        Continue link rebuild against slug B after the course is
        renamed in the DB (mirrors the sync-pipeline rename path).
        """
        _clear_course_data()
        user = _create_user('main@test.com', tier_slug='main')
        course = _seed_in_progress_course(user, slug='python-course')

        context = _auth_context(browser, 'main@test.com')
        page = context.new_page()

        # Step 1: navigate to dashboard, confirm link points at OLD slug.
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        learning = page.locator(
            'section:has(h2:has-text("Continue Learning"))',
        )
        learning_html = learning.inner_html()
        assert '/courses/python-course/' in learning_html, (
            f'Expected /courses/python-course/ in dashboard, got: {learning_html}'
        )

        # Step 2: rename the course in the DB (simulates a content-repo
        # rename run through the sync pipeline). The Course pk and the
        # Enrollment FK are unchanged — only ``slug`` flips.
        course.slug = 'python'
        course.save(update_fields=['slug'])
        connection.close()

        # Step 3: reload the dashboard.
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        learning = page.locator(
            'section:has(h2:has-text("Continue Learning"))',
        )
        learning_html = learning.inner_html()
        # Continue link rebuilds against the NEW slug.
        assert '/courses/python/' in learning_html, (
            f'Expected /courses/python/ after rename, got: {learning_html}'
        )
        # And the OLD slug is gone (no broken legacy link).
        assert '/courses/python-course/' not in learning_html, (
            f'Old slug still appears after rename: {learning_html}'
        )

        # Step 4: click Continue and confirm the page resolves at the
        # new URL (200, not 404).
        continue_link = learning.locator('a:has-text("Continue")').first
        continue_link.click()
        page.wait_for_load_state('domcontentloaded')
        assert '/courses/python/' in page.url, (
            f'Continue did not navigate to new slug; landed at {page.url}'
        )
        # Body has the unit content (proxy for "page resolved successfully").
        assert page.locator('body').inner_text(), 'Empty page body'

        context.close()
