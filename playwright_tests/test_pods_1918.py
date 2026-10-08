"""Pods phase 1 member and operator journeys (issue #1918).

Fixtures: a published course enabled in ``PODS_COURSE_SLUGS`` (DB
IntegrationSetting) with a dated ``Cohort 4`` (``external_key=4``); enrolled
anna (Europe/Berlin, Main), raj (Asia/Kolkata, Main), mike
(America/New_York, Main), basic (enrolled, Basic level with course access);
non-enrolled main; staff admin.
"""

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

COURSE_SLUG = 'pods-buildcamp'
WEEKDAY_EVENINGS = [(day, 18 * 60, 21 * 60, 'preferred') for day in range(5)]


def _set_config(key, value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    if value is None:
        IntegrationSetting.objects.filter(key=key).delete()
    else:
        IntegrationSetting.objects.update_or_create(key=key, defaults={'value': value})
    clear_config_cache()
    connection.close()


def _seed(enabled=True):
    """Create the course, Cohort 4, and the people from the issue fixtures."""
    from accounts.models import User
    from content.models import Cohort, CohortEnrollment, Course, CourseAccess

    create_staff_user('admin@test.com')
    people = {
        'anna': ('anna@test.com', 'main', 'Anna', 'Klein', 'Europe/Berlin'),
        'raj': ('raj@test.com', 'main', 'Raj', 'Shah', 'Asia/Kolkata'),
        'mike': ('mike@test.com', 'main', 'Mike', 'Kay', 'America/New_York'),
        'basic': ('basic@test.com', 'basic', 'Bea', 'Sic', ''),
        'main': ('main@test.com', 'main', 'Max', 'Out', ''),
    }
    users = {}
    for key, (email, tier, first, last, tz) in people.items():
        create_user(email, tier_slug=tier, first_name=first)
        user = User.objects.get(email=email)
        user.last_name = last
        user.preferred_timezone = tz
        user.save(update_fields=['last_name', 'preferred_timezone'])
        users[key] = user
    course = Course.objects.create(
        title='Pods Buildcamp', slug=COURSE_SLUG, status='published', required_level=20,
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='4', mode='cohort',
        start_date=today - datetime.timedelta(days=7),
        end_date=today + datetime.timedelta(days=50),
    )
    for key in ('anna', 'raj', 'mike', 'basic'):
        CohortEnrollment.objects.create(user=users[key], cohort=cohort)
    CourseAccess.objects.create(user=users['basic'], course=course, access_type='granted')
    connection.close()
    _set_config('PODS_COURSE_SLUGS', COURSE_SLUG if enabled else None)
    return course, cohort, users


def _pod(cohort, name, members, owner=None, max_members=4, purpose=None):
    from pods.models import Pod, PodMembership

    pod = Pod.objects.create(
        cohort=cohort, name=name, purpose=purpose or f'{name} meets weekly to ship.',
        max_members=max_members, owner=owner, source='studio',
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    connection.close()
    return pod


def _windows(user, zone, windows):
    from pods.models import AvailabilityProfile, AvailabilityWindow

    profile, _ = AvailabilityProfile.objects.update_or_create(user=user, defaults={'timezone': zone})
    profile.windows.all().delete()
    for weekday, start, end, preference in windows:
        AvailabilityWindow.objects.create(
            profile=profile, weekday=weekday, start_minute=start, end_minute=end, preference=preference,
        )
    connection.close()


def _page(browser, email=None, timezone_id=None):
    options = {'viewport': VIEWPORT}
    if timezone_id:
        options['timezone_id'] = timezone_id
    context = browser.new_context(**options)
    cookies = [{'name': 'aslab_analytics_consent', 'value': 'denied', 'domain': '127.0.0.1', 'path': '/'}]
    if email:
        cookies += [
            {'name': 'sessionid', 'value': create_session_for_user(email), 'domain': '127.0.0.1', 'path': '/'},
            {'name': 'csrftoken', 'value': 'e2e-test-csrf-token-value', 'domain': '127.0.0.1', 'path': '/'},
        ]
    context.add_cookies(cookies)
    return context, context.new_page()


def _home(server):
    return f'{server}/courses/{COURSE_SLUG}/home'


def _pod_url(server, pod):
    return f'{server}/courses/{COURSE_SLUG}/home/pods/{pod.pk}'


@pytest.mark.core
@browser_journey
def test_buildcamp_student_starts_a_pod_of_one(django_server, browser):
    _seed()
    context, page = _page(browser, 'anna@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    page.get_by_test_id('course-tab-pods').click()
    expect(page).to_have_url(re.compile(r'/home/pods\?cohort=4$'))
    empty = page.get_by_test_id('pods-empty')
    expect(empty).to_contain_text('No pods yet')
    empty.get_by_role('link', name='Start a pod').click()

    page.get_by_label('Name').fill('RAG evals study group')
    page.get_by_label('Purpose').fill('Weekly review of our eval notebooks')
    page.get_by_role('button', name='Start pod').click()

    expect(page.get_by_test_id('pod-name')).to_have_text('RAG evals study group')
    expect(page.get_by_test_id('pod-meta')).to_contain_text('1 of 4 members')
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Your pod')
    expect(page.get_by_test_id('messages-region')).to_contain_text(
        'Pod started. Add your availability so others can see if they fit.',
    )
    expect(page.get_by_test_id('pod-times-needs-more')).to_contain_text('at least two members')

    page.get_by_test_id('pod-back').click()
    first_row = page.get_by_test_id('pod-row').first
    expect(first_row.get_by_test_id('pod-row-name')).to_have_text('RAG evals study group')
    expect(first_row.get_by_test_id('pod-row-badge')).to_have_text('Your pod')
    expect(first_row.get_by_test_id('pod-row-action')).to_have_text('Open pod')
    context.close()


@browser_journey
def test_student_adds_weekly_availability_in_her_own_timezone(django_server, browser):
    _seed()
    from accounts.models import User

    User.objects.filter(email='anna@test.com').update(preferred_timezone='')
    connection.close()
    context, page = _page(browser, 'anna@test.com', timezone_id='Europe/Berlin')
    page.goto(f'{_home(django_server)}/pods?cohort=4', wait_until='domcontentloaded')
    page.get_by_role('link', name='Edit my availability').first.click()
    expect(page.get_by_test_id('availability-timezone-line')).to_contain_text('Europe/Berlin')

    page.get_by_test_id('availability-preset-weekday-evenings').click()
    friday = page.get_by_test_id('availability-day').nth(4)
    friday.get_by_test_id('availability-window-preference').select_option('if_needed')
    page.get_by_test_id('availability-save').click()

    expect(page.get_by_test_id('messages-region')).to_contain_text('Availability saved.')
    days = page.get_by_test_id('availability-summary-day')
    expect(days).to_have_count(5)
    expect(days.nth(0)).to_contain_text('Monday')
    expect(days.nth(0)).to_contain_text('18:00-21:00')
    expect(days.nth(4)).to_contain_text('18:00-21:00 (If needed)')

    tuesday = page.get_by_test_id('availability-day').nth(1)
    tuesday.get_by_test_id('availability-add-window').click()
    new_row = tuesday.get_by_test_id('availability-window').last
    new_row.get_by_test_id('availability-window-start').fill('20:00')
    new_row.get_by_test_id('availability-window-end').fill('22:00')
    page.get_by_test_id('availability-save').click()
    expect(page.get_by_test_id('availability-error')).to_have_text(
        'Windows on Tuesday overlap. Merge them into one window.',
    )
    expect(page.get_by_test_id('availability-summary-day')).to_have_count(5)
    context.close()


@browser_journey
def test_student_finds_a_pod_that_fits_and_requests_to_join(django_server, browser):
    _course, cohort, users = _seed()
    from notifications.models import Notification

    _windows(users['anna'], 'Europe/Berlin', WEEKDAY_EVENINGS)
    _windows(users['raj'], 'Europe/Berlin', [(1, 18 * 60, 21 * 60, 'preferred'), (3, 18 * 60, 21 * 60, 'preferred')])
    _windows(users['mike'], 'Asia/Tokyo', [(1, 6 * 60, 8 * 60, 'preferred')])
    _pod(cohort, 'Morning crew', [users['mike']], owner=users['mike'])
    evening = _pod(cohort, 'Evening builders', [users['raj']], owner=users['raj'])
    from pods.models import Pod

    Pod.objects.filter(pk=evening.pk).update(created_at=timezone.now() - datetime.timedelta(hours=1))
    connection.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{_home(django_server)}/pods?cohort=4', wait_until='domcontentloaded')
    names = page.get_by_test_id('pod-row-name')
    expect(names).to_have_text(['Evening builders', 'Morning crew'])
    rows = page.get_by_test_id('pod-row')
    expect(rows.nth(0).get_by_test_id('pod-row-fit')).to_have_text(re.compile(r'Overlaps with you [1-9]\d* h a week'))
    expect(rows.nth(1).get_by_test_id('pod-row-fit')).to_have_text('No overlap with you yet')

    rows.nth(0).get_by_role('link', name='Request to join').click()
    page.get_by_label('Why this pod? What are you working on?').fill('I am building a RAG eval harness')
    page.get_by_test_id('pod-request-submit').click()
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Request sent')
    expect(page.get_by_role('button', name='Withdraw request')).to_be_visible()
    assert Notification.objects.filter(
        user=users['raj'], read=False, title='New request to join Evening builders',
    ).exists()
    connection.close()
    context.close()


@pytest.mark.core
@browser_journey
def test_pod_owner_reviews_a_request_with_fit_and_approves_it(django_server, browser):
    _course, cohort, users = _seed()
    from pods.services.membership import request_to_join

    for key in ('raj', 'mike'):
        _windows(users[key], 'Europe/Berlin', [(1, 18 * 60, 21 * 60, 'preferred'), (3, 18 * 60, 21 * 60, 'preferred')])
    _windows(users['anna'], 'Europe/Berlin', [(1, 18 * 60, 21 * 60, 'preferred')])
    pod = _pod(cohort, 'Evening builders', [users['raj'], users['mike']], owner=users['raj'])
    request_to_join(pod, users['anna'], 'I am building a RAG eval harness')
    connection.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    expect(page.get_by_test_id('course-tab-pods-count')).to_have_text('1')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    request_row = page.get_by_test_id('pod-request')
    expect(request_row.get_by_test_id('pod-request-name')).to_have_text('Anna K.')
    expect(request_row.get_by_test_id('pod-request-meta')).to_contain_text('Europe/Berlin')
    expect(request_row.get_by_test_id('pod-request-meta')).to_contain_text('Requested today')
    expect(request_row.get_by_test_id('pod-request-fit')).to_have_text(re.compile(r'Fits \d+ of \d+ suggested times'))
    expect(request_row.get_by_test_id('pod-request-message-text')).to_have_text('I am building a RAG eval harness')
    expect(request_row.get_by_role('button', name='Decline')).to_be_visible()
    request_row.get_by_role('button', name='Approve').click()
    expect(page.get_by_test_id('pod-member-name')).to_contain_text(['Raj S.', 'Mike K.', 'Anna K.'])
    expect(page.get_by_test_id('pod-requests-empty')).to_be_visible()
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    link = page.get_by_role('link', name=re.compile('You joined Evening builders'))
    expect(link).to_be_visible()
    link.click()
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Your pod')
    context.close()


@browser_journey
def test_member_joins_the_waiting_list_and_gets_in_when_a_seat_opens(django_server, browser):
    _course, cohort, users = _seed()
    from notifications.models import Notification

    pod = _pod(cohort, 'Evening builders', [users['raj'], users['mike']], owner=users['raj'], max_members=2)
    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{_home(django_server)}/pods?cohort=4', wait_until='domcontentloaded')
    row = page.get_by_test_id('pod-row').first
    expect(row.get_by_test_id('pod-row-badge')).to_have_text('Full - waiting list')
    row.get_by_role('link', name='Join waiting list').click()
    page.get_by_role('button', name='Join waiting list').click()
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('On waiting list - #1')
    context.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-leave-toggle').click()
    page.get_by_role('button', name='Yes, leave pod').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('You left Evening builders.')
    context.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    request_row = page.get_by_test_id('pod-request')
    expect(request_row.get_by_test_id('pod-request-status')).to_have_text('Pending')
    approve = request_row.get_by_role('button', name='Approve')
    expect(approve).to_be_enabled()
    assert Notification.objects.filter(user=users['raj'], title='A seat opened in Evening builders').exists()
    connection.close()
    approve.click()
    expect(page.get_by_test_id('pod-meta')).to_contain_text('2 of 2 members')
    expect(page.get_by_test_id('pod-member-name')).to_contain_text(['Raj S.', 'Anna K.'])
    context.close()


@browser_journey
def test_pod_members_see_suggestions_that_respect_every_timezone(django_server, browser):
    _course, cohort, users = _seed()
    tue_thu = lambda start, end: [(1, start, end, 'preferred'), (3, start, end, 'preferred')]  # noqa: E731
    _windows(users['anna'], 'Europe/Berlin', tue_thu(18 * 60, 21 * 60))
    _windows(users['raj'], 'Asia/Kolkata', tue_thu(21 * 60, 23 * 60 + 30))
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['raj'], users['mike']], owner=users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    times = page.get_by_test_id('pod-slot-time')
    expect(times.first).to_have_text(re.compile(r'^(Tue|Thu), .* Europe/Berlin$'))
    strip = page.get_by_test_id('pod-slot-strip').first
    expect(strip).to_contain_text(re.compile(r'Anna K\. \d\d:\d\d Berlin'))
    expect(strip).to_contain_text(re.compile(r'Raj S\. \d\d:\d\d Kolkata'))
    expect(page.get_by_test_id('pod-times-based-on')).to_have_text(
        "Based on 2 of 3 members. Mike K. hasn't added availability yet.",
    )
    anna_first = times.first.get_attribute('datetime')
    context.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slot-time').first).to_have_text(re.compile(r'Asia/Kolkata$'))
    assert page.get_by_test_id('pod-slot-time').first.get_attribute('datetime') == anna_first
    context.close()


@browser_journey
def test_pod_moves_to_slack_through_a_public_channel(django_server, browser):
    _course, cohort, users = _seed()
    from accounts.models import User

    for email, slack_id in (('anna@test.com', 'UANNA'), ('raj@test.com', 'URAJ')):
        User.objects.filter(email=email).update(slack_member=True, slack_user_id=slack_id)
    connection.close()
    _set_config('SLACK_TEAM_ID', 'T0POD')
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['raj']], owner=users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    callout = page.get_by_test_id('pod-slack-callout')
    expect(callout).to_contain_text('Move your pod to Slack')
    expect(callout.get_by_test_id('pod-slack-suggested-name')).to_have_text(re.compile(r'^#pod-'))
    expect(page.get_by_test_id('pod-member-slack')).to_have_attribute('href', 'https://app.slack.com/client/T0POD/URAJ')

    callout.get_by_test_id('pod-slack-input').fill('http://example.com/chan')
    callout.get_by_role('button', name='Save link').click()
    expect(page.get_by_test_id('pod-slack-error')).to_contain_text('Paste a Slack channel link')
    expect(page.get_by_test_id('pod-slack-callout')).to_be_visible()

    page.get_by_test_id('pod-slack-input').fill('https://aishippinglabs.slack.com/archives/C0123456789')
    page.get_by_role('button', name='Save link').click()
    expect(page.get_by_test_id('pod-slack-channel')).to_have_text('Slack channel - Open in Slack')
    expect(page.get_by_test_id('pod-slack-open')).to_have_attribute(
        'href', 'https://aishippinglabs.slack.com/archives/C0123456789',
    )
    expect(page.get_by_test_id('pod-slack-callout')).to_have_count(0)
    context.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slack-open')).to_have_attribute(
        'href', 'https://aishippinglabs.slack.com/archives/C0123456789',
    )
    context.close()
    _set_config('SLACK_TEAM_ID', None)


