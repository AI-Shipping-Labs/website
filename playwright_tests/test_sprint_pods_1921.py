"""Pods for community sprints (issue #1921).

Fixtures: ``spring-sprint`` (active, ``pods_enabled=True``) with enrolled
``main@test.com`` (Anna K., Main), ``main2@test.com`` (Mike K., Main) and
``free@test.com`` (Fran F., Free, enrolled by staff); ``basic@test.com`` (Bea
S., Basic) is not enrolled. ``autumn-sprint`` has pods off.
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

SPRINT_SLUG = 'spring-sprint'
SPRINT_NAME = 'Spring sprint'
COURSE_SLUG = 'pods-buildcamp'
ALL_WEEK = [(day, 8 * 60, 22 * 60, 'preferred') for day in range(7)]


def _seed():
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment

    create_staff_user('admin@test.com')
    people = {
        'anna': ('main@test.com', 'main', 'Anna', 'Klein'),
        'mike': ('main2@test.com', 'main', 'Mike', 'Kay'),
        'free': ('free@test.com', 'free', 'Fran', 'Free'),
        'basic': ('basic@test.com', 'basic', 'Bea', 'Sic'),
    }
    users = {}
    for key, (email, tier, first, last) in people.items():
        create_user(email, tier_slug=tier, first_name=first)
        user = User.objects.get(email=email)
        user.last_name = last
        user.preferred_timezone = 'Europe/Berlin'
        user.save(update_fields=['last_name', 'preferred_timezone'])
        users[key] = user
    today = timezone.localdate()
    sprint = Sprint.objects.create(
        name=SPRINT_NAME, slug=SPRINT_SLUG, status='active', pods_enabled=True,
        start_date=today - datetime.timedelta(days=7), duration_weeks=6,
    )
    autumn = Sprint.objects.create(
        name='Autumn sprint', slug='autumn-sprint', status='active', pods_enabled=False,
        start_date=today - datetime.timedelta(days=3), duration_weeks=6,
    )
    for key in ('anna', 'mike'):
        SprintEnrollment.objects.create(sprint=sprint, user=users[key])
    SprintEnrollment.objects.create(sprint=sprint, user=users['free'], enrolled_by=User.objects.get(email='admin@test.com'))
    connection.close()
    return sprint, autumn, users


def _pod(sprint, name, members, owner=None):
    from pods.models import Pod, PodMembership

    pod = Pod.objects.create(
        sprint=sprint, name=name, purpose=f'{name} meets weekly to ship.', owner=owner, source='studio',
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    connection.close()
    return pod


def _windows(user):
    from pods.models import AvailabilityProfile, AvailabilityWindow

    profile, _ = AvailabilityProfile.objects.update_or_create(user=user, defaults={'timezone': 'Europe/Berlin'})
    profile.windows.all().delete()
    for weekday, start, end, preference in ALL_WEEK:
        AvailabilityWindow.objects.create(
            profile=profile, weekday=weekday, start_minute=start, end_minute=end, preference=preference,
        )
    connection.close()


def _page(browser, email=None):
    context = browser.new_context(viewport=VIEWPORT)
    cookies = [{'name': 'aslab_analytics_consent', 'value': 'denied', 'domain': '127.0.0.1', 'path': '/'}]
    if email:
        cookies += [
            {'name': 'sessionid', 'value': create_session_for_user(email), 'domain': '127.0.0.1', 'path': '/'},
            {'name': 'csrftoken', 'value': 'e2e-test-csrf-token-value', 'domain': '127.0.0.1', 'path': '/'},
        ]
    context.add_cookies(cookies)
    return context, context.new_page()


def _sprint_url(server, slug=SPRINT_SLUG):
    return f'{server}/sprints/{slug}'


def _pod_url(server, pod):
    return f'{server}/sprints/{SPRINT_SLUG}/pods/{pod.pk}'


@pytest.mark.core
@browser_journey
def test_enrolled_member_finds_pods_on_the_sprint_page_and_starts_one(django_server, browser):
    _seed()
    context, page = _page(browser, 'main@test.com')
    page.goto(_sprint_url(django_server), wait_until='domcontentloaded')
    section = page.get_by_test_id('sprint-pods')
    expect(section).to_contain_text("You're not in a pod yet")
    body = page.content()
    assert body.index('data-testid="sprint-accountability-partners"') < body.index('data-testid="sprint-pods"')

    section.get_by_role('link', name='Browse pods').click()
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods$'))
    expect(page.get_by_test_id('sprint-pods-sprint-link')).to_have_text(SPRINT_NAME)
    expect(page.get_by_test_id('pods-list')).to_contain_text(
        'Small groups in this sprint that meet to work on one thing together.',
    )
    expect(page.get_by_test_id('course-tab-pods')).to_have_count(0)

    page.get_by_role('link', name='Start a pod').first.click()
    page.get_by_label('Name').fill('Agents study group')
    page.get_by_label('Purpose').fill('Build and review one agent a week')
    page.get_by_role('button', name='Start pod').click()
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods/\d+$'))
    expect(page.get_by_test_id('pod-name')).to_have_text('Agents study group')
    expect(page.get_by_test_id('pod-member-name')).to_contain_text(['Anna K.'])

    page.get_by_test_id('sprint-pods-sprint-link').click()
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}$'))
    row = page.get_by_test_id('sprint-pod-row')
    expect(row.get_by_test_id('sprint-pod-name')).to_have_text('Agents study group')
    expect(row.get_by_test_id('sprint-pod-meta')).to_have_text('1 of 4 members')
    context.close()


@pytest.mark.core
@browser_journey
def test_second_member_asks_to_join_and_the_owner_approves_from_the_sprint_page(django_server, browser):
    sprint, _autumn, users = _seed()
    pod = _pod(sprint, 'Agents study group', [users['anna']], owner=users['anna'])

    context, page = _page(browser, 'main2@test.com')
    page.goto(f'{_sprint_url(django_server)}/pods', wait_until='domcontentloaded')
    page.get_by_test_id('pod-row').first.get_by_role('link', name='Request to join').click()
    page.get_by_test_id('pod-request-submit').click()
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Request sent')
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods/{pod.pk}$'))
    context.close()

    context, page = _page(browser, 'main@test.com')
    page.goto(_sprint_url(django_server), wait_until='domcontentloaded')
    waiting = page.get_by_test_id('sprint-pod-requests')
    expect(waiting).to_have_text('1 request waiting for you')
    waiting.click()
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods/{pod.pk}#requests$'))
    page.get_by_test_id('pod-request').get_by_role('button', name='Approve').click()
    expect(page.get_by_test_id('pod-member-name')).to_contain_text(['Anna K.', 'Mike K.'])
    main_text = page.locator('#main-content').inner_text()
    assert not re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', main_text), main_text
    context.close()


@browser_journey
def test_pod_members_agree_a_meeting_time_on_a_sprint_pod(django_server, browser):
    sprint, _autumn, users = _seed()
    _windows(users['anna'])
    _windows(users['mike'])
    pod = _pod(sprint, 'Agents study group', [users['anna'], users['mike']], owner=users['anna'])

    context, page = _page(browser, 'main@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-slot-propose').first.click()
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods/{pod.pk}/meetings/new\?'))
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    context.close()

    context, page = _page(browser, 'main2@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-meeting-going').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('Meeting confirmed.')
    context.close()

    context, page = _page(browser, 'main@test.com')
    page.goto(f'{django_server}/', wait_until='domcontentloaded')
    link = page.get_by_test_id('dashboard-pod-meeting-row').get_by_test_id('dashboard-pod-meeting-link')
    expect(link).to_have_attribute('href', f'/sprints/{SPRINT_SLUG}/pods/{pod.pk}')
    context.close()


@browser_journey
def test_pod_members_chat_on_a_sprint_pod(django_server, browser):
    sprint, _autumn, users = _seed()
    pod = _pod(sprint, 'Agents study group', [users['anna'], users['mike']], owner=users['anna'])

    context, page = _page(browser, 'main@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    chat = page.get_by_test_id('pod-chat')
    chat.get_by_test_id('pod-chat-input').fill('Shall we meet Tuesday?')
    chat.get_by_test_id('pod-chat-send').click()
    message = page.get_by_test_id('pod-chat-message').last
    expect(message.get_by_test_id('pod-chat-body')).to_have_text('Shall we meet Tuesday?')
    expect(message.get_by_test_id('pod-chat-author')).to_have_text('Anna K. (you)')
    expect(page).to_have_url(re.compile(rf'/sprints/{SPRINT_SLUG}/pods/{pod.pk}#chat$'))
    context.close()

    context, page = _page(browser, 'main2@test.com')
    page.goto(f'{_sprint_url(django_server)}/pods', wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-row').first.get_by_test_id('pod-row-unread')).to_have_text('1 new message')
    context.close()


@browser_journey
def test_member_who_leaves_the_sprint_is_removed_from_its_pods(django_server, browser):
    sprint, _autumn, users = _seed()
    from content.models import Cohort, CohortEnrollment, Course
    from pods.models import PodJoinRequest, PodMembership

    pod = _pod(sprint, 'Agents study group', [users['anna'], users['mike']], owner=users['anna'])
    other = _pod(sprint, 'Evals crew', [users['anna']], owner=users['anna'])
    PodJoinRequest.objects.create(pod=other, user=users['mike'])
    course = Course.objects.create(title='Pods Buildcamp', slug=COURSE_SLUG, status='published', required_level=0)
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='4', mode='cohort',
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        end_date=timezone.localdate() + datetime.timedelta(days=50),
    )
    CohortEnrollment.objects.create(user=users['mike'], cohort=cohort)
    from pods.models import Pod

    cohort_pod = Pod.objects.create(cohort=cohort, name='Cohort pod', purpose='p', source='studio')
    PodMembership.objects.create(pod=cohort_pod, user=users['mike'], source='staff')
    connection.close()

    context, page = _page(browser, 'main2@test.com')
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto(_sprint_url(django_server), wait_until='domcontentloaded')
    page.get_by_test_id('sprint-cta-leave').click()
    expect(page.get_by_test_id('sprint-message')).to_contain_text(f'You left {SPRINT_NAME}.')
    context.close()

    context, page = _page(browser, 'main@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-member-name')).to_have_text(['Anna K. (you)'])
    expect(page.get_by_test_id('pod-meta')).to_contain_text('1 of 4 members')
    context.close()

    assert PodJoinRequest.objects.get(pod=other, user=users['mike']).status == 'cancelled'
    assert PodMembership.objects.filter(pod=cohort_pod, user=users['mike']).exists()
    connection.close()


@browser_journey
def test_visitor_who_is_not_in_the_sprint_cannot_see_its_pods(django_server, browser):
    _seed()
    context, page = _page(browser, 'basic@test.com')
    page.goto(_sprint_url(django_server), wait_until='domcontentloaded')
    expect(page.get_by_test_id('sprint-pods')).to_have_count(0)
    expect(page.get_by_test_id('sprint-landing-includes-pods')).to_have_text(
        'Pods: small groups that meet to work on one thing',
    )
    response = page.goto(f'{_sprint_url(django_server)}/pods', wait_until='domcontentloaded')
    assert response.status == 404
    context.close()

    context, page = _page(browser)
    page.goto(f'{_sprint_url(django_server)}/pods', wait_until='domcontentloaded')
    expect(page).to_have_url(re.compile(rf'/accounts/login/\?next=/sprints/{SPRINT_SLUG}/pods$'))
    context.close()


@browser_journey
def test_sprint_with_pods_turned_off_shows_nothing(django_server, browser):
    _sprint, autumn, users = _seed()
    from plans.models import SprintEnrollment

    SprintEnrollment.objects.create(sprint=autumn, user=users['anna'])
    connection.close()
    context, page = _page(browser, 'main@test.com')
    page.goto(_sprint_url(django_server, 'autumn-sprint'), wait_until='domcontentloaded')
    expect(page.get_by_test_id('sprint-cta-enrolled')).to_be_visible()
    expect(page.get_by_test_id('sprint-pods')).to_have_count(0)
    expect(page.get_by_test_id('sprint-landing-includes-pods')).to_have_count(0)
    response = page.goto(f'{_sprint_url(django_server, "autumn-sprint")}/pods', wait_until='domcontentloaded')
    assert response.status == 404
    context.close()


@browser_journey
def test_member_enrolled_below_the_sprint_tier_uses_pods_but_not_slack(django_server, browser):
    sprint, _autumn, users = _seed()
    _windows(users['free'])
    _windows(users['anna'])
    pod = _pod(sprint, 'Agents study group', [users['anna'], users['free']], owner=users['anna'])

    context, page = _page(browser, 'free@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slot').first).to_be_visible()
    expect(page.get_by_test_id('pod-slack-gate')).to_have_text('Slack is available to Main and Premium members.')
    expect(page.get_by_test_id('pod-slack-form')).to_have_count(0)
    page.get_by_test_id('pod-chat-input').fill('Hi from the free tier')
    page.get_by_test_id('pod-chat-send').click()
    expect(page.get_by_test_id('pod-chat-body').last).to_have_text('Hi from the free tier')
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_turn_on_pods_for_a_sprint_and_prepare_a_pod_in_studio(django_server, browser):
    _sprint, autumn, users = _seed()
    from plans.models import SprintEnrollment

    for key in ('anna', 'mike'):
        SprintEnrollment.objects.create(sprint=autumn, user=users[key])
    connection.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/sprints/{autumn.pk}/', wait_until='domcontentloaded')
    card = page.get_by_test_id('sprint-pods-card')
    expect(card.get_by_test_id('sprint-pods-off')).to_have_text('Pods are off for this sprint. Turn them on in Edit.')

    page.get_by_role('link', name='Edit sprint').click()
    page.get_by_test_id('sprint-pods-enabled').check()
    page.get_by_role('button', name='Save changes').click()
    expect(page.get_by_test_id('sprint-pods-off')).to_have_count(0)

    page.get_by_test_id('sprint-pods-card').get_by_role('link', name='Create pod').click()
    expect(page).to_have_url(re.compile(rf'/studio/pods/new\?sprint={autumn.pk}$'))
    expect(page.get_by_test_id('studio-pod-activity')).to_have_value(f'sprint:{autumn.pk}')

    page.get_by_label('Name').fill('Autumn agents')
    page.get_by_label('Purpose').fill('Ship one agent each')
    page.get_by_label('Owner email').fill('basic@test.com')
    page.get_by_role('button', name='Create pod').click()
    expect(page.get_by_test_id('studio-pod-error')).to_have_text('basic@test.com is not enrolled in this sprint.')

    page.get_by_label('Owner email').fill('main@test.com')
    page.get_by_label('Member emails').fill('main2@test.com\nbasic@test.com')
    page.get_by_role('button', name='Create pod').click()
    expect(page.get_by_test_id('studio-pod-title')).to_have_text('Autumn agents')
    expect(page.get_by_test_id('studio-pod-result-not_enrolled')).to_contain_text('basic@test.com')

    page.goto(f'{django_server}/studio/pods/?sprint={autumn.pk}', wait_until='domcontentloaded')
    rows = page.get_by_test_id('studio-pod-row')
    expect(rows).to_have_count(1)
    expect(rows.first.get_by_test_id('studio-pod-activity')).to_contain_text('Autumn sprint')
    rows.first.get_by_test_id('studio-pod-name').click()
    href = page.get_by_test_id('studio-pod-view-as-member').get_attribute('href')
    assert re.fullmatch(r'/sprints/autumn-sprint/pods/\d+', href), href
    page.goto(f'{django_server}{href}', wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-name')).to_have_text('Autumn agents')
    context.close()


@browser_journey
def test_cohort_pods_keep_working_unchanged(django_server, browser):
    _seed()
    from content.models import Cohort, CohortEnrollment, Course
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting
    from pods.models import Pod, PodMembership

    course = Course.objects.create(title='Pods Buildcamp', slug=COURSE_SLUG, status='published', required_level=0)
    cohort = Cohort.objects.create(
        course=course, name='Cohort 4', external_key='4', mode='cohort',
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        end_date=timezone.localdate() + datetime.timedelta(days=50),
    )
    from accounts.models import User

    anna = User.objects.get(email='main@test.com')
    CohortEnrollment.objects.create(user=anna, cohort=cohort)
    pod = Pod.objects.create(cohort=cohort, name='Cohort crew', purpose='Ship', source='studio')
    PodMembership.objects.create(pod=pod, user=anna, source='staff')
    IntegrationSetting.objects.update_or_create(key='PODS_COURSE_SLUGS', defaults={'value': COURSE_SLUG})
    clear_config_cache()
    connection.close()

    context, page = _page(browser, 'main@test.com')
    page.goto(f'{django_server}/courses/{COURSE_SLUG}/home/pods', wait_until='domcontentloaded')
    expect(page.get_by_test_id('course-tab-pods')).to_have_attribute('aria-current', 'page')
    expect(page.get_by_test_id('pods-list')).to_contain_text(
        'Small groups in your cohort that meet to work on one thing together.',
    )
    expect(page.get_by_test_id('sprint-pods-header')).to_have_count(0)
    page.get_by_test_id('pod-row').first.get_by_role('link', name='Open pod').click()
    expect(page).to_have_url(re.compile(rf'/courses/{COURSE_SLUG}/home/pods/{pod.pk}$'))
    context.close()
