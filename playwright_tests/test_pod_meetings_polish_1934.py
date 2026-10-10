"""Pod meetings polish (issue #1934): the confirmation rule on the propose
page, the ``Waiting on`` hint, unanswered proposals on the dashboard, the
next best time in the meeting's own week, the call link affordance,
clock-change wording and Studio ``Mark held`` only after the start.

Fixtures come from ``test_pods_1918``: enrolled anna (Europe/Berlin), raj
(Asia/Kolkata), mike (America/New_York) and basic (Bea S.). Every time is
created relative to now so the journeys never rot.
"""

import datetime
import os
import re
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import POD_NAME, _meeting, _meeting_pod, _messages, _row, _weekly
from playwright_tests.test_pods_1918 import _page, _pod_url, _seed, _windows
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

BERLIN = ZoneInfo('Europe/Berlin')
WEEKDAYS = range(5)


def _broad_windows(users, keys):
    """Overlapping weekday ``Works well`` windows (about 13:00-18:30 UTC)."""
    windows = {
        'anna': ('Europe/Berlin', 14 * 60, 23 * 60),
        'raj': ('Asia/Kolkata', 18 * 60, 24 * 60),
        'mike': ('America/New_York', 8 * 60, 16 * 60),
    }
    for key in keys:
        zone, start, end = windows[key]
        _windows(users[key], zone, [(d, start, end, 'preferred') for d in WEEKDAYS])


def _compact(value, zone):
    local = value.astimezone(ZoneInfo(zone))
    return f'{local:%b} {local.day}'


def _next_tuesday_berlin(min_days=2):
    """A Tuesday at 18:00 Berlin at least ``min_days`` days from now."""
    today = timezone.now().astimezone(BERLIN).date() + datetime.timedelta(days=min_days)
    day = today + datetime.timedelta(days=(1 - today.weekday()) % 7)
    return datetime.datetime(day.year, day.month, day.day, 18, 0, tzinfo=BERLIN).astimezone(UTC)


def _berlin_day_at(start, days, hour=18):
    day = start.astimezone(BERLIN).date() + datetime.timedelta(days=days)
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=BERLIN).astimezone(UTC)