@browser_journey
def test_basic_member_in_a_pod_understands_why_slack_is_not_offered(django_server, browser):
    _course, cohort, users = _seed()
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['basic']], owner=users['anna'])
    context, page = _page(browser, 'basic@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slack-gate')).to_have_text('Slack is available to Main and Premium members.')
    expect(page.get_by_test_id('pod-slack-membership')).to_have_attribute('href', '/membership')
    expect(page.get_by_test_id('pod-slack-input')).to_have_count(0)
    expect(page.get_by_test_id('pod-member-slack')).to_have_count(0)
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slack-tier-warning')).to_contain_text('Not everyone in this pod can join Slack')
    context.close()


@browser_journey
def test_someone_outside_the_cohort_cannot_see_its_pods(django_server, browser):
    _course, cohort, users = _seed()
    pod = _pod(cohort, 'Evening builders', [users['anna']], owner=users['anna'])
    context, page = _page(browser, 'main@test.com')
    for url in (f'{_home(django_server)}/pods?cohort=4', _pod_url(django_server, pod)):
        response = page.goto(url, wait_until='domcontentloaded')
        assert response.status == 404
        expect(page.get_by_text('Anna K.')).to_have_count(0)
    context.close()

    context, page = _page(browser)
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page).to_have_url(re.compile(r'/accounts/login/\?next=/courses/pods-buildcamp/home/pods/\d+$'))
    context.close()


