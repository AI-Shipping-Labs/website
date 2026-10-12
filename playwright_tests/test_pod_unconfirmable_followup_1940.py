"""Follow-ups to pod proposals that can no longer be confirmed (issue #1940).

Suggested times points at the meeting above instead of asking members to
confirm a dead proposal; a proposal replaced through ``Propose <time>
instead`` says so on its cancelled row; a moved proposal reads
``Proposed by``, ``Moved by``, then the waiting or can't-be-confirmed line.

Fixtures come from ``test_pods_1918``: enrolled anna (Europe/Berlin), raj
(Asia/Kolkata) and mike (America/New_York). Every time is created relative
to now so the journeys never rot.
"""

import datetime
import os
import re

import pytest
from django.db import connection
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import _meeting_pod, _messages
from playwright_tests.test_pod_meetings_polish_1934 import _broad_windows
from playwright_tests.test_pod_unconfirmable_1937 import LINE, _propose, _row_for
from playwright_tests.test_pods_1918 import _page, _pod_url, _seed
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

DEAD = "The proposed time can't be confirmed. Propose another time from the meeting above."
LIVE = 'Confirm or cancel the proposed time before proposing another.'
REPLACED = 'Replaced by a new proposed time.'
META = (
    'pod-meeting-replaced', 'pod-meeting-proposed-by', 'pod-meeting-moved', 'pod-meeting-waiting',
    'pod-meeting-unconfirmable', 'pod-meeting-cant',
)


def _pod_of_three(users):
    _broad_windows(users, ('anna', 'raj', 'mike'))
    return _meeting_pod(users['cohort'], [users['anna'], users['raj'], users['mike']])


def _seeded():
    _course, cohort, users = _seed()
    return {**users, 'cohort': cohort}


def _moved(pod, proposal, mover, *, cant=()):
    """``mover`` moves ``proposal`` two days later; then each of ``cant`` can't make it."""
    from pods.models import MEETING_RESPONSE_CANT
    from pods.services.meetings import move_meeting, respond_to_meeting

    move_meeting(proposal, mover, proposal.starts_at + datetime.timedelta(days=2))
    for user in cant:
        respond_to_meeting(proposal, user, MEETING_RESPONSE_CANT)
    connection.close()


def _meta_lines(row):
    """``(testid, text)`` for the row's meta lines, top to bottom."""
    selector = ', '.join(f'[data-testid="{testid}"]' for testid in META)
    return [
        (line.get_attribute('data-testid'), line.inner_text().strip())
        for line in row.locator(selector).all()
    ]


@browser_journey
def test_student_with_a_dead_proposal_is_pointed_back_to_the_meeting(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    blocked = page.get_by_test_id('pod-times-propose-blocked')
    blocked.scroll_into_view_if_needed()
    expect(blocked).to_have_text(DEAD)
    expect(page.get_by_text(LIVE)).to_have_count(0)
    expect(page.get_by_test_id('pod-slot-propose')).to_have_count(0)
    expect(page.get_by_test_id('pod-times-propose-custom')).to_have_count(0)
    expect(_row_for(page, proposal).get_by_test_id('pod-meeting-badge')).to_have_text("Can't be confirmed")
    context.close()


@browser_journey
def test_student_with_a_live_proposal_still_sees_the_confirm_or_cancel_rule(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'], cant=[users['mike']])

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-times-propose-blocked')).to_have_text(LIVE)

    _row_for(page, proposal).get_by_test_id('pod-meeting-cant-make-it').click()
    expect(_messages(page)).to_contain_text("This time can't be confirmed")
    expect(page.get_by_test_id('pod-times-propose-blocked')).to_have_text(DEAD)
    context.close()


@pytest.mark.core
@browser_journey
def test_student_replaces_a_dead_proposal_and_sees_what_happened_to_the_old_one(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod) + '#meetings', wait_until='domcontentloaded')
    offer = _row_for(page, proposal).get_by_test_id('pod-meeting-offer-propose')
    expect(offer).to_have_text(re.compile(r'^Propose .+ instead$'))
    offer.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    expect(page).to_have_url(re.compile(rf'meetings/new\?start=.+&replaces={proposal.pk}$'))
    expect(page.get_by_test_id('pod-meeting-form-time')).to_be_visible()
    repeat = page.get_by_test_id('pod-meeting-repeat')
    if repeat.count():
        repeat.uncheck()
    page.get_by_test_id('pod-meeting-form-submit').click()

    expect(_messages(page)).to_contain_text('Time proposed.')
    new_row = page.locator('[data-testid="pod-meeting"][data-meeting-status="proposed"]')
    expect(new_row).to_have_count(1)
    expect(new_row.get_by_test_id('pod-meeting-badge')).to_have_text('Proposed - 1 of 2 needed')
    expect(new_row.get_by_test_id('pod-meeting-replaced')).to_have_count(0)
    old_row = _row_for(page, proposal)
    expect(old_row.get_by_test_id('pod-meeting-badge')).to_have_text('Cancelled')
    expect(old_row.get_by_test_id('pod-meeting-replaced')).to_have_text(REPLACED)
    context.close()


@browser_journey
def test_student_who_cancels_by_hand_sees_a_plain_cancelled_row(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _row_for(page, proposal).get_by_test_id('pod-meeting-cancel').click()
    expect(_messages(page)).to_contain_text('Meeting cancelled.')

    page.get_by_test_id('pod-slot-propose').first.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    repeat = page.get_by_test_id('pod-meeting-repeat')
    if repeat.count():
        repeat.uncheck()
    page.get_by_test_id('pod-meeting-form-submit').click()

    expect(_messages(page)).to_contain_text('Time proposed.')
    old_row = _row_for(page, proposal)
    expect(old_row.get_by_test_id('pod-meeting-badge')).to_have_text('Cancelled')
    expect(old_row.get_by_test_id('pod-meeting-replaced')).to_have_count(0)
    expect(page.get_by_text(REPLACED)).to_have_count(0)
    context.close()


@browser_journey
def test_student_reads_a_moved_dead_proposal_top_to_bottom(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'])
    _moved(pod, proposal, users['mike'], cant=[users['raj'], users['mike']])

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-badge')).to_have_text("Can't be confirmed")
    lines = _meta_lines(row)
    assert [testid for testid, _text in lines] == [
        'pod-meeting-proposed-by', 'pod-meeting-moved', 'pod-meeting-unconfirmable', 'pod-meeting-cant',
    ], lines
    assert lines[0][1] == 'Proposed by Anna K.'
    assert lines[1][1] == 'Moved by Mike K. today'
    assert lines[2][1] == LINE
    assert lines[3][1].startswith("Can't make it: ")
    context.close()


@browser_journey
def test_student_reads_a_moved_proposal_still_waiting_on_answers(django_server, browser):
    users = _seeded()
    pod = _pod_of_three(users)
    proposal = _propose(pod, users['anna'])
    _moved(pod, proposal, users['mike'])

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    row = _row_for(page, proposal)
    expect(row.get_by_test_id('pod-meeting-waiting')).to_contain_text('Waiting on you')
    lines = _meta_lines(row)
    assert [testid for testid, _text in lines] == [
        'pod-meeting-proposed-by', 'pod-meeting-moved', 'pod-meeting-waiting',
    ], lines
    assert lines[0][1] == 'Proposed by Anna K.'
    assert lines[1][1] == 'Moved by Mike K. today'
    assert lines[2][1].startswith('Waiting on you')
    context.close()
