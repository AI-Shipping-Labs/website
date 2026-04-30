"""Playwright coverage for Studio course/workshop provenance UI (#397)."""

import datetime
import os
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
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _reset_content():
    from content.models import Course, Module, Unit, Workshop, WorkshopPage

    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    connection.close()


def _create_course_pair():
    from content.models import Course, Module, Unit

    synced = Course.objects.create(
        title='Synced Origin Course',
        slug='synced-origin-course',
        status='published',
        source_repo='AI-Shipping-Labs/content',
        source_path='courses/synced-origin/course.yaml',
        source_commit='abc1234def5678901234567890123456789abcde',
        content_id=uuid.uuid4(),
    )
    module = Module.objects.create(
        course=synced,
        title='Synced Module',
        slug='synced-module',
        sort_order=1,
        source_repo='AI-Shipping-Labs/content',
        source_path='courses/synced-origin/synced-module/README.md',
    )
    Unit.objects.create(
        module=module,
        title='Synced Unit',
        slug='synced-unit',
        sort_order=1,
        source_repo='AI-Shipping-Labs/content',
        source_path='courses/synced-origin/synced-module/synced-unit.md',
    )
    Unit.objects.create(
        module=module,
        title='Local Unit',
        slug='local-unit',
        sort_order=2,
    )
    local = Course.objects.create(
        title='Local Origin Course',
        slug='local-origin-course',
        status='draft',
    )
    connection.close()
    return synced, local


def _create_workshop_pair():
    from content.models import Workshop, WorkshopPage

    synced = Workshop.objects.create(
        slug='synced-origin-workshop',
        title='Synced Origin Workshop',
        date=datetime.date(2026, 4, 21),
        description='Synced workshop.',
        status='published',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        source_repo='AI-Shipping-Labs/workshops-content',
        source_path='2026/synced-origin-workshop/workshop.yaml',
        source_commit='def1234def5678901234567890123456789abcde',
        content_id=uuid.uuid4(),
    )
    WorkshopPage.objects.create(
        workshop=synced,
        slug='setup',
        title='Setup',
        sort_order=1,
        body='# Setup',
        source_repo='AI-Shipping-Labs/workshops-content',
        source_path='2026/synced-origin-workshop/setup.md',
    )
    local = Workshop.objects.create(
        slug='local-origin-workshop',
        title='Local Origin Workshop',
        date=datetime.date(2026, 4, 22),
        description='Local workshop.',
        status='draft',
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
    )
    WorkshopPage.objects.create(
        workshop=local,
        slug='notes',
        title='Local Notes',
        sort_order=1,
        body='# Notes',
    )
    connection.close()
    return synced, local


@pytest.mark.django_db(transaction=True)
class TestStudioCourseOrigin:
    def test_staff_can_scan_courses_and_trace_unit_source(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_content()
        _create_staff_user('origin-staff@test.com')
        synced, local = _create_course_pair()

        context = _auth_context(browser, 'origin-staff@test.com')
        page = context.new_page()

        page.goto(f'{django_server}/studio/courses/', wait_until='domcontentloaded')
        body = page.content()
        assert 'Synced Origin Course' in body
        assert 'courses/synced-origin/course.yaml' in body
        assert 'Local Origin Course' in body
        assert 'Local / manual' in body
        assert 'No GitHub source metadata' in body
        assert page.locator(
            'tr:has-text("Synced Origin Course") a:has-text("View")'
        ).count() >= 1
        assert page.locator(
            'tr:has-text("Local Origin Course") a:has-text("Edit")'
        ).count() >= 1

        page.goto(
            f'{django_server}/studio/courses/{synced.pk}/edit',
            wait_until='domcontentloaded',
        )
        detail = page.content()
        assert 'Synced from GitHub' in detail
        assert 'abc1234def5678901234567890123456789abcde' in detail
        assert 'Re-sync source' in detail
        assert 'courses/synced-origin/synced-module/README.md' in detail
        assert 'courses/synced-origin/synced-module/synced-unit.md' in detail
        assert 'Local Unit' in detail

        page.goto(
            f'{django_server}/studio/courses/{local.pk}/edit',
            wait_until='domcontentloaded',
        )
        local_detail = page.content()
        assert 'Local / manual' in local_detail
        assert 'Re-sync source' not in local_detail
        assert 'Edit on GitHub' not in local_detail

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopOrigin:
    def test_staff_can_trace_workshop_and_page_sources(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_content()
        _create_staff_user('workshop-origin-staff@test.com')
        synced, local = _create_workshop_pair()

        context = _auth_context(browser, 'workshop-origin-staff@test.com')
        page = context.new_page()

        page.goto(f'{django_server}/studio/workshops/', wait_until='domcontentloaded')
        body = page.content()
        assert 'Synced Origin Workshop' in body
        assert '2026/synced-origin-workshop/workshop.yaml' in body
        assert 'Local Origin Workshop' in body
        assert 'Local / manual' in body

        page.goto(
            f'{django_server}/studio/workshops/{synced.pk}/',
            wait_until='domcontentloaded',
        )
        detail = page.content()
        assert 'Synced from GitHub' in detail
        assert 'def1234def5678901234567890123456789abcde' in detail
        assert '2026/synced-origin-workshop/setup.md' in detail
        assert (
            'https://github.com/AI-Shipping-Labs/workshops-content/'
            'blob/main/2026/synced-origin-workshop/setup.md'
        ) in detail

        page.goto(
            f'{django_server}/studio/workshops/{local.pk}/',
            wait_until='domcontentloaded',
        )
        local_detail = page.content()
        assert 'Local / manual' in local_detail
        assert 'No GitHub source metadata' in local_detail
        assert 'Edit on GitHub' not in local_detail

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioOriginAccess:
    def test_non_staff_cannot_inspect_source_metadata(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_content()
        _create_user('member-origin@test.com')
        synced, _local = _create_course_pair()

        context = _auth_context(browser, 'member-origin@test.com')
        page = context.new_page()

        response = page.goto(
            f'{django_server}/studio/courses/{synced.pk}/edit',
            wait_until='domcontentloaded',
        )
        body = page.content()

        assert response.status == 403
        assert 'AI-Shipping-Labs/content' not in body
        assert 'courses/synced-origin/course.yaml' not in body
        assert 'abc1234def5678901234567890123456789abcde' not in body

        context.close()
