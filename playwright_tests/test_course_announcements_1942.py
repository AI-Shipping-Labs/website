"""Course announcements member and Studio journeys (issue #1942)."""

import datetime
import os
import re

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    VIEWPORT,
    create_session_for_user,
    create_staff_user,
    create_user,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

COURSE_SLUG = 'announcements-1942'


def _seed(*, with_announcement=False, extra=None):
    from accounts.models import User
    from content.models import Course, Enrollment
    from content.models.course_announcement import CourseAnnouncement

    create_staff_user('admin@test.com')
    create_user('premium@test.com', tier_slug='premium', first_name='Pat')
    create_user('free@test.com', tier_slug='free', first_name='Fay')
    staff = User.objects.get(email='admin@test.com')
    learner = User.objects.get(email='premium@test.com')
    course = Course.objects.create(
        title='Announcements course', slug=COURSE_SLUG,
        status='published', required_level=0,
    )
    Enrollment.objects.create(user=learner, course=course)
    announcement = None
    if with_announcement:
        announcement = CourseAnnouncement.objects.create(
            course=course,
            title='Week 2 office hours moved',
            body='**Tomorrow** we meet at 18:00 CET.',
            author=staff,
            status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )
    if extra:
        extra(course, staff, learner)
    connection.close()
    return course, announcement


def _page(browser, email=None):
    context = browser.new_context(viewport=VIEWPORT)
    cookies = [
        {
            'name': 'aslab_analytics_consent',
            'value': 'denied',
            'domain': '127.0.0.1',
            'path': '/',
        },
    ]
    if email:
        cookies += [
            {
                'name': 'sessionid',
                'value': create_session_for_user(email),
                'domain': '127.0.0.1',
                'path': '/',
            },
            {
                'name': 'csrftoken',
                'value': 'e2e-test-csrf-token-value',
                'domain': '127.0.0.1',
                'path': '/',
            },
        ]
    context.add_cookies(cookies)
    return context, context.new_page()


def _home(server):
    return f'{server}/courses/{COURSE_SLUG}/home'