@pytest.mark.core
@browser_journey
def test_admin_creates_a_pod_in_studio_for_paired_students(django_server, browser):
    _seed()
    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    page.get_by_test_id('pods-new').click()
    page.get_by_label('Cohort').select_option(label='Pods Buildcamp - Cohort 4')
    page.get_by_label('Name').fill('Agents pod')
    page.get_by_label('Purpose').fill('Ship an agent demo')
    page.get_by_label('Owner email').fill('anna@test.com')
    page.get_by_label('Member emails').fill('anna@test.com\nraj@test.com\nmain@test.com')
    page.get_by_test_id('sticky-save-action').click()
    expect(page.get_by_test_id('studio-pod-result-added')).to_contain_text('Added (2)')
    expect(page.get_by_test_id('studio-pod-result-not_enrolled')).to_contain_text('main@test.com')

    page.get_by_test_id('studio-pod-max-members').fill('5')
    page.get_by_test_id('sticky-save-action').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('Pod saved.')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row = page.get_by_test_id('studio-pod-row').filter(has_text='Agents pod')
    expect(row.get_by_test_id('studio-pod-members')).to_have_text('2/5')
    # At the default 1280px operator width the table fits without sideways
    # scrolling, so the row's View action is on screen.
    fits = page.evaluate(
        "() => { const w = document.querySelector('table').parentElement;"
        " return w.scrollWidth <= w.clientWidth; }"
    )
    assert fits
    expect(row.get_by_role('link', name='View')).to_be_in_viewport()
    expect(row.get_by_test_id('studio-pod-owner')).to_have_text('anna@test.com')
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{_home(django_server)}/pods?cohort=4', wait_until='domcontentloaded')
    first = page.get_by_test_id('pod-row').first
    expect(first.get_by_test_id('pod-row-name')).to_have_text('Agents pod')
    expect(first.get_by_test_id('pod-row-badge')).to_have_text('Your pod')
    context.close()


