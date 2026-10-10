"""Stuck pod meeting proposals in Studio (issue #1935): the ``Stuck
proposal`` badge on ``/studio/pods/``, the ``Stuck`` badge and ``No
answer`` line on the pod page, the threshold setting, and how answering or
moving a proposal clears it.

Fixtures come from ``test_pods_1918``: enrolled anna (Anna K., Europe/Berlin)
and mike (Mike K., America/New_York); staff admin. Every time is relative
to now so the journeys never rot.
"""

import datetime
import os

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import _meeting_pod, _messages
from playwright_tests.test_pod_meetings_polish_1934 import _broad_windows
from playwright_tests.test_pods_1918 import _page, _pod, _pod_url, _seed, _set_config
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _proposal(pod, proposer, *, age, days_ahead=4):
    """A proposal by ``proposer`` created ``age`` ago for ``days_ahead`` days from now."""
    from pods.models import PodMeeting
    from pods.services.meetings import propose_meeting

    start = (timezone.now() + datetime.timedelta(days=days_ahead)).replace(minute=0, second=0, microsecond=0)
    [proposal] = propose_meeting(pod, proposer, start)
    PodMeeting.objects.filter(pk=proposal.pk).update(created_at=timezone.now() - age)
    connection.close()
    return proposal


def _studio_row(page, pod):
    return page.locator(f'[data-testid="studio-pod-row"][data-pod-id="{pod.pk}"]')


def _stuck_pair(cohort, users):
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    _proposal(pod, users['anna'], age=datetime.timedelta(days=3))
    return pod


@pytest.mark.core
@browser_journey
def test_staff_spots_a_stuck_proposal_and_sees_who_has_not_answered(django_server, browser):
    _course, cohort, users = _seed()
    stuck = _stuck_pair(cohort, users)
    fresh = _pod(cohort, 'Morning crew', [users['raj'], users['basic']], owner=users['raj'])
    _proposal(fresh, users['raj'], age=datetime.timedelta(hours=2))

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    expect(_studio_row(page, stuck).get_by_test_id('studio-pod-stuck')).to_have_text('Stuck proposal')
    expect(_studio_row(page, fresh).get_by_test_id('studio-pod-stuck')).to_have_count(0)

    _studio_row(page, stuck).get_by_test_id('studio-pod-name').click()
    meeting = page.get_by_test_id('studio-pod-meeting')
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_text('Stuck')
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_have_text('No answer: Mike K.')
    context.close()


@browser_journey
def test_proposal_stops_being_stuck_once_the_quiet_member_answers(django_server, browser):
    _course, cohort, users = _seed()
    pod = _stuck_pair(cohort, users)

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-meeting-going').click()
    expect(_messages(page)).to_contain_text('Meeting confirmed.')
    context.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-stuck')).to_have_count(0)
    _studio_row(page, pod).get_by_test_id('studio-pod-name').click()
    meeting = page.get_by_test_id('studio-pod-meeting')
    expect(meeting.get_by_test_id('studio-pod-meeting-status')).to_contain_text('Scheduled')
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_count(0)
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_have_count(0)
    context.close()


@browser_journey
def test_operator_lowers_the_stuck_threshold_in_studio_settings(django_server, browser):
    _course, cohort, users = _seed()
    pod = _meeting_pod(cohort, [users['anna'], users['mike']])
    _proposal(pod, users['anna'], age=datetime.timedelta(hours=30))

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-name')).to_be_visible()
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-stuck')).to_have_count(0)

    page.goto(f'{django_server}/studio/settings/#community', wait_until='domcontentloaded')
    card = page.locator('#integration-pods')
    card.locator('#field-PODS_STUCK_PROPOSAL_HOURS').fill('24')
    card.get_by_role('button', name='Save Pods').click()
    expect(card.locator('[data-settings-source="PODS_STUCK_PROPOSAL_HOURS"]')).to_have_attribute(
        'data-source-badge', 'db',
    )

    page.goto(f'{django_server}/studio/pods/', wait_until='domcontentloaded')
    expect(_studio_row(page, pod).get_by_test_id('studio-pod-stuck')).to_have_text('Stuck proposal')
    context.close()
    _set_config('PODS_STUCK_PROPOSAL_HOURS', None)


@browser_journey
def test_a_moved_proposal_is_no_longer_stuck(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna', 'mike'))
    pod = _stuck_pair(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-meeting-change').click()
    expect(page.get_by_role('heading', name='Change meeting time')).to_be_visible()
    choices = page.get_by_test_id('pod-move-choice')
    expect(choices.first).to_be_visible()
    choices.last.check()
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Meeting moved.')
    context.close()

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    meeting = page.get_by_test_id('studio-pod-meeting')
    expect(meeting.get_by_test_id('studio-pod-meeting-no-answer')).to_have_text('No answer: Mike K.')
    expect(meeting.get_by_test_id('studio-pod-meeting-stuck')).to_have_count(0)
    context.close()
