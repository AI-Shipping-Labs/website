"""Pod meeting journeys (issue #1919): propose a weekly series, confirm and
see it on the dashboard, can't make it plus the one-tap move, move the rest
of a series, the call link and join window, mark held, the meeting limit,
the clock-change note, the same weekly time, outsiders, and Studio.

Fixtures come from ``test_pods_1918``: a published course enabled in
``PODS_COURSE_SLUGS`` with dated ``Cohort 4``; enrolled anna
(Europe/Berlin), raj (Asia/Kolkata), mike (America/New_York) and basic
(Bea S., enrolled, not in the pod), all with course access. Every time is
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

from playwright_tests.test_pods_1918 import _page, _pod, _pod_url, _seed, _windows
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

POD_NAME = 'RAG evals study group'
BERLIN = ZoneInfo('Europe/Berlin')
KOLKATA = ZoneInfo('Asia/Kolkata')
WEEKDAYS = range(5)


def _availability(users):
    """Overlapping ``Works well`` windows, each in the member's own zone
    (roughly 13:00-18:30 UTC on weekdays)."""
    _windows(users['anna'], 'Europe/Berlin', [(d, 14 * 60, 23 * 60, 'preferred') for d in WEEKDAYS])
    _windows(users['raj'], 'Asia/Kolkata', [(d, 18 * 60, 24 * 60, 'preferred') for d in WEEKDAYS])
    _windows(users['mike'], 'America/New_York', [(d, 8 * 60, 16 * 60, 'preferred') for d in WEEKDAYS])


def _meeting_pod(cohort, members, *, meeting_count=4, meeting_url=''):
    from pods.models import Pod

    pod = _pod(cohort, POD_NAME, members, owner=members[0])
    Pod.objects.filter(pk=pod.pk).update(meeting_count=meeting_count, meeting_minutes=60, meeting_url=meeting_url)
    pod.refresh_from_db()
    connection.close()
    return pod


def _meeting(pod, starts_at, *, status='scheduled', zone='Europe/Berlin', series_id=None):
    from pods.models import PodMeeting

    meeting = PodMeeting.objects.create(
        pod=pod, starts_at=starts_at, duration_minutes=60, timezone=zone, status=status,
        series_id=series_id, created_via='studio',
    )
    connection.close()
    return meeting


def _hour(days, hour_utc):
    """``days`` from today at ``hour_utc``:00 UTC."""
    day = (timezone.now() + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour_utc, 0, tzinfo=UTC)


def _weekly(pod, first_start, count=4):
    """``count`` scheduled weekly meetings from ``first_start`` (Berlin wall clock)."""
    import uuid

    from pods.services.meetings import weekly_start

    series_id = uuid.uuid4()
    return [
        _meeting(pod, weekly_start(first_start, 'Europe/Berlin', k), series_id=series_id) for k in range(count)
    ]


def _row(page, number, total=4):
    return page.get_by_test_id('pod-meeting').filter(
        has=page.get_by_test_id('pod-meeting-title').get_by_text(f'Meeting {number} of {total}', exact=True),
    )


def _messages(page):
    return page.get_by_test_id('messages-region')


@pytest.mark.core
@browser_journey
def test_pod_member_proposes_a_weekly_meeting_from_a_suggested_time(django_server, browser):
    _course, cohort, users = _seed()
    _availability(users)
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-meetings-empty')).to_contain_text('No meetings yet')
    expect(page.get_by_test_id('pod-meetings-progress-summary')).to_have_text('0 of 4 held')

    page.get_by_test_id('pod-slot-propose').first.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    expect(page.get_by_test_id('pod-meeting-form-time')).to_have_text(re.compile(r'Europe/Berlin$'))
    expect(page.get_by_test_id('pod-meeting-form-strip-item')).to_have_count(3)
    expect(page.get_by_test_id('pod-meeting-repeat-label')).to_have_text('Repeat weekly for the remaining 4 meetings')
    expect(page.get_by_test_id('pod-meeting-repeat')).to_be_checked()

    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Time proposed. It is confirmed once 2 of you can make it.')
    expect(page.get_by_test_id('pod-meeting-title')).to_have_text(
        re.compile(r'^Proposed: \w+days at \d\d:\d\d Europe/Berlin, 4 meetings from \w{3} \d+$'),
    )
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    expect(page.get_by_test_id('pod-slot-propose')).to_have_count(0)
    expect(page.get_by_test_id('pod-times-propose-blocked')).to_have_text(
        'Confirm or cancel the proposed time before proposing another.',
    )
    context.close()


@pytest.mark.core
@browser_journey
def test_second_member_confirms_and_the_pod_gets_its_four_weekly_meetings(django_server, browser):
    from pods.services.meetings import propose_meeting

    _course, cohort, users = _seed()
    _availability(users)
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    first = _hour(2, 0).astimezone(BERLIN).replace(hour=18).astimezone(UTC)
    propose_meeting(pod, users['anna'], first, repeat_weekly=True)
    connection.close()

    context, page = _page(browser, 'raj@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    page.get_by_role('link', name=re.compile(f'Anna K. proposed a time for {POD_NAME}')).first.click()
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')

    page.get_by_test_id('pod-meeting-going').click()
    expect(_messages(page)).to_contain_text('Meeting confirmed.')
    expect(page.get_by_test_id('pod-meeting-title')).to_have_text(
        [f'Meeting {n} of 4' for n in range(1, 5)],
    )
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text(['Confirmed'] * 4)
    times = page.get_by_test_id('pod-meeting-time').all_inner_texts()
    assert all(t.endswith('Asia/Kolkata') for t in times), times
    # One week apart at Anna's 18:00 Berlin; Raj reads them on the same weekday
    # (his time moves an hour if the EU clocks change inside the series).
    assert len({t[:3] for t in times}) == 1, times
    starts = [
        datetime.datetime.fromisoformat(v)
        for v in page.get_by_test_id('pod-meeting-time').evaluate_all('els => els.map(e => e.dateTime)')
    ]
    berlin_days = [s.astimezone(BERLIN).date() for s in starts]
    assert [(b - a).days for a, b in zip(berlin_days, berlin_days[1:], strict=False)] == [7, 7, 7], starts
    assert {s.astimezone(BERLIN).strftime('%H:%M') for s in starts} == {'18:00'}, starts

    page.goto(django_server + '/', wait_until='domcontentloaded')
    card = page.get_by_test_id('dashboard-pod-meeting-row')
    expect(card.get_by_test_id('dashboard-pod-meeting-title')).to_have_text(f'{POD_NAME} - Meeting 1 of 4')
    expect(card.get_by_test_id('dashboard-pod-meeting-time')).to_have_text(times[0])
    card.get_by_test_id('dashboard-pod-meeting-link').click()
    expect(page).to_have_url(re.compile(rf'/home/pods/{pod.pk}$'))
    context.close()


@pytest.mark.core
@browser_journey
def test_member_cant_make_it_and_the_pod_moves_it_to_the_next_best_time(django_server, browser):
    _course, cohort, users = _seed()
    _availability(users)
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    _weekly(pod, _hour(1, 16))

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _row(page, 2).get_by_test_id('pod-meeting-cant-make-it').click()
    expect(_messages(page)).to_contain_text("Marked that you can't make it. The pod sees the next best time.")
    expect(_row(page, 2).get_by_test_id('pod-meeting-cant')).to_have_text("Can't make it: Mike K.")
    expect(_row(page, 2).get_by_test_id('pod-meeting-going')).to_have_text('I can make it after all')
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    later_before = [_row(page, n).get_by_test_id('pod-meeting-time').inner_text() for n in (3, 4)]
    row = _row(page, 2)
    expect(row.get_by_test_id('pod-meeting-offer')).to_contain_text('Next best time:')
    expect(row.get_by_test_id('pod-meeting-offer-fit')).to_be_visible()
    offer = row.get_by_test_id('pod-meeting-offer-time').inner_text()
    move = row.get_by_test_id('pod-meeting-offer-move')
    expect(move).to_have_text(f'Move to {offer.rsplit(" ", 1)[0]}')
    move.click()
    expect(_messages(page)).to_contain_text('Meeting moved.')
    row = _row(page, 2)
    expect(row.get_by_test_id('pod-meeting-time')).to_have_text(offer)
    expect(row.get_by_test_id('pod-meeting-moved')).to_have_text(re.compile(r'^Moved by Anna K\. on \w{3} \d+$'))
    expect(row.get_by_test_id('pod-meeting-cant')).to_have_count(0)
    assert [_row(page, n).get_by_test_id('pod-meeting-time').inner_text() for n in (3, 4)] == later_before
    context.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    expect(page.get_by_role('link', name=re.compile(
        rf'Anna K\. moved {POD_NAME} meeting 2 to .* America\/New_York',
    ))).to_have_count(1)
    context.close()


@browser_journey
def test_member_moves_the_rest_of_the_series_to_a_new_weekly_time(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    now = timezone.now()
    days_since_tuesday = (now.weekday() - 1) % 7 or 7
    meetings = _weekly(pod, _hour(-days_since_tuesday, 16))
    from pods.models import PodMeeting

    PodMeeting.objects.filter(pk=meetings[0].pk).update(status='held')
    connection.close()
    local_now = now.astimezone(KOLKATA)
    thursday = local_now.date() + datetime.timedelta(days=(3 - local_now.weekday()) % 7 or 7)

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _row(page, 2).get_by_test_id('pod-meeting-change').click()
    expect(page.get_by_role('heading', name='Change meeting time')).to_be_visible()
    page.get_by_test_id('pod-move-choice-custom').check()
    page.get_by_test_id('pod-meeting-date').fill(thursday.isoformat())
    page.get_by_test_id('pod-meeting-time').fill('17:00')
    page.get_by_test_id('pod-meeting-move-later').check()
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Meeting moved.')
    for number, week in ((2, 0), (3, 1), (4, 2)):
        day = thursday + datetime.timedelta(days=7 * week)
        expect(_row(page, number).get_by_test_id('pod-meeting-time')).to_have_text(
            f'Thu {day:%b %d}, 17:00 Asia/Kolkata',
        )
    expect(_row(page, 1).get_by_test_id('pod-meeting-badge')).to_have_text('Held')
    context.close()

    from notifications.models import Notification

    moved = Notification.objects.filter(user=users['anna'], title__contains='and the later meetings')
    assert moved.count() == 1, list(moved.values_list('title', flat=True))
    connection.close()


@browser_journey
def test_pod_adds_its_call_link_and_joins_when_the_meeting_starts(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    _meeting(pod, timezone.now() + datetime.timedelta(minutes=5))
    _meeting(pod, timezone.now() + datetime.timedelta(days=1))

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-call-link-toggle').click()
    page.get_by_test_id('pod-call-link-input').fill('http://meet.google.com/abc')
    page.get_by_test_id('pod-call-link-save').click()
    expect(page.get_by_test_id('pod-call-link-error')).to_have_text(
        'Paste an https link to your call, for example https://meet.google.com/abc-defg-hij.',
    )
    page.get_by_test_id('pod-call-link-input').fill('https://meet.google.com/abc-defg-hij')
    page.get_by_test_id('pod-call-link-save').click()
    expect(_messages(page)).to_contain_text('Call link saved.')
    now_row, tomorrow_row = _row(page, 1), _row(page, 2)
    expect(now_row.get_by_test_id('pod-meeting-badge')).to_have_text('Starting now')
    join = now_row.get_by_test_id('pod-meeting-join')
    expect(join).to_have_attribute('href', 'https://meet.google.com/abc-defg-hij')
    expect(join).to_have_attribute('target', '_blank')
    # Issue #1934: the Call link meta sits on the soonest upcoming row only,
    # which here shows Join call instead; the section line keeps the link.
    expect(page.get_by_test_id('pod-call-link-line')).to_have_text('Call link: meet.google.com/abc-defg-hij')
    expect(tomorrow_row.get_by_test_id('pod-meeting-call-link')).to_have_count(0)
    expect(tomorrow_row.get_by_test_id('pod-meeting-join')).to_have_count(0)
    context.close()


@browser_journey
def test_pod_records_whether_past_meetings_happened(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    _meeting(pod, timezone.now() - datetime.timedelta(hours=2))

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row(page, 1)
    expect(row.get_by_test_id('pod-meeting-held')).to_be_visible()
    expect(row.get_by_test_id('pod-meeting-not-held')).to_have_text("Didn't happen")
    row.get_by_test_id('pod-meeting-held').click()
    expect(_messages(page)).to_contain_text('Marked as held.')
    expect(_row(page, 1).get_by_test_id('pod-meeting-badge')).to_have_text('Held')
    expect(_row(page, 1).get_by_test_id('pod-meeting-actions')).to_have_count(0)
    expect(page.get_by_test_id('pod-meetings-progress-summary')).to_have_text('1 of 4 held')
    context.close()


@browser_journey
def test_pod_cannot_plan_more_meetings_than_agreed(django_server, browser):
    _course, cohort, users = _seed()
    _availability(users)
    pod = _meeting_pod(cohort, [users['anna'], users['raj']], meeting_count=2)
    _meeting(pod, timezone.now() - datetime.timedelta(days=6), status='held')
    _meeting(pod, timezone.now() + datetime.timedelta(days=4))

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slot-propose')).to_have_count(0)
    expect(page.get_by_test_id('pod-times-propose-blocked')).to_have_text('All 2 meetings are planned.')

    page.get_by_test_id('pod-edit-link').click()
    page.get_by_test_id('pod-form-meeting-count').fill('1')
    page.get_by_test_id('pod-edit-submit').click()
    expect(page.get_by_test_id('pod-form-error')).to_have_text(
        'This pod already has 2 meetings planned or held. Cancel a meeting before lowering the number.',
    )

    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row(page, 2, total=2)
    row.get_by_test_id('pod-meeting-cancel-toggle').click()
    row.get_by_test_id('pod-meeting-cancel-confirm').click()
    expect(_messages(page)).to_contain_text('Meeting cancelled.')
    expect(page.get_by_test_id('pod-slot-propose').first).to_be_visible()
    context.close()


def _eu_clock_change_series_start():
    """Tuesday 18:00 Berlin, 12 days before the next EU October change.

    Meeting 3 then falls two days after the EU change and before the US one
    (always a week later); meeting 4 falls after both.
    """
    now = timezone.now()
    year = now.year
    while True:
        last_october = datetime.date(year, 10, 31)
        change = last_october - datetime.timedelta(days=(last_october.weekday() + 1) % 7)
        first = change - datetime.timedelta(days=12)
        start = datetime.datetime(first.year, first.month, first.day, 18, 0, tzinfo=BERLIN).astimezone(UTC)
        if start > now + datetime.timedelta(hours=2):
            return start
        year += 1


@browser_journey
def test_series_across_a_clock_change_warns_the_member_whose_time_shifts(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    _weekly(pod, _eu_clock_change_series_start())

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    times = page.get_by_test_id('pod-meeting-time')
    expect(times).to_have_count(4)
    assert all(', 18:00 Europe/Berlin' in t for t in times.all_inner_texts()), times.all_inner_texts()
    expect(_row(page, 3).get_by_test_id('pod-meeting-dst')).to_have_text(
        'Clock change: 13:00 for Mike K. this week (usually 12:00).',
    )
    for number in (1, 2, 4):
        expect(_row(page, number).get_by_test_id('pod-meeting-dst')).to_have_count(0)
    context.close()


@browser_journey
def test_member_schedules_meeting_2_at_the_same_weekly_time_after_a_one_off_first_meeting(django_server, browser):
    _course, cohort, users = _seed()
    _availability(users)
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    first = _meeting(pod, _hour(-2, 16), status='held')

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    same = page.get_by_test_id('pod-slot').first
    expect(same.get_by_test_id('pod-slot-same-week')).to_have_text('Same weekly time')
    from pods.services.meetings import weekly_start

    expected = weekly_start(first.starts_at, 'Europe/Berlin', 1)
    expect(same.get_by_test_id('pod-slot-time')).to_have_attribute('datetime', expected.isoformat())
    expect(same.get_by_test_id('pod-slot-fit')).to_be_visible()

    same.get_by_test_id('pod-slot-propose').click()
    page.get_by_test_id('pod-meeting-repeat').uncheck()
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Time proposed.')
    row = _row(page, 2)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    expect(row.get_by_test_id('pod-meeting-time')).to_have_attribute('datetime', expected.isoformat())
    context.close()


@browser_journey
def test_outsider_cannot_see_or_act_on_pod_meetings(django_server, browser):
    from pods.models import PodMeetingResponse

    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']], meeting_url='https://meet.google.com/abc-defg-hij')
    meeting = _meeting(pod, timezone.now() + datetime.timedelta(days=2))

    context, page = _page(browser, 'basic@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-name')).to_have_text(POD_NAME)
    expect(page.get_by_test_id('pod-meetings')).to_have_count(0)
    expect(page.get_by_test_id('pod-meeting-time')).to_have_count(0)
    expect(page.locator('a[href*="meet.google.com"]')).to_have_count(0)
    csrf_token = next(c['value'] for c in context.cookies() if c['name'] == 'csrftoken')
    response = page.request.post(
        f'{_pod_url(django_server, pod)}/meetings/{meeting.pk}/respond',
        form={'response': 'going'},
        headers={'X-CSRFToken': csrf_token},
    )
    assert response.status == 404
    assert not PodMeetingResponse.objects.filter(user=users['basic']).exists()
    connection.close()
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_schedules_pod_meetings_from_studio(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']])
    next_week = (timezone.now().astimezone(BERLIN) + datetime.timedelta(days=7)).date()

    context, page = _page(browser, 'admin@test.com')
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    page.get_by_test_id('studio-pod-schedule-date').fill(next_week.isoformat())
    page.get_by_test_id('studio-pod-schedule-time').fill('17:00')
    page.get_by_test_id('studio-pod-schedule-timezone').select_option('Europe/Berlin')
    page.get_by_test_id('studio-pod-schedule-repeat').check()
    page.get_by_test_id('studio-pod-schedule-submit').click()
    rows = page.get_by_test_id('studio-pod-meeting')
    expect(rows).to_have_count(4)
    expect(page.get_by_test_id('studio-pod-meeting-status')).to_have_text(['Scheduled'] * 4)
    expect(page.get_by_test_id('studio-pod-meeting-zone')).to_have_text(['Europe/Berlin'] * 4)
    expect(page.get_by_test_id('studio-pod-meeting-start').first).to_contain_text(
        f'{next_week.isoformat()} ',
    )

    # At the 1280px operator width the meetings table fits, so every row's
    # Mark held / Cancel actions are on screen without sideways scrolling.
    fits = page.evaluate(
        "() => { const w = document.querySelector('[data-testid=\\'studio-pod-meetings\\'] table').parentElement;"
        " return w.scrollWidth <= w.clientWidth; }"
    )
    assert fits
    cancel_box = rows.first.get_by_test_id('studio-pod-meeting-cancel').bounding_box()
    assert cancel_box['x'] + cancel_box['width'] <= page.viewport_size['width'], cancel_box
    rows.nth(2).get_by_test_id('studio-pod-meeting-cancel').click()
    expect(page.get_by_test_id('studio-pod-meeting').nth(2).get_by_test_id('studio-pod-meeting-status')).to_have_text(
        'Cancelled',
    )
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-meeting-badge')).to_have_text(['Confirmed', 'Confirmed', 'Cancelled', 'Confirmed'])
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    expect(page.get_by_role('link', name=re.compile(f'{POD_NAME} meets '))).to_have_count(1)
    context.close()
