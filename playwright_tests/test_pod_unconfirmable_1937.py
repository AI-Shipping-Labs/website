"""Pod proposals that can no longer be confirmed, plus wording fixes (issue #1937).

When too many members answer ``Can't make it`` the proposal reads
``Can't be confirmed`` and points at the next best time or ``Propose
another time``; the answer that causes it rings the bell for the rest of
the pod. Plus ``Cancel meeting`` on proposals, one call link line and
``since today`` / ``since yesterday`` dates.

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

from playwright_tests.test_pod_meetings_1919 import POD_NAME, _meeting, _meeting_pod, _messages
from playwright_tests.test_pod_meetings_polish_1934 import _broad_windows
from playwright_tests.test_pods_1918 import _page, _pod_url, _seed, _windows
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

LINE = "Too few of you can make this time, so it can't be confirmed. Propose another time."
NEW_YORK = ZoneInfo('America/New_York')


def _start(days, hour_utc=16):
    day = (timezone.now() + datetime.timedelta(days=days)).date()
    return datetime.datetime(day.year, day.month, day.day, hour_utc, 0, tzinfo=UTC)


def _propose(pod, proposer, days=3, *, cant=()):
    """A one-off proposal by ``proposer`` that each user in ``cant`` can't make."""
    from pods.models import MEETING_RESPONSE_CANT
    from pods.services.meetings import propose_meeting, respond_to_meeting

    [proposal] = propose_meeting(pod, proposer, _start(days))
    for user in cant:
        respond_to_meeting(proposal, user, MEETING_RESPONSE_CANT)
    connection.close()
    return proposal


def _row_for(page, meeting):
    return page.locator(f'[data-testid="pod-meeting"][data-meeting-id="{meeting.pk}"]')


def _is_primary(locator):
    return 'bg-accent' in (locator.get_attribute('class') or '')


@pytest.mark.core
@browser_journey
def test_pod_learns_a_proposed_time_is_dead_and_moves_on_with_the_next_best_time(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna', 'raj', 'mike'))
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    proposal = _propose(pod, users['anna'], cant=[users['mike']])

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod) + '#meetings', wait_until='domcontentloaded')
    _row_for(page, proposal).get_by_test_id('pod-meeting-cant-make-it').click()
    expect(_messages(page)).to_contain_text(
        "Marked that you can't make it. This time can't be confirmed, so propose another time.",
    )
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text("Can't be confirmed")
    expect(row.get_by_test_id('pod-meeting-unconfirmable')).to_have_text(LINE)
    expect(row.get_by_test_id('pod-meeting-waiting')).to_have_count(0)
    expect(row.get_by_test_id('pod-meeting-change')).to_have_text('Propose another time')
    offer = row.get_by_test_id('pod-meeting-offer-propose')
    expect(offer).to_have_text(re.compile(r'^Propose .+ instead$'))
    assert _is_primary(offer)
    assert not _is_primary(row.get_by_test_id('pod-meeting-change'))

    offer.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    page.get_by_test_id('pod-meeting-repeat').uncheck()
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Time proposed.')
    expect(page.locator('[data-testid="pod-meeting"][data-meeting-status="proposed"]')).to_have_count(1)
    expect(page.locator('[data-meeting-status="proposed"]').get_by_test_id('pod-meeting-badge')).to_have_text(
        'Proposed - 1 of 2 needed',
    )
    # The dead proposal is no longer open: it is cancelled.
    expect(_row_for(page, proposal).get_by_test_id('pod-meeting-badge')).to_have_text('Cancelled')
    context.close()


