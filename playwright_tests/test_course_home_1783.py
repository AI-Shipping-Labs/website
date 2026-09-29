"""Course home: the Current module card, next session, and checklist."""

import datetime
import os

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
def test_learner_uses_current_module_card_and_next_session(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Course, Enrollment, Module, Unit, UserCourseProgress
    from events.models import Event, EventSeries

    user = create_user('course-home-module-card@test.com')
    course = Course.objects.create(
        title='A practical engineering course with a long title',
        slug='course-home-module-card', status='published', required_level=0,
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
    third = Module.objects.create(
        course=course, title='Agentic flows', slug='agentic-flows',
        sort_order=3, available_after_days=14,
    )
    done = Unit.objects.create(module=first, title='Orientation', slug='orientation')
    current_unit = Unit.objects.create(module=second, title='Ship a prototype', slug='ship-prototype')
    Unit.objects.create(
        module=second, title='Session 2', slug='session', kind='event',
        sort_order=2, session_position=2,
    )
    Unit.objects.create(
        module=third, title='Session 3', slug='session', kind='event',
        sort_order=2, session_position=3,
    )
    UserCourseProgress.objects.create(user=user, unit=done, completed_at=timezone.now())
    Enrollment.objects.create(user=user, course=course)
    today = timezone.localdate()
    series = EventSeries.objects.create(name='Module card sessions', slug='module-card-sessions')
    cohort = Cohort.objects.create(
        course=course, name='Current study cohort', external_key='current-study',
        start_date=today - datetime.timedelta(days=9),
        end_date=today + datetime.timedelta(days=20),
        event_series=series,
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    now = timezone.now()
    Event.objects.create(
        event_series=series, slug='module-card-session-2', title='Second session event',
        status='completed', series_position=2,
        start_datetime=now - datetime.timedelta(days=1),
        end_datetime=now - datetime.timedelta(days=1) + datetime.timedelta(hours=1),
    )
    Event.objects.create(
        event_series=series, slug='module-card-session-3', title='Third session event',
        status='upcoming', series_position=3,
        start_datetime=now + datetime.timedelta(days=6),
        end_datetime=now + datetime.timedelta(days=6, hours=1),
        description='Bring questions about the week.',
    )
    connection.close()

    context = auth_context(browser, user.email)
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'domain': '127.0.0.1', 'path': '/',
    }])
    page = context.new_page()
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(f'{django_server}/courses', wait_until='domcontentloaded')
    page.get_by_role('link', name=course.title).click()
    expect(page).to_have_url(f'{django_server}/courses/{course.slug}/home')

    search = page.locator('#course-home-syllabus-search')
    card = page.get_by_test_id('course-home-current-module')
    next_session = page.get_by_test_id('course-home-next-session')
    expect(search).to_be_visible()
    # Search comes first, then the module card, then the next session.
    assert search.bounding_box()['y'] < card.bounding_box()['y'] < next_session.bounding_box()['y']
    expect(card.get_by_test_id('course-home-current-module-link')).to_have_text(second.title)
    expect(card.get_by_test_id('course-home-live-session-title')).to_have_text('Session 2')
    expect(card).to_contain_text('No recap yet')
    expect(next_session.get_by_test_id('course-home-next-session-module')).to_have_text(
        'Week 3 · Agentic flows',
    )
    expect(next_session.get_by_role('link', name='Open session')).to_be_visible()
    expect(page.get_by_role('navigation', name='Course help and links')).to_have_count(0)
    # Design system: the primary action follows the progress in one vertical
    # order, never pinned beside it in a shared flex row, even at desktop width.
    layout = card.evaluate("""card => {
        const progress = card.querySelector('[data-testid="course-home-module-progress-block"]');
        const action = card.querySelector('[data-testid="course-home-primary-action"]');
        const row = action.parentElement;
        return {
            follows: Boolean(progress.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING),
            sharesFlexRow: row.contains(progress) && getComputedStyle(row).display.includes('flex'),
            below: action.getBoundingClientRect().top >= progress.getBoundingClientRect().bottom,
        };
    }""")
    assert layout == {'follows': True, 'sharesFlexRow': False, 'below': True}

    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    expect(card.get_by_test_id('course-home-primary-action')).to_be_visible()

    card.get_by_role('link', name='Continue lesson').click()
    expect(page).to_have_url(f'{django_server}{current_unit.get_absolute_url()}?cohort=current-study')
    page.locator('[data-testid="reader-course-home"]').click()
    expect(page).to_have_url(f'{django_server}/courses/{course.slug}/home?cohort=current-study')
    context.close()


@pytest.mark.core
@browser_journey
def test_course_home_checklist_skip_updates_in_place(django_server, browser):
    from content.models import Course, Enrollment, Module, Unit

    user = create_user('course-home-skip@test.com')
    course = Course.objects.create(
        title='Checklist skip course', slug='course-home-skip',
        status='published', required_level=0,
    )
    module = Module.objects.create(
        course=course, title='First steps', slug='first-steps', sort_order=1,
    )
    Unit.objects.create(module=module, title='First lesson', slug='first-lesson', sort_order=1)
    Unit.objects.create(
        module=module, title='First homework', slug='first-homework',
        sort_order=2, kind='homework',
    )
    Enrollment.objects.create(user=user, course=course)
    connection.close()

    context = auth_context(browser, user.email)
    page = context.new_page()
    page.goto(f'{django_server}/courses/{course.slug}/home', wait_until='domcontentloaded')
    progress = page.locator('[data-testid="course-checklist-progress-copy"]')
    expect(progress).to_contain_text('0 of 2 done')

    # A marker on window survives only if the page is not reloaded.
    page.evaluate('window.__checklistNoReload = true')
    page.locator('[data-testid="course-checklist-skip-lesson"]').click()

    row = page.locator('[data-testid="course-checklist-item-lesson"]')
    expect(row).to_contain_text('Skipped')
    expect(progress).to_contain_text('1 of 2 done')
    expect(page.locator('[data-testid="course-checklist-skip-homework"]')).to_be_focused()
    assert page.evaluate('window.__checklistNoReload === true')
    context.close()
