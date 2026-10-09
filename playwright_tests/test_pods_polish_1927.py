"""Pods polish journeys (issue #1927): honest suggested times, the
re-request cooldown after a decline, Studio's pending-only stale badge, and
genuinely different suggested slots.

Fixtures come from ``test_pods_1918``: a published course enabled in
``PODS_COURSE_SLUGS`` with dated ``Cohort 4``; enrolled anna
(Europe/Berlin), raj (Asia/Kolkata), mike (America/New_York), all Main.
"""

import datetime
import os
import re
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pods_1918 import (
    COURSE_SLUG,
    WEEKDAY_EVENINGS,
    _page,
    _pod,
    _pod_url,
    _seed,
    _set_config,
    _windows,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

PODS_TAB = f'/courses/{COURSE_SLUG}/home/pods?cohort=4'


def _decline(pod, user, days_ago=0):
    """Create a request from ``user`` and decline it ``days_ago`` days back."""
    from pods.models import PodJoinRequest
    from pods.services.membership import decline_request

    join_request = PodJoinRequest.objects.create(pod=pod, user=user, status='pending')
    decline_request(join_request, pod.owner)
    when = timezone.now() - datetime.timedelta(days=days_ago)
    PodJoinRequest.objects.filter(pk=join_request.pk).update(decided_at=when, created_at=when)
    connection.close()
    return when


def _berlin_date(value):
    return value.astimezone(ZoneInfo('Europe/Berlin')).strftime('%b %d')


@pytest.mark.core
@browser_journey
def test_pod_member_reads_suggested_times_knowing_who_has_no_availability(django_server, browser):
    _course, cohort, users = _seed()
    _windows(users['anna'], 'Europe/Berlin', WEEKDAY_EVENINGS)
    _windows(users['raj'], 'Europe/Berlin', WEEKDAY_EVENINGS)
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['raj'], users['mike']], owner=users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    based_on = page.get_by_test_id('pod-times-based-on')
    expect(based_on).to_have_text("Based on 2 of 3 members. Mike K. hasn't added availability yet.")
    first_slot = page.get_by_test_id('pod-slot').first
    assert based_on.bounding_box()['y'] < first_slot.bounding_box()['y']
    expect(first_slot.get_by_test_id('pod-slot-fit')).to_have_text('All 2 with availability - works well')
    items = first_slot.get_by_test_id('pod-slot-strip-item')
    expect(items).to_have_text([
        re.compile(r'^Anna K\. \d\d:\d\d Berlin$'),
        re.compile(r'^Raj S\. \d\d:\d\d Berlin$'),
        'Mike K. - no availability',
    ])
    context.close()


@browser_journey
def test_mike_adds_availability_and_the_pod_sees_an_everyone_fits_time(django_server, browser):
    _course, cohort, users = _seed()
    _windows(users['anna'], 'Europe/Berlin', WEEKDAY_EVENINGS)
    _windows(users['raj'], 'Europe/Berlin', WEEKDAY_EVENINGS)
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['raj'], users['mike']], owner=users['anna'])

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-edit-availability').click()
    page.get_by_test_id('availability-timezone').select_option('Europe/Berlin')
    page.get_by_test_id('availability-preset-weekday-evenings').click()
    page.get_by_test_id('availability-save').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('Availability saved.')

    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-times-based-on')).to_have_count(0)
    first_slot = page.get_by_test_id('pod-slot').first
    expect(first_slot.get_by_test_id('pod-slot-fit')).to_have_text('Everyone - works well')
    expect(first_slot.get_by_test_id('pod-slot-strip-item')).to_have_text([
        re.compile(r'^Anna K\. \d\d:\d\d Berlin$'),
        re.compile(r'^Raj S\. \d\d:\d\d Berlin$'),
        re.compile(r'^Mike K\. \d\d:\d\d Berlin$'),
    ])
    context.close()


@pytest.mark.core
@browser_journey
def test_declined_student_cannot_immediately_rerequest_the_same_pod(django_server, browser):
    _course, cohort, users = _seed()
    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    _pod(cohort, 'Pod B', [users['mike']], owner=users['mike'])
    decided = _decline(pod_a, users['anna'])
    ask_again = _berlin_date(decided + datetime.timedelta(days=14))

    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    page.get_by_role('link', name=re.compile('Your request to join Pod A was not accepted')).click()
    expect(page).to_have_url(re.compile(r'/home/pods\?cohort=4$'))
    page.get_by_test_id('pod-row').filter(has_text='Pod A').get_by_role('link', name='Open pod').click()
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Not accepted')
    expect(page.get_by_test_id('pod-request-form')).to_have_count(0)
    expect(page.get_by_test_id('pod-request-blocked')).to_have_text(
        f'Your request was not accepted. You can ask again on {ask_again}.',
    )

    page.goto(f'{django_server}{PODS_TAB}', wait_until='domcontentloaded')
    page.get_by_test_id('pod-row').filter(has_text='Pod B').get_by_role('link', name='Request to join').click()
    page.get_by_test_id('pod-request-submit').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('Request sent.')
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Request sent')
    context.close()


@browser_journey
def test_owner_is_not_pinged_again_by_a_declined_student(django_server, browser):
    _course, cohort, users = _seed()
    from notifications.models import Notification
    from pods.services.membership import decline_request, request_to_join

    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-request-form')).to_be_visible()

    # Meanwhile, in another tab, anna asked and raj declined.
    decline_request(request_to_join(pod_a, users['anna'], 'first ask'), users['raj'])
    decided = timezone.now()
    connection.close()

    page.get_by_test_id('pod-request-message').fill('asking again')
    page.get_by_test_id('pod-request-submit').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text(
        f'You can ask to join this pod again on {_berlin_date(decided + datetime.timedelta(days=14))}.',
    )
    context.close()

    assert Notification.objects.filter(user=users['raj'], title='New request to join Pod A').count() == 1
    connection.close()
    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-requests-empty')).to_be_visible()
    context.close()