@browser_journey
def test_student_proposes_a_time_and_understands_what_confirms_it(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna', 'mike'))
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-slot-propose').first.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    rule = page.get_by_test_id('pod-meeting-form-rule')
    expect(rule).to_have_text('Confirmed once 2 of you can make it.')
    rule_box = rule.bounding_box()
    repeat_box = page.get_by_test_id('pod-meeting-repeat').bounding_box()
    assert rule_box['y'] < repeat_box['y'], (rule_box, repeat_box)

    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Time proposed.')
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    today = _compact(timezone.now(), 'Europe/Berlin')
    expect(page.get_by_test_id('pod-meeting-waiting')).to_have_text(f'Waiting on Mike K. since {today}.')
    context.close()


@pytest.mark.core
@browser_journey
def test_quiet_member_answers_a_proposal_from_the_dashboard(django_server, browser):
    from pods.services.meetings import propose_meeting

    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    start = (timezone.now() + datetime.timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    propose_meeting(pod, users['anna'], start)
    connection.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(django_server + '/', wait_until='domcontentloaded')
    card = page.get_by_test_id('dashboard-pod-meeting-row')
    expect(card.get_by_test_id('dashboard-pod-meeting-title')).to_have_text(f'{POD_NAME} - Proposed time')
    expect(card.get_by_test_id('dashboard-pod-meeting-needs-answer')).to_have_text('Needs your answer')

    card.get_by_test_id('dashboard-pod-meeting-link').click()
    expect(page).to_have_url(re.compile(rf'/home/pods/{pod.pk}#meetings$'))
    since = _compact(timezone.now(), 'America/New_York')
    expect(page.get_by_test_id('pod-meeting-waiting')).to_have_text(f'Waiting on you since {since}.')

    page.get_by_test_id('pod-meeting-going').click()
    expect(_messages(page)).to_contain_text('Meeting confirmed.')
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text('Confirmed')
    expect(page.get_by_test_id('pod-meeting-waiting')).to_have_count(0)

    page.goto(django_server + '/', wait_until='domcontentloaded')
    card = page.get_by_test_id('dashboard-pod-meeting-row')
    expect(card.get_by_test_id('dashboard-pod-meeting-title')).to_have_text(f'{POD_NAME} - Meeting 1 of 4')
    expect(card.get_by_test_id('dashboard-pod-meeting-needs-answer')).to_have_count(0)
    context.close()


@browser_journey
def test_student_who_already_answered_is_not_nagged_on_the_dashboard(django_server, browser):
    from pods.models import MEETING_RESPONSE_GOING
    from pods.services.meetings import propose_meeting, respond_to_meeting

    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike'], users['raj'], users['basic']])
    start = (timezone.now() + datetime.timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    [proposal] = propose_meeting(pod, users['anna'], start)
    respond_to_meeting(proposal, users['mike'], MEETING_RESPONSE_GOING)
    connection.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(django_server + '/', wait_until='domcontentloaded')
    expect(page.get_by_test_id('dashboard-pod-meeting-title')).to_have_count(0)
    context.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(django_server + '/', wait_until='domcontentloaded')
    card = page.get_by_test_id('dashboard-pod-meeting-row')
    expect(card.get_by_test_id('dashboard-pod-meeting-title')).to_have_text(f'{POD_NAME} - Proposed time')
    expect(card.get_by_test_id('dashboard-pod-meeting-needs-answer')).to_have_text('Needs your answer')
    context.close()


@browser_journey
def test_student_who_cant_make_meeting_2_gets_an_offer_in_the_same_week(django_server, browser):
    from pods.services.meetings import format_in_zone

    _course, cohort, users = _seed()
    for key in ('anna', 'mike'):
        _windows(users[key], 'Europe/Berlin', [(1, 18 * 60, 19 * 60, 'preferred'), (2, 18 * 60, 19 * 60, 'preferred')])
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    meetings = _weekly(pod, _next_tuesday_berlin(), count=3)
    expected = _berlin_day_at(meetings[1].starts_at, 1)

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _row(page, 2, total=4).get_by_test_id('pod-meeting-cant-make-it').click()
    expect(_messages(page)).to_contain_text("Marked that you can't make it.")
    row = _row(page, 2, total=4)
    offer = row.get_by_test_id('pod-meeting-offer-time')
    # Wednesday of meeting 2's own week, not the Wednesday before it.
    expect(offer).to_have_attribute('datetime', expected.isoformat())
    # Mike's availability is kept in Berlin, so the page reads in Berlin.
    expect(offer).to_have_text(format_in_zone(expected, 'Europe/Berlin'))
    expect(offer).to_have_text(re.compile(r'^Wed '))

    row.get_by_test_id('pod-meeting-offer-move').click()
    expect(_messages(page)).to_contain_text('Meeting moved.')
    expect(_row(page, 2, total=4).get_by_test_id('pod-meeting-time')).to_have_attribute(
        'datetime', expected.isoformat(),
    )
    context.close()


@browser_journey
def test_no_nearby_time_fits_and_the_student_is_told_so(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna',))
    _windows(users['mike'], 'Europe/Berlin', [(1, 18 * 60, 19 * 60, 'preferred')])
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    _weekly(pod, _next_tuesday_berlin(), count=3)

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _row(page, 2, total=4).get_by_test_id('pod-meeting-cant-make-it').click()
    row = _row(page, 2, total=4)
    expect(row.get_by_test_id('pod-meeting-no-offer')).to_have_text(
        'No other time within a week of this meeting fits at least 2 of you.',
    )
    expect(row.get_by_test_id('pod-meeting-change')).to_be_visible()
    context.close()


@browser_journey
def test_pod_member_adds_then_changes_the_call_link_and_finds_it_on_the_next_meeting(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    for days in (2, 3, 4):
        _meeting(pod, timezone.now() + datetime.timedelta(days=days))

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    toggle = page.get_by_test_id('pod-call-link-toggle')
    expect(toggle).to_have_text('Add a call link')
    toggle.click()
    page.get_by_test_id('pod-call-link-input').fill('https://meet.google.com/abc-defg-hij')
    page.get_by_test_id('pod-call-link-save').click()
    expect(_messages(page)).to_contain_text('Call link saved.')
    expect(page.get_by_test_id('pod-call-link-line')).to_have_text('Call link: meet.google.com/abc-defg-hij')
    expect(page.get_by_test_id('pod-call-link-toggle')).to_have_text('Change link')
    expect(page.get_by_test_id('pod-meeting-call-link')).to_have_count(1)
    expect(_row(page, 1).get_by_test_id('pod-meeting-call-link')).to_have_text(
        'Call link: meet.google.com/abc-defg-hij',
    )
    for number in (2, 3):
        expect(_row(page, number).get_by_test_id('pod-meeting-call-link')).to_have_count(0)

    page.get_by_test_id('pod-call-link-toggle').click()
    page.get_by_test_id('pod-call-link-input').fill('https://zoom.us/j/123456789')
    page.get_by_test_id('pod-call-link-save').click()
    expect(_messages(page)).to_contain_text('Call link saved.')
    expect(page.get_by_test_id('pod-call-link-line')).to_have_text('Call link: zoom.us/j/123456789')
    expect(_row(page, 1).get_by_test_id('pod-meeting-call-link')).to_have_text('Call link: zoom.us/j/123456789')
    context.close()


def _series_start_five_days_before_the_eu_change():
    """Tuesday 18:00 Berlin, five days before the next EU October change.

    Meeting 2 is then two days after the EU change and before the US one
    (always a week later); meetings 3 and 4 fall after both.
    """
    now = timezone.now()
    year = now.year
    while True:
        last_october = datetime.date(year, 10, 31)
        change = last_october - datetime.timedelta(days=(last_october.weekday() + 1) % 7)
        first = change - datetime.timedelta(days=5)
        start = datetime.datetime(first.year, first.month, first.day, 18, 0, tzinfo=BERLIN).astimezone(UTC)
        if start > now + datetime.timedelta(hours=2):
            return start
        year += 1


@browser_journey
def test_members_in_other_timezones_read_clock_changes_correctly(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    _weekly(pod, _series_start_five_days_before_the_eu_change())

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_row(page, 2).get_by_test_id('pod-meeting-dst')).to_have_text([
        'Clock change: 22:30 for Raj S. from this week (was 21:30).',
        'Clock change: 13:00 for Mike K. this week (usually 12:00).',
    ])
    for number in (1, 3, 4):
        expect(_row(page, number).get_by_test_id('pod-meeting-dst')).to_have_count(0)
    context.close()


@browser_journey
def test_staff_can_only_mark_a_meeting_held_after_it_starts(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    _meeting(pod, timezone.now() - datetime.timedelta(days=1))
    _meeting(pod, timezone.now() + datetime.timedelta(days=7))

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    heading = page.locator('#studio-pod-meetings-heading')
    expect(heading).to_contain_text('Meetings (0 held,')
    rows = page.get_by_test_id('studio-pod-meeting')
    expect(rows).to_have_count(2)
    expect(rows.nth(0).get_by_test_id('studio-pod-meeting-held')).to_have_text('Mark held')
    expect(rows.nth(1).get_by_test_id('studio-pod-meeting-held')).to_have_count(0)
    for index in (0, 1):
        expect(rows.nth(index).get_by_test_id('studio-pod-meeting-cancel')).to_be_visible()

    rows.nth(0).get_by_test_id('studio-pod-meeting-held').click()
    expect(page.get_by_text('Meeting marked as held.')).to_be_visible()
    expect(page.get_by_test_id('studio-pod-meeting').nth(0).get_by_test_id('studio-pod-meeting-status')).to_have_text(
        'Held',
    )
    expect(page.locator('#studio-pod-meetings-heading')).to_contain_text('Meetings (1 held,')
    context.close()
