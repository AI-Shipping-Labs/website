"""Staff stuck-proposal follow-ups in Studio (issue #1938).

``/studio/pods/`` reads ``N proposal(s) waiting`` for a pod with live
proposals but no scheduled meeting; a short-notice proposal is stuck after
half the time to its start; and a proposal too many members can't make
shows ``Can't be confirmed`` on the Studio pod page until it recovers.

Fixtures come from ``test_pods_1918``: enrolled anna (Anna K.), raj (Raj
S.), mike (Mike K.) and basic (Bea S.); staff admin. Every time is relative
to now so the journeys never rot.
"""

import datetime
import os
import uuid

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import _meeting, _meeting_pod, _messages
from playwright_tests.test_pods_1918 import _page, _pod, _pod_url, _seed
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


def _hour(delta):
    return (timezone.now() + delta).replace(minute=0, second=0, microsecond=0)


def _open_proposal(pod, proposer, *, age, starts_in, weeks=1):
    """A member proposal nobody answered yet, created ``age`` ago.

    ``weeks`` > 1 makes a weekly series; the first meeting is its head.
    """
    from pods.models import PodMeeting

    now = timezone.now()
    series_id = uuid.uuid4() if weeks > 1 else None
    meetings = [
        PodMeeting.objects.create(
            pod=pod, starts_at=now + starts_in + k * 7 * DAY, duration_minutes=60, timezone='UTC',
            status='proposed', series_id=series_id, created_via='member', proposed_by=proposer,
        )
        for k in range(weeks)
    ]
    PodMeeting.objects.filter(pk__in=[m.pk for m in meetings]).update(created_at=now - age)
    connection.close()
    return meetings[0]


def _proposal_with_answers(pod, proposer, *, cant, age=2 * HOUR):
    """A proposal 3 days ahead (proposer going) that each of ``cant`` can't make."""
    from pods.models import MEETING_RESPONSE_CANT, PodMeeting
    from pods.services.meetings import propose_meeting, respond_to_meeting

    [proposal] = propose_meeting(pod, proposer, _hour(3 * DAY))
    for user in cant:
        respond_to_meeting(proposal, user, MEETING_RESPONSE_CANT)
    PodMeeting.objects.filter(pk=proposal.pk).update(created_at=timezone.now() - age)
    connection.close()
    return proposal


def _studio_row(page, pod):
    return page.locator(f'[data-testid="studio-pod-row"][data-pod-id="{pod.pk}"]')


def _studio_meeting(page, meeting):
    return page.locator(f'[data-testid="studio-pod-meeting"][data-meeting-id="{meeting.pk}"]')


def _trio(cohort, users):
    return _meeting_pod(cohort, [users['anna'], users['raj'], users['mike']])


@pytest.mark.core
@browser_journey
def test_staff_sees_a_pod_waiting_on_a_proposal_not_idle(django_server, browser):
    _course, cohort, users = _seed()
    waiting = _meeting_pod(cohort, [users['anna'], users['mike']])
    proposal = _open_proposal(waiting, users['anna'], age=HOUR, starts_in=4 * DAY)
    idle = _pod(cohort, 'Morning crew', [users['raj'], users['basic']], owner=users['raj'])

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row = _studio_row(page, waiting)
    expect(row.get_by_test_id('studio-pod-next-meeting')).to_have_text('1 proposal waiting')
    expect(row.get_by_test_id('studio-pod-stuck')).to_have_count(0)
    expect(_studio_row(page, idle).get_by_test_id('studio-pod-next-meeting')).to_have_text('No meeting planned')

    row.get_by_test_id('studio-pod-name').click()
    meeting = _studio_meeting(page, proposal)
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_have_text('No answer: Anna K., Mike K.')
    context.close()


@browser_journey
def test_staff_spots_a_short_notice_proposal_before_it_expires(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    # 20 hours of notice, 15 already gone: past the 10-hour halfway point.
    proposal = _open_proposal(pod, users['anna'], age=15 * HOUR, starts_in=5 * HOUR)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row = _studio_row(page, pod)
    expect(row.get_by_test_id('studio-pod-next-meeting')).to_have_text('1 proposal waiting')
    expect(row.get_by_test_id('studio-pod-stuck')).to_have_text('Stuck proposal')

    row.get_by_test_id('studio-pod-name').click()
    meeting = _studio_meeting(page, proposal)
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_text('Stuck')
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_have_text('No answer: Anna K., Mike K.')
    context.close()


@browser_journey
def test_a_far_ahead_proposal_is_not_flagged_too_early(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    # Made 8 days ahead 40 hours ago: the 48-hour threshold still applies.
    proposal = _open_proposal(pod, users['anna'], age=40 * HOUR, starts_in=8 * DAY - 40 * HOUR)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    row = _studio_row(page, pod)
    expect(row.get_by_test_id('studio-pod-next-meeting')).to_have_text('1 proposal waiting')
    expect(row.get_by_test_id('studio-pod-stuck')).to_have_count(0)

    row.get_by_test_id('studio-pod-name').click()
    meeting = _studio_meeting(page, proposal)
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_be_visible()
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_count(0)
    context.close()


@browser_journey
def test_staff_sees_a_proposal_that_can_no_longer_be_confirmed(django_server, browser):
    _course, cohort, users = _seed()
    pod = _trio(cohort, users)
    proposal = _proposal_with_answers(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    meeting = _studio_meeting(page, proposal)
    status = meeting.get_by_test_id('studio-pod-meeting-status')
    expect(status).to_contain_text('Proposed')
    expect(meeting.get_by_test_id('studio-pod-meeting-unconfirmable')).to_have_text("Can't be confirmed")
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_count(0)
    expect(meeting.get_by_test_id('studio-pod-meeting-cant')).to_contain_text('Raj S.')
    expect(meeting.get_by_test_id('studio-pod-meeting-cant')).to_contain_text('Mike K.')
    context.close()


@browser_journey
def test_the_unconfirmable_label_clears_once_the_pod_recovers(django_server, browser):
    _course, cohort, users = _seed()
    pod = _trio(cohort, users)
    proposal = _proposal_with_answers(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.locator(f'[data-testid="pod-meeting"][data-meeting-id="{proposal.pk}"]').get_by_test_id(
        'pod-meeting-going',
    ).click()
    expect(_messages(page)).to_contain_text('Meeting confirmed.')
    context.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    meeting = _studio_meeting(page, proposal)
    expect(meeting.get_by_test_id('studio-pod-meeting-status')).to_contain_text('Scheduled')
    expect(meeting.get_by_test_id('studio-pod-meeting-unconfirmable')).to_have_count(0)
    expect(page.get_by_text("Can't be confirmed")).to_have_count(0)
    context.close()


@browser_journey
def test_staff_sees_two_open_proposals_counted_once_per_series(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']], meeting_count=6)
    _open_proposal(pod, users['anna'], age=HOUR, starts_in=2 * DAY)
    _open_proposal(pod, users['mike'], age=HOUR, starts_in=3 * DAY, weeks=3)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-next-meeting')).to_have_text('2 proposals waiting')
    context.close()


@browser_journey
def test_a_pod_with_a_confirmed_meeting_still_shows_its_next_meeting(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    tomorrow = _hour(DAY)
    _meeting(pod, tomorrow, zone='UTC')
    _open_proposal(pod, users['anna'], age=HOUR, starts_in=7 * DAY)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    # Operator datetimes render in UTC (``TIME_ZONE``).
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-next-meeting')).to_have_text(
        f'Next {tomorrow.astimezone(datetime.UTC):%Y-%m-%d %H:%M}',
    )
    context.close()
