"""Course home entry, reader return, and responsive learner layout."""

import datetime
import os
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


@pytest.mark.core
@browser_journey
def test_course_home_entry_reader_return_and_mobile_themes(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Course, Enrollment, Module, Unit, UserCourseProgress

    user = create_user('course-home-browser@test.com')
    course = Course.objects.create(
        title='A practical engineering course with a long title',
        slug='course-home-browser', status='published', required_level=0,
        discussion_url='https://example.org/course-discussion',
    )
    first = Module.objects.create(
        course=course, title='Foundations and tools', slug='foundations',
        sort_order=1, available_after_days=0,
    )
    second = Module.objects.create(
        course=course, title='Build a useful system', slug='build-system',
        sort_order=2, available_after_days=7,
    )
    optional = Module.objects.create(
        course=course, title='Further experiments', slug='experiments',
        sort_order=3, is_bonus=True,
    )
    done = Unit.objects.create(module=first, title='Orientation', slug='orientation')
    next_unit = Unit.objects.create(
        module=first, title='Retrieval-augmented generation',
        slug='retrieval-augmented-generation', sort_order=1,
        body='Practice reading and building.',
    )
    Unit.objects.create(module=second, title='Ship a prototype', slug='ship-prototype')
    Unit.objects.create(module=optional, title='Try another model', slug='try-model')
    UserCourseProgress.objects.create(user=user, unit=done, completed_at=timezone.now())
    Enrollment.objects.create(user=user, course=course)
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name='Current study cohort', external_key='current-study',
        start_date=today - datetime.timedelta(days=16),
        end_date=today + datetime.timedelta(days=20),
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    connection.close()

    context = auth_context(browser, user.email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    page = context.new_page()
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(f'{django_server}/courses', wait_until='domcontentloaded')
    page.get_by_role('link', name=course.title).click()
    expect(page).to_have_url(f'{django_server}/courses/{course.slug}/home')
    expect(page.locator('[data-testid="course-home-focus"]')).to_contain_text(second.title)
    expect(page.locator('[data-testid="course-home-week"]')).to_contain_text('Cohort week 3')
    expect(page.locator('[data-testid="course-home-recommendation"]')).to_have_text(next_unit.title)
    expect(page.locator('[data-testid="course-home-position"]')).to_contain_text(
        'Next uncompleted course material',
    )
    screenshot_dir = Path('.tmp/screenshots/course-home-1783')
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_dir / 'desktop-light.png'), full_page=True)
    page.set_viewport_size({'width': 720, 'height': 450})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('[data-testid="course-home-open-lesson"]')).to_be_visible()
    assert page.locator('[data-testid="course-home-open-lesson"]').bounding_box()['y'] < 844
    assert page.locator('[data-testid="course-home-open-lesson"]').bounding_box()['height'] >= 44
    assert page.locator('[data-testid="course-home-help-links"] a').first.bounding_box()['height'] >= 44
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(screenshot_dir / 'mobile-light.png'), full_page=True)

    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('html')).to_have_class('dark')
    page.screenshot(path=str(screenshot_dir / 'mobile-dark.png'), full_page=True)
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.screenshot(path=str(screenshot_dir / 'desktop-dark.png'), full_page=True)

    help_links = page.get_by_role('navigation', name='Course help and links')
    expect(help_links.get_by_role('link', name='Course communication')).to_be_visible()

    page.locator('[data-testid="course-home-open-lesson"]').click()
    expect(page).to_have_url(f'{django_server}{next_unit.get_absolute_url()}?cohort=current-study')
    page.locator('[data-testid="reader-course-home"]').click()
    expect(page).to_have_url(f'{django_server}/courses/{course.slug}/home?cohort=current-study')
    page.get_by_role('navigation', name='Course pages').get_by_role(
        'link', name='Course materials',
    ).click()
    expect(page).to_have_url(f'{django_server}/courses/{course.slug}?cohort=current-study')
    context.close()
