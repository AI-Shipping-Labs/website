"""Self-paced event-placeholder journeys for issue #1980."""

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.self_paced_progress_1980_helpers import (
    capture_responsive,
    context_page,
    seed_fixture,
)
from scripts.browser_journey_policy import browser_journey

pytestmark = [
    pytest.mark.core,
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]


def _assert_start_home(page, django_server, fixture):
    course = fixture['course']
    page.goto(f'{django_server}/courses/{course.slug}/home', wait_until='domcontentloaded')
    action = page.get_by_test_id('course-home-primary-action')
    expect(action).to_have_attribute('href', fixture['lesson_first'].get_absolute_url())
    expect(page.get_by_text(fixture['event_first'].title, exact=True)).to_have_count(0)
    capture_responsive(page, 'course-home')


def _assert_start_syllabus(page, django_server, fixture):
    course = fixture['course']
    page.goto(
        f'{django_server}/courses/{course.slug}/home/syllabus',
        wait_until='domcontentloaded',
    )
    page.get_by_test_id('syllabus-module-summary').first.click()
    lesson_url = fixture['lesson_first'].get_absolute_url()
    expect(page.locator(f'[data-syllabus-unit-row][href="{lesson_url}"]')).to_be_visible()
    event_url = fixture['event_first'].get_absolute_url()
    expect(page.locator(f'[data-syllabus-unit-row][href="{event_url}"]')).to_have_count(0)
    capture_responsive(page, 'syllabus')


def _assert_start_reader(page, django_server, fixture):
    lesson_url = fixture['lesson_first'].get_absolute_url()
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{django_server}{lesson_url}', wait_until='domcontentloaded')
    expect(page.get_by_test_id('bottom-prev-btn')).to_have_count(0)
    next_url = fixture['lesson_second'].get_absolute_url()
    expect(page.get_by_test_id('bottom-next-btn')).to_have_attribute('href', next_url)
    reader_url = page.url
    page.locator('[data-completion-toggle]').click()
    expect(page.locator('[data-completion-toggle]')).to_contain_text('Completed')
    expect(page).to_have_url(reader_url)
    expect(page.get_by_test_id('bottom-next-btn')).to_have_attribute('href', next_url)
    sidebar = page.locator('#sidebar-nav')
    expect(sidebar).to_be_visible()
    expect(sidebar.get_by_text(fixture['event_first'].title, exact=True)).to_have_count(0)
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.get_by_test_id('reader-mobile-progress-text')).to_have_text('Lesson 1 of 2')
    page.locator('#sidebar-toggle-btn').click()
    expect(sidebar).to_be_visible()
    expect(sidebar.get_by_text(fixture['event_first'].title, exact=True)).to_have_count(0)
    capture_responsive(page, 'reader')


def _assert_start_overview(page, django_server, fixture):
    course = fixture['course']
    page.goto(
        f'{django_server}/courses/{course.slug}?view=overview',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_role('link', name='Continue lesson')).to_have_attribute(
        'href', fixture['lesson_second'].get_absolute_url(),
    )
    capture_responsive(page, 'overview')


@browser_journey
def test_self_paced_learner_starts_with_real_material(django_server, browser):
    fixture = seed_fixture('start')
    context, page = context_page(browser, fixture)
    try:
        _assert_start_home(page, django_server, fixture)
        _assert_start_syllabus(page, django_server, fixture)
        _assert_start_reader(page, django_server, fixture)
        _assert_start_overview(page, django_server, fixture)
    finally:
        context.close()


@browser_journey
def test_self_paced_learner_finishes_without_empty_session(django_server, browser):
    from content.models import UserCourseProgress

    fixture = seed_fixture('finish')
    units = (fixture['lesson_first'], fixture['lesson_second'])
    UserCourseProgress.objects.bulk_create([
        UserCourseProgress(user=fixture['user'], unit=unit, completed_at=timezone.now())
        for unit in units
    ])
    connection.close()
    context, page = context_page(browser, fixture)
    try:
        _assert_finished_surfaces(page, django_server, fixture)
    finally:
        context.close()