@browser_journey
def test_admin_overrides_an_owner_who_has_not_answered(django_server, browser):
    _course, cohort, users = _seed()
    from pods.models import PodJoinRequest
    from pods.services.membership import request_to_join

    _set_config('PODS_STALE_REQUEST_DAYS', '5')
    pod = _pod(cohort, 'Evening builders', [users['raj']], owner=users['raj'])
    join_request = request_to_join(pod, users['mike'])
    PodJoinRequest.objects.filter(pk=join_request.pk).update(created_at=timezone.now() - datetime.timedelta(days=6))
    connection.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row = page.get_by_test_id('studio-pod-row').filter(has_text='Evening builders')
    expect(row.get_by_test_id('studio-pod-oldest')).to_have_text('6 days ago')
    expect(row.get_by_test_id('studio-pod-oldest').locator('span')).to_have_class(re.compile('amber'))
    row.get_by_test_id('studio-pod-name').click()
    page.get_by_test_id('studio-pod-request-approve').click()
    expect(page.get_by_test_id('studio-pod-member')).to_contain_text(['raj@test.com', 'mike@test.com'])
    context.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    expect(page.get_by_role('link', name=re.compile('You joined Evening builders'))).to_be_visible()
    context.close()
    _set_config('PODS_STALE_REQUEST_DAYS', None)


@browser_journey
def test_pods_stay_hidden_until_the_course_is_enabled(django_server, browser):
    _seed(enabled=False)
    context, page = _page(browser, 'anna@test.com')
    page.goto(_home(django_server), wait_until='domcontentloaded')
    expect(page.get_by_role('navigation', name='Course pages')).to_be_visible()
    expect(page.get_by_test_id('course-tab-pods')).to_have_count(0)
    response = page.goto(f'{_home(django_server)}/pods', wait_until='domcontentloaded')
    assert response.status == 404

    admin_context, admin_page = _page(browser, 'admin@test.com')
    admin_page.goto(f'{django_server}/studio/settings/#community', wait_until='domcontentloaded')
    card = admin_page.locator('#integration-pods')
    card.locator('#field-PODS_COURSE_SLUGS').fill(COURSE_SLUG)
    card.get_by_role('button', name='Save Pods').click()
    expect(card.locator('[data-source-badge="db"]').first).to_be_visible()
    admin_context.close()

    page.goto(_home(django_server), wait_until='domcontentloaded')
    expect(page.get_by_test_id('course-tab-pods')).to_be_visible()
    context.close()
    _set_config('PODS_COURSE_SLUGS', None)