@pytest.mark.core
@browser_journey
def test_enrolled_learner_reads_a_new_course_announcement(django_server, browser):
    _seed(with_announcement=True)
    context, page = _page(browser, 'premium@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    page.get_by_test_id('course-tab-announcements').click()
    expect(page).to_have_url(re.compile(r'/home/announcements'))
    card = page.get_by_test_id('course-announcement-card')
    expect(card).to_contain_text('Week 2 office hours moved')
    expect(card.locator('strong')).to_have_text('Tomorrow')
    expect(page.get_by_role('link', name='Home', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='Syllabus')).to_be_visible()
    context.close()


@pytest.mark.core
@browser_journey
def test_enrolled_learner_finds_empty_announcements_tab(django_server, browser):
    _seed()
    context, page = _page(browser, 'premium@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    page.get_by_role('link', name='Announcements').click()
    empty = page.get_by_test_id('course-announcements-empty')
    expect(empty).to_contain_text('No announcements yet')
    expect(empty).to_contain_text(
        'When the instructor posts an update, it will show up here.',
    )
    page.get_by_role('link', name='Home', exact=True).click()
    expect(page).to_have_url(re.compile(r'/courses/.+/home$'))
    context.close()


@pytest.mark.core
@browser_journey
def test_instructor_publishes_from_studio_and_learner_sees_it(django_server, browser):
    course, _announcement = _seed()
    context, page = _page(browser, 'admin@test.com')
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto(
        f'{django_server}/studio/courses/{course.pk}/edit',
        wait_until='domcontentloaded',
    )
    page.get_by_test_id('panel-manage-announcements').click()
    page.get_by_test_id('announcement-new').click()
    page.locator('input[name="title"]').fill('Project deadline extended')
    page.locator('textarea[name="body"]').fill('Submit by Friday.')
    page.get_by_test_id('announcement-save-draft').click()
    expect(page.get_by_test_id('announcement-row')).to_contain_text('Draft')

    learner_context, learner_page = _page(browser, 'premium@test.com')
    learner_page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    expect(learner_page.get_by_test_id('course-announcements-empty')).to_be_visible()
    learner_context.close()

    page.get_by_test_id('announcement-publish').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text(
        'Emails are being sent to enrolled learners',
    )

    learner_context, learner_page = _page(browser, 'premium@test.com')
    learner_page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    card = learner_page.get_by_test_id('course-announcement-card')
    expect(card).to_contain_text('Project deadline extended')
    expect(card).to_contain_text('Submit by Friday.')
    learner_context.close()
    context.close()


@pytest.mark.core
@browser_journey
def test_non_enrolled_member_cannot_find_course_announcements(django_server, browser):
    def extra(course, staff, _learner):
        from content.models.course_announcement import CourseAnnouncement

        CourseAnnouncement.objects.create(
            course=course, title='Enrolled only', body='Secret',
            author=staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )

    _seed(extra=extra)
    context, page = _page(browser, 'free@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    expect(page.get_by_test_id('course-tab-announcements')).to_have_count(0)
    response = page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    assert response is not None
    assert response.status == 404
    expect(page.get_by_text('Enrolled only')).to_have_count(0)
    context.close()


@pytest.mark.core
@browser_journey
def test_anonymous_visitor_is_asked_to_sign_in(django_server, browser):
    _seed(with_announcement=True)
    context, page = _page(browser)
    page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    expect(page).to_have_url(re.compile(r'/accounts/login/'))
    context.close()

    signed_in, page = _page(browser, 'premium@test.com')
    page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_test_id('course-announcement-card')).to_contain_text(
        'Week 2 office hours moved',
    )
    signed_in.close()


@pytest.mark.core
@browser_journey
def test_staff_previews_announcements_without_enrolling(django_server, browser):
    def extra(course, staff, _learner):
        from content.models.course_announcement import CourseAnnouncement

        CourseAnnouncement.objects.create(
            course=course, title='Staff preview', body='Readable.',
            author=staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )

    _seed(extra=extra)
    context, page = _page(browser, 'admin@test.com')
    page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    expect(page.get_by_test_id('course-tab-announcements')).to_be_visible()
    expect(page.get_by_test_id('course-announcement-card')).to_contain_text(
        'Staff preview',
    )
    context.close()


@pytest.mark.core
@browser_journey
def test_learner_sees_newest_announcement_first(django_server, browser):
    def extra(course, staff, _learner):
        from content.models.course_announcement import CourseAnnouncement

        now = timezone.now()
        CourseAnnouncement.objects.create(
            course=course, title='Older note', body='yesterday',
            author=staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=now - datetime.timedelta(days=1),
        )
        CourseAnnouncement.objects.create(
            course=course, title='Newer note', body='today',
            author=staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=now,
        )

    _seed(extra=extra)
    context, page = _page(browser, 'premium@test.com')
    page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    titles = page.get_by_test_id('course-announcement-card').locator('h3')
    expect(titles.nth(0)).to_have_text('Newer note')
    expect(titles.nth(1)).to_have_text('Older note')
    context.close()


@pytest.mark.core
@browser_journey
def test_instructor_edits_published_announcement_without_reemail_confirm(
    django_server, browser,
):
    def extra(course, staff, _learner):
        from content.models.course_announcement import CourseAnnouncement

        CourseAnnouncement.objects.create(
            course=course, title='Old title', body='Old body',
            author=staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
            email_queued_at=timezone.now(),
        )

    course, _announcement = _seed(extra=extra)
    from content.models.course_announcement import CourseAnnouncement

    row = CourseAnnouncement.objects.get(course=course, title='Old title')
    connection.close()
    context, page = _page(browser, 'admin@test.com')
    dialogs = []
    page.on('dialog', lambda dialog: dialogs.append(dialog.message) or dialog.dismiss())
    page.goto(
        f'{django_server}/studio/courses/{course.pk}/announcements/{row.pk}/edit',
        wait_until='domcontentloaded',
    )
    page.locator('input[name="title"]').fill('Updated title')
    page.locator('textarea[name="body"]').fill('Updated body')
    page.get_by_test_id('announcement-save').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text(
        'Announcement updated.',
    )
    assert dialogs == []
    context.close()

    learner_context, learner_page = _page(browser, 'premium@test.com')
    learner_page.goto(
        f'{django_server}/courses/{COURSE_SLUG}/home/announcements',
        wait_until='domcontentloaded',
    )
    card = learner_page.get_by_test_id('course-announcement-card')
    expect(card).to_contain_text('Updated title')
    expect(card).to_contain_text('Updated body')
    expect(card).not_to_contain_text('Old title')
    learner_context.close()
