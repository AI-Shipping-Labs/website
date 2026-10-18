"""Fixtures and screenshot helpers for issue #1980 browser journeys."""

from datetime import timedelta
from pathlib import Path

from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_user

SCREENSHOT_DIR = Path('.tmp/screenshots/self-paced-progress-1980')


def capture_responsive(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    viewports = (
        ('desktop', {'width': 1280, 'height': 900}),
        ('mobile-390', {'width': 390, 'height': 844}),
    )
    for label, viewport in viewports:
        page.set_viewport_size(viewport)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(
            path=str(SCREENSHOT_DIR / f'{name}-{label}.png'), full_page=True,
        )


def _course_and_user(label, buildcamp):
    from content.models import Course

    user = create_user(f'{label}-1980@test.com')
    course_slug = f'event-first-{label}-1980'
    if buildcamp:
        course_slug = 'ai-buildcamp'
    course = Course.objects.create(
        title=f'Event-first {label}', slug=course_slug,
        status='published', required_level=0,
    )
    return course, user


def _first_modules(course, buildcamp):
    from content.models import Module

    first = Module.objects.create(
        course=course, title='Foundations', slug='foundations', sort_order=1,
    )
    if not buildcamp:
        return first, first
    event_module = Module.objects.create(
        course=course, parent=first, title='Session 1', slug='session', sort_order=0,
    )
    lesson_module = Module.objects.create(
        course=course, parent=first, title='Week 1 Overview',
        slug='overview', sort_order=1,
    )
    return event_module, lesson_module


def _second_modules(course, first_event_module, buildcamp):
    from content.models import Module

    second = Module.objects.create(
        course=course, title='Build', slug='build', sort_order=2,
    )
    if not buildcamp:
        return first_event_module, second
    event_module = Module.objects.create(
        course=course, parent=second, title='Session 2', slug='session', sort_order=0,
    )
    lesson_module = Module.objects.create(
        course=course, parent=second, title='Testing and Evaluation Overview',
        slug='overview', sort_order=1,
    )
    return event_module, lesson_module


def _curriculum(course, buildcamp):
    from content.models import Unit

    first_event_module, first_lesson_module = _first_modules(course, buildcamp)
    event_first = Unit.objects.create(
        module=first_event_module, title='Session 1', slug='live-kickoff',
        sort_order=0, kind='event', session_position=1,
    )
    lesson_first = Unit.objects.create(
        module=first_lesson_module, title='First real lesson', slug='first-real-lesson',
        sort_order=1, kind='lesson', body='# First real lesson\n\nStart here.',
    )
    second_event_module, second_lesson_module = _second_modules(
        course, first_event_module, buildcamp,
    )
    event_middle = Unit.objects.create(
        module=second_event_module, title='Session 2', slug='live-review',
        sort_order=2, kind='event', session_position=2,
    )
    lesson_second = Unit.objects.create(
        module=second_lesson_module, title='Second real lesson',
        slug='second-real-lesson', sort_order=0, kind='lesson',
        body='# Second real lesson\n\nKeep building.',
    )
    return event_first, event_middle, lesson_first, lesson_second


def _enroll(user, course, *, dated, historical):
    from content.models import Cohort, CohortEnrollment, Enrollment

    self_paced = Cohort.objects.create(
        course=course, name='Self-paced', external_key='self-paced',
        mode='self_paced', is_active=True,
    )
    CohortEnrollment.objects.create(user=user, cohort=self_paced)
    if dated or historical:
        _enroll_dated(user, course, historical)
    Enrollment.objects.create(user=user, course=course)


def _enroll_dated(user, course, historical):
    from content.models import Cohort, CohortEnrollment

    today = timezone.localdate()
    start_date = today - timedelta(days=2)
    end_date = today + timedelta(days=20)
    if historical:
        start_date = today - timedelta(days=60)
        end_date = today - timedelta(days=30)
    cohort = Cohort.objects.create(
        course=course, name='Dated cohort', external_key='dated',
        mode='cohort', is_active=True,
        start_date=start_date, end_date=end_date,
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)


def seed_fixture(label, *, dated=False, historical=False, buildcamp=True):
    course, user = _course_and_user(label, buildcamp)
    event_first, event_middle, lesson_first, lesson_second = _curriculum(
        course, buildcamp,
    )
    _enroll(user, course, dated=dated, historical=historical)
    connection.close()
    return {
        'user': user, 'course': course, 'event_first': event_first,
        'event_middle': event_middle, 'lesson_first': lesson_first,
        'lesson_second': lesson_second,
    }


def context_page(browser, fixture):
    context = auth_context(browser, fixture['user'].email)
    return context, context.new_page()