@browser_journey
def test_proposer_is_told_by_the_bell_that_their_time_cant_be_confirmed(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna', 'raj', 'mike'))
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    proposal = _propose(pod, users['anna'], cant=[users['mike'], users['raj']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(f'{django_server}/notifications', wait_until='domcontentloaded')
    link = page.get_by_role('link', name=re.compile(
        rf"Raj S\. can't make {POD_NAME} meeting 1, so this time can't be confirmed\. Propose another time\.",
    ))
    expect(link).to_have_count(1)
    link.click()
    expect(page).to_have_url(re.compile(rf'/home/pods/{pod.pk}#meetings$'))
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text("Can't be confirmed")
    expect(row.get_by_test_id('pod-meeting-change')).to_have_text('Propose another time')
    context.close()


@browser_journey
def test_member_proposes_another_time_when_no_next_best_time_exists(django_server, browser):
    _course, cohort, users = _seed()
    # No weekday is shared, so no time fits 2 of the 3.
    for key, weekday in (('anna', 0), ('raj', 1), ('mike', 2)):
        _windows(users[key], 'Europe/Berlin', [(weekday, 18 * 60, 19 * 60, 'preferred')])
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    proposal = _propose(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-no-offer')).to_have_text(
        'No other time within a week of this meeting fits at least 2 of you.',
    )
    change = row.get_by_test_id('pod-meeting-change')
    expect(change).to_have_text('Propose another time')
    assert _is_primary(change)

    change.click()
    expect(page.get_by_role('heading', name='Change meeting time')).to_be_visible()
    page.get_by_test_id('pod-move-choice-custom').check()
    day = (timezone.now().astimezone(ZoneInfo('Europe/Berlin')) + datetime.timedelta(days=4)).date()
    page.get_by_test_id('pod-meeting-date').fill(day.isoformat())
    page.get_by_test_id('pod-meeting-time').fill('18:00')
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Meeting moved.')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    expect(row.get_by_test_id('pod-meeting-cant')).to_have_count(0)
    expect(row.get_by_test_id('pod-meeting-unconfirmable')).to_have_count(0)
    context.close()


@browser_journey
def test_member_changes_their_mind_and_the_proposal_is_back_on_track(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    proposal = _propose(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text("Can't be confirmed")
    row.get_by_test_id('pod-meeting-going').click()
    expect(_messages(page)).to_contain_text('Meeting confirmed.')
    expect(_row_for(page, proposal).get_by_test_id('pod-meeting-badge')).to_have_text('Confirmed')
    context.close()


@browser_journey
def test_one_absence_in_a_larger_pod_does_not_raise_a_false_alarm(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike'], users['basic']])
    proposal = _propose(pod, users['anna'], cant=[users['raj']])

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 3 needed')
    expect(row.get_by_test_id('pod-meeting-waiting')).to_have_text('Waiting on you and Bea S. since today.')
    expect(page.get_by_text("Can't be confirmed")).to_have_count(0)
    expect(row.get_by_test_id('pod-meeting-unconfirmable')).to_have_count(0)

    page.goto(django_server + '/', wait_until='domcontentloaded')
    card = page.get_by_test_id('dashboard-pod-meeting-row')
    expect(card.get_by_test_id('dashboard-pod-meeting-title')).to_have_text(f'{POD_NAME} - Proposed time')
    expect(card.get_by_test_id('dashboard-pod-meeting-needs-answer')).to_have_text('Needs your answer')
    context.close()


@browser_journey
def test_member_checking_their_week_is_not_nagged_about_a_dead_proposal(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike'], users['basic']])
    _propose(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'basic@test.com')
    page.goto(django_server + '/', wait_until='domcontentloaded')
    expect(page.get_by_test_id('dashboard-pod-meeting-title')).to_have_count(0)
    context.close()


@browser_journey
def test_student_cancels_a_proposal_and_a_confirmed_meeting_with_the_same_wording(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])
    confirmed = _meeting(pod, _start(2))
    proposal = _propose(pod, users['anna'], days=4)

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_row_for(page, proposal).get_by_test_id('pod-meeting-cancel')).to_have_text('Cancel meeting')
    expect(_row_for(page, confirmed).get_by_test_id('pod-meeting-cancel-toggle')).to_have_text('Cancel meeting')
    expect(page.get_by_role('button', name='Cancel', exact=True)).to_have_count(0)

    _row_for(page, proposal).get_by_test_id('pod-meeting-cancel').click()
    expect(_messages(page)).to_contain_text('Meeting cancelled.')
    expect(_row_for(page, proposal).get_by_test_id('pod-meeting-badge')).to_have_text('Cancelled')

    row = _row_for(page, confirmed)
    row.get_by_test_id('pod-meeting-cancel-toggle').click()
    expect(row.get_by_test_id('pod-meeting-cancel-disclosure')).to_contain_text(
        'Cancel this meeting? Everyone in the pod gets a notification.',
    )
    row.get_by_test_id('pod-meeting-cancel-confirm').click()
    expect(_messages(page)).to_contain_text('Meeting cancelled.')
    expect(_row_for(page, confirmed).get_by_test_id('pod-meeting-badge')).to_have_text('Cancelled')
    context.close()


@browser_journey
def test_member_finds_the_call_link_once_not_twice(django_server, browser):
    from pods.models import PodMeeting

    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['raj']], meeting_url='https://meet.google.com/abc-defg-hij')
    meeting = _meeting(pod, timezone.now() + datetime.timedelta(days=1))

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-call-link-line')).to_have_text('Call link: meet.google.com/abc-defg-hij')
    expect(page.get_by_test_id('pod-meetings').get_by_text('Call link:')).to_have_count(1)
    expect(_row_for(page, meeting).get_by_test_id('pod-meeting-call-link')).to_have_count(0)
    expect(_row_for(page, meeting).get_by_test_id('pod-meeting-join')).to_have_count(0)

    # The meeting now starts in 5 minutes: Join call appears on its row.
    PodMeeting.objects.filter(pk=meeting.pk).update(starts_at=timezone.now() + datetime.timedelta(minutes=5))
    connection.close()
    page.reload(wait_until='domcontentloaded')
    join = _row_for(page, meeting).get_by_test_id('pod-meeting-join')
    expect(join).to_have_attribute('href', 'https://meet.google.com/abc-defg-hij')
    expect(page.get_by_test_id('pod-meeting-call-link')).to_have_count(0)
    context.close()


@browser_journey
def test_member_sees_how_fresh_a_proposal_is_in_their_own_words(django_server, browser):
    from pods.models import PodMeeting

    _course, cohort, users = _seed()
    now = timezone.now()
    local_midnight = now.astimezone(NEW_YORK).replace(hour=0, minute=0, second=0, microsecond=0)
    fresh_pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    fresh = _propose(fresh_pod, users['anna'])
    # Earlier the same New York day.
    PodMeeting.objects.filter(pk=fresh.pk).update(created_at=max(local_midnight, now - datetime.timedelta(hours=1)))
    moved_pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    moved = _propose(moved_pod, users['anna'], days=5)
    yesterday_noon = (local_midnight - datetime.timedelta(hours=12)).astimezone(UTC)
    PodMeeting.objects.filter(pk=moved.pk).update(
        created_at=yesterday_noon - datetime.timedelta(days=2), moved_at=yesterday_noon, moved_by=users['anna'],
    )
    connection.close()

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, fresh_pod), wait_until='domcontentloaded')
    expect(_row_for(page, fresh).get_by_test_id('pod-meeting-waiting')).to_have_text('Waiting on you since today.')

    page.goto(_pod_url(django_server, moved_pod), wait_until='domcontentloaded')
    row = _row_for(page, moved)
    expect(row.get_by_test_id('pod-meeting-moved')).to_have_text('Moved by Anna K. yesterday')
    expect(row.get_by_test_id('pod-meeting-waiting')).to_have_text('Waiting on you since yesterday.')
    context.close()
