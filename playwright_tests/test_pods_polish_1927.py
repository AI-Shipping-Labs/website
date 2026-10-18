"""Pods polish journeys (issue #1927): honest suggested times and genuinely
different suggested slots.

Fixtures come from ``test_pods_1918``: a published course enabled in
``PODS_COURSE_SLUGS`` with dated ``Cohort 4``; enrolled anna
(Europe/Berlin), raj (Asia/Kolkata), mike (America/New_York), all Main.
"""

import datetime
import os
import re
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import expect

from playwright_tests.test_pods_1918 import (
    COURSE_SLUG,
    WEEKDAY_EVENINGS,
    _page,
    _pod,
    _pod_url,
    _seed,
    _windows,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

PODS_TAB = f'/courses/{COURSE_SLUG}/home/pods?cohort=4'


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