@pytest.mark.core
@browser_journey
def test_student_asks_once_more_after_the_cooldown(django_server, browser):
    _course, cohort, users = _seed()
    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    _decline(pod_a, users['anna'], days_ago=15)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-request-retry')).to_have_text(
        'Your last request was not accepted. You can ask once more.',
    )
    page.get_by_test_id('pod-request-message').fill('Shipping an eval harness now')
    page.get_by_test_id('pod-request-submit').click()
    expect(page.get_by_test_id('messages-region')).to_contain_text('Request sent.')
    context.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    rows = page.get_by_test_id('pod-request')
    expect(rows).to_have_count(1)
    expect(rows.first.get_by_test_id('pod-request-message-text')).to_have_text('Shipping an eval harness now')
    context.close()


@browser_journey
def test_twice_declined_student_is_pointed_to_other_pods(django_server, browser):
    _course, cohort, users = _seed()
    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    _pod(cohort, 'Pod B', [users['mike']], owner=users['mike'])
    _decline(pod_a, users['anna'], days_ago=30)
    _decline(pod_a, users['anna'], days_ago=2)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-request-form')).to_have_count(0)
    expect(page.get_by_test_id('pod-request-blocked')).to_have_text(
        "This pod declined your request twice, so you can't ask to join it again. "
        'Browse other pods or start your own.',
    )
    page.get_by_role('link', name='Browse other pods').click()
    expect(page).to_have_url(re.compile(r'/home/pods\?cohort=4$'))
    expect(page.get_by_test_id('pod-row-name')).to_contain_text(['Pod B'])
    context.close()


@browser_journey
def test_staff_can_still_place_a_twice_declined_student(django_server, browser):
    _course, cohort, users = _seed()
    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    _decline(pod_a, users['anna'], days_ago=30)
    _decline(pod_a, users['anna'], days_ago=2)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod_a.pk}/', wait_until='domcontentloaded')
    page.get_by_test_id('studio-pod-add-emails').fill('anna@test.com')
    page.get_by_test_id('studio-pod-add-submit').click()
    expect(page.get_by_test_id('studio-pod-member')).to_contain_text(['raj@test.com', 'anna@test.com'])
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod_a), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-status-badge')).to_have_text('Your pod')
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_spots_a_stale_pod_in_studio(django_server, browser):
    _course, cohort, users = _seed()
    from pods.models import PodJoinRequest

    _set_config('PODS_STALE_REQUEST_DAYS', '5')
    pod_a = _pod(cohort, 'Pod A', [users['raj']], owner=users['raj'])
    pod_b = _pod(cohort, 'Pod B', [users['mike']], owner=users['mike'], max_members=1)
    now = timezone.now()
    pending = PodJoinRequest.objects.create(pod=pod_a, user=users['anna'], status='pending')
    waitlisted = PodJoinRequest.objects.create(pod=pod_b, user=users['anna'], status='waitlisted')
    PodJoinRequest.objects.filter(pk=pending.pk).update(created_at=now - datetime.timedelta(days=6))
    PodJoinRequest.objects.filter(pk=waitlisted.pk).update(created_at=now - datetime.timedelta(days=10))
    connection.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row_a = page.get_by_test_id('studio-pod-row').filter(has_text='Pod A')
    row_b = page.get_by_test_id('studio-pod-row').filter(has_text='Pod B')
    expect(row_a.get_by_test_id('studio-pod-oldest').locator('span')).to_have_class(re.compile('amber'))
    expect(row_b.get_by_test_id('studio-pod-oldest')).to_have_text('10 days ago')
    expect(row_b.get_by_test_id('studio-pod-oldest').locator('span')).to_have_count(0)

    row_a.get_by_test_id('studio-pod-name').click()
    page.get_by_test_id('studio-pod-request-decline').click()
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row_a = page.get_by_test_id('studio-pod-row').filter(has_text='Pod A')
    expect(row_a.get_by_test_id('studio-pod-oldest')).to_have_text('—')
    context.close()
    _set_config('PODS_STALE_REQUEST_DAYS', None)


@browser_journey
def test_pod_member_compares_genuinely_different_suggested_times(django_server, browser):
    _course, cohort, users = _seed()
    windows = [(1, 18 * 60, 20 * 60, 'preferred'), (3, 10 * 60, 11 * 60, 'preferred')]
    _windows(users['anna'], 'UTC', windows)
    _windows(users['raj'], 'UTC', windows)
    pod = _pod(cohort, 'Evening builders', [users['anna'], users['raj']], owner=users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    times = page.get_by_test_id('pod-slot-time')
    expect(times.first).to_be_visible()
    starts = [
        datetime.datetime.fromisoformat(times.nth(i).get_attribute('datetime'))
        for i in range(times.count())
    ]
    weekdays = {start.weekday() for start in starts}
    assert {1, 3} <= weekdays, starts
    for i, first in enumerate(starts):
        for second in starts[i + 1:]:
            gap = abs((first.weekday() * 1440 + first.hour * 60 + first.minute)
                      - (second.weekday() * 1440 + second.hour * 60 + second.minute)) % (7 * 1440)
            assert min(gap, 7 * 1440 - gap) >= 60, starts
    context.close()