def _assert_finished_surfaces(page, django_server, fixture):
    course = fixture['course']
    page.goto(f'{django_server}/courses/{course.slug}/home', wait_until='domcontentloaded')
    action = page.get_by_test_id('course-home-primary-action')
    expect(action).to_have_attribute('href', fixture['lesson_second'].get_absolute_url())
    expect(action).to_have_text('Next module: Build')
    expect(page.get_by_text(fixture['event_first'].title, exact=True)).to_have_count(0)
    capture_responsive(page, 'course-home-complete')
    page.goto(f'{django_server}/courses/{course.slug}?view=overview', wait_until='domcontentloaded')
    expect(page.get_by_test_id('continue-button')).to_have_count(0)
    page.goto(django_server, wait_until='domcontentloaded')
    expect(page.get_by_test_id('continue-learning-course')).to_have_count(0)
    capture_responsive(page, 'dashboard')


@browser_journey
def test_current_dated_learner_keeps_live_session_journey(django_server, browser):
    fixture = seed_fixture('dated', dated=True)
    context, page = context_page(browser, fixture)
    try:
        _assert_dated_syllabus(page, django_server, fixture)
        _assert_dated_reader(page, django_server, fixture)
    finally:
        context.close()


def _assert_dated_syllabus(page, django_server, fixture):
    course = fixture['course']
    page.goto(
        f'{django_server}/courses/{course.slug}/home/syllabus',
        wait_until='domcontentloaded',
    )
    page.get_by_test_id('syllabus-module-summary').first.click()
    event_url = fixture['event_first'].get_absolute_url()
    expect(page.locator(f'[data-syllabus-unit-row][href="{event_url}"]')).to_be_visible()


def _assert_dated_reader(page, django_server, fixture):
    lesson_url = fixture['lesson_first'].get_absolute_url()
    page.goto(f'{django_server}{lesson_url}', wait_until='domcontentloaded')
    expect(page.get_by_test_id('bottom-prev-btn')).to_have_attribute(
        'href', fixture['event_first'].get_absolute_url(),
    )
    expect(page.get_by_test_id('bottom-next-btn')).to_have_attribute(
        'href', fixture['event_middle'].get_absolute_url(),
    )
    sidebar = page.locator('#sidebar-nav')
    expect(sidebar.get_by_text(fixture['event_first'].title, exact=True)).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#sidebar-toggle-btn').click()
    expect(sidebar).to_be_visible()
    expect(sidebar.get_by_text(fixture['event_first'].title, exact=True)).to_be_visible()


@browser_journey
def test_returning_alumnus_follows_new_self_paced_path(django_server, browser):
    fixture = seed_fixture('alumnus', historical=True)
    context, page = context_page(browser, fixture)
    try:
        course = fixture['course']
        page.goto(
            f'{django_server}/courses/{course.slug}?view=overview',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_role('link', name='Continue lesson')).to_have_attribute(
            'href', fixture['lesson_first'].get_absolute_url(),
        )
        page.goto(
            f'{django_server}{fixture["lesson_first"].get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_test_id('bottom-prev-btn')).to_have_count(0)
        expect(page.get_by_test_id('bottom-next-btn')).to_have_attribute(
            'href', fixture['lesson_second'].get_absolute_url(),
        )
    finally:
        context.close()


@browser_journey
def test_authenticated_progress_api_matches_default_cohort(django_server, browser):
    from content.models import UserCourseProgress

    self_paced = seed_fixture('api-self-paced', buildcamp=False)
    dated = seed_fixture('api-dated', dated=True, buildcamp=False)
    for fixture in (self_paced, dated):
        UserCourseProgress.objects.create(
            user=fixture['user'], unit=fixture['lesson_first'],
            completed_at=timezone.now(),
        )
    connection.close()
    _assert_api_progress(django_server, browser, self_paced, dated)


def _assert_api_progress(django_server, browser, self_paced, dated):
    sp_context, sp_page = context_page(browser, self_paced)
    dated_context, dated_page = context_page(browser, dated)
    try:
        sp_page.goto(f'{django_server}/courses/{self_paced["course"].slug}/home')
        dated_page.goto(f'{django_server}/courses/{dated["course"].slug}/home')
        sp_url = f'{django_server}/api/courses/{self_paced["course"].slug}'
        dated_url = f'{django_server}/api/courses/{dated["course"].slug}'
        sp_data = sp_context.request.get(sp_url).json()
        dated_data = dated_context.request.get(dated_url).json()
        assert sp_data['progress'] == {'completed': 1, 'total': 2}
        assert dated_data['progress'] == {'completed': 1, 'total': 4}
        assert sp_data.keys() == dated_data.keys()
    finally:
        sp_context.close()
        dated_context.close()
