"""Weekly overlap view and the ``Plan your first meeting`` dashboard row
(issue #1928).

Fixtures come from ``test_pods_1918``: enrolled anna (Europe/Berlin), raj
(Asia/Kolkata), mike (America/New_York) and basic (Bea S.). Every time is
relative to now so the journeys never rot; the 7-day overlap always holds
exactly one Tuesday and one Thursday.
"""

import datetime
import os
import re
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pod_meetings_1919 import _meeting, _messages
from playwright_tests.test_pods_1918 import COURSE_SLUG, _page, _pod, _pod_url, _seed, _set_config, _windows
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

NUDGE_POD = 'Agents study group'
NUDGE_TITLE = f'Plan your first meeting with {NUDGE_POD}'
TUE, THU = 1, 3


def _evening_pod(cohort, users, members=('anna', 'raj', 'mike'), with_windows=None):
    """Berlin Tue/Thu 18-21, Kolkata Tue/Thu 21:30-23:30, London Tue 17-20.

    ``basic`` reuses anna's Berlin windows when a pod needs a third member.
    """
    windows = {
        'anna': ('Europe/Berlin', [(d, 18 * 60, 21 * 60, 'preferred') for d in (TUE, THU)]),
        'raj': ('Asia/Kolkata', [(d, 21 * 60 + 30, 23 * 60 + 30, 'preferred') for d in (TUE, THU)]),
        'mike': ('Europe/London', [(TUE, 17 * 60, 20 * 60, 'preferred')]),
        'basic': ('Europe/Berlin', [(d, 18 * 60, 21 * 60, 'preferred') for d in (TUE, THU)]),
    }
    for key in with_windows or members:
        _windows(users[key], *windows[key])
    return _pod(cohort, 'RAG evals', [users[k] for k in members], owner=users[members[0]])


def _no_page_overflow(page):
    return page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')


def _nudge_pod(cohort, users, *, days_ago=4, keys=('anna', 'mike')):
    from pods.models import PodMembership

    pod = _pod(cohort, NUDGE_POD, [users[k] for k in keys], owner=users[keys[0]])
    PodMembership.objects.filter(pod=pod).update(joined_at=timezone.now() - datetime.timedelta(days=days_ago))
    connection.close()
    return pod


def _broad_windows(users, keys):
    windows = {
        'anna': ('Europe/Berlin', 14 * 60, 23 * 60),
        'mike': ('America/New_York', 8 * 60, 16 * 60),
    }
    for key in keys:
        zone, start, end = windows[key]
        _windows(users[key], zone, [(d, start, end, 'preferred') for d in range(7)])


def _store_course_slugs_like_studio():
    """Store ``PODS_COURSE_SLUGS`` where Studio settings writes it.

    ``_seed`` writes the legacy donor row, which the Studio pods form does
    not render, so saving the group from Studio would blank the slugs.
    """
    from community_base.config.service import set as service_set

    from integrations.config import clear_config_cache

    service_set('PODS_COURSE_SLUGS', COURSE_SLUG, actor_ref='test', reason='issue 1928 fixture')
    clear_config_cache()
    connection.close()


def _dashboard(page, server):
    page.goto(server + '/', wait_until='domcontentloaded')


@pytest.mark.core
@browser_journey
def test_member_uses_the_weekly_overlap_to_spot_an_evening_across_timezones(django_server, browser):
    _course, cohort, users = _seed()
    pod = _evening_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    overlap = page.get_by_test_id('pod-weekly-overlap')
    table = page.get_by_test_id('pod-overlap-table')
    expect(table).to_be_hidden()
    overlap.get_by_text('See full weekly overlap').click()
    expect(table).to_be_visible()

    today = timezone.now().astimezone(ZoneInfo('Europe/Berlin'))
    dates = table.get_by_test_id('pod-overlap-date')
    expect(dates).to_have_count(7)
    expect(dates.first).to_have_text(f'{today:%a} {today:%b} {today.day}')

    tuesday = table.get_by_test_id('pod-overlap-cell').filter(
        has=page.get_by_text(re.compile(r'^Tuesday .+, 18:00, 3 of 3 free$')),
    )
    expect(tuesday).to_have_count(1)
    expect(tuesday.get_by_test_id('pod-overlap-count')).to_have_text('3')
    thursday = table.get_by_test_id('pod-overlap-label').filter(has_text=re.compile(r'^Thursday .+, 18:00, 2 of 3 free$'))
    expect(thursday).to_have_count(1)
    expect(page.get_by_test_id('pod-overlap-legend')).to_be_visible()
    assert _no_page_overflow(page)

    page.set_viewport_size({'width': 768, 'height': 900})
    expect(table).to_be_visible()
    assert _no_page_overflow(page)

    page.set_viewport_size({'width': 1280, 'height': 900})
    page.get_by_test_id('pod-slot-propose').first.click()
    expect(page.get_by_role('heading', name='Propose a meeting time')).to_be_visible()
    context.close()


@browser_journey
def test_overlap_is_honest_when_a_member_has_no_availability(django_server, browser):
    _course, cohort, users = _seed()
    pod = _evening_pod(cohort, users, with_windows=('anna', 'raj'))

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    page.get_by_test_id('pod-weekly-overlap').get_by_text('See full weekly overlap').click()
    expect(page.get_by_test_id('pod-overlap-based-on')).to_have_text(
        "Based on 2 of 3 members. Mike K. hasn't added availability yet.",
    )
    labels = page.get_by_test_id('pod-overlap-label').all_text_contents()
    assert labels and all(label.endswith(' of 2 free') for label in labels), labels
    context.close()


@browser_journey
def test_pod_of_one_has_no_overlap_accordion(django_server, browser):
    _course, cohort, users = _seed()
    _windows(users['anna'], 'Europe/Berlin', [(TUE, 18 * 60, 21 * 60, 'preferred')])
    pod = _pod(cohort, 'Solo pod', [users['anna']], owner=users['anna'])

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-times-needs-more')).to_have_text(
        'Suggested times appear when at least two members have added availability.',
    )
    expect(page.get_by_test_id('pod-weekly-overlap')).to_have_count(0)
    context.close()


@browser_journey
def test_cohort_member_browsing_another_pod_cannot_see_its_overlap(django_server, browser):
    _course, cohort, users = _seed()
    pod = _evening_pod(cohort, users, members=('anna', 'raj', 'basic'))

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-request-form')).to_be_visible()
    expect(page.get_by_test_id('pod-weekly-overlap')).to_have_count(0)
    expect(page.get_by_test_id('pod-overlap-table')).to_have_count(0)
    context.close()


@browser_journey
def test_phone_user_keeps_the_simple_suggested_times_list(django_server, browser):
    _course, cohort, users = _seed()
    pod = _evening_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-slot').first).to_be_visible()
    expect(page.get_by_test_id('pod-weekly-overlap')).to_be_hidden()
    expect(page.get_by_text('See full weekly overlap')).to_be_hidden()
    assert _no_page_overflow(page)
    context.close()


@pytest.mark.core
@browser_journey
def test_member_is_prompted_to_plan_the_first_meeting_and_the_prompt_clears(django_server, browser):
    _course, cohort, users = _seed()
    _broad_windows(users, ('anna', 'mike'))
    pod = _nudge_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    _dashboard(page, django_server)
    row = page.get_by_test_id('dashboard-pod-nudge-row')
    expect(row.get_by_test_id('dashboard-pod-nudge-title')).to_have_text(NUDGE_TITLE)
    expect(row.get_by_test_id('dashboard-pod-nudge-eyebrow')).to_have_text('Pod')
    row.get_by_test_id('dashboard-pod-nudge-link').click()
    expect(page).to_have_url(re.compile(rf'/home/pods/{pod.pk}#suggested-times$'))
    expect(page.get_by_test_id('pod-suggested-times')).to_be_in_viewport()

    page.get_by_test_id('pod-slot-propose').first.click()
    page.get_by_test_id('pod-meeting-form-submit').click()
    expect(_messages(page)).to_contain_text('Time proposed.')

    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-commitment-zones')).to_be_visible()
    expect(page.get_by_test_id('dashboard-pod-nudge-row')).to_have_count(0)
    context.close()


@browser_journey
def test_pod_that_moved_to_slack_is_not_nagged(django_server, browser):
    _course, cohort, users = _seed()
    from accounts.models import User

    for email, slack_id in (('anna@test.com', 'UANNA'), ('mike@test.com', 'UMIKE')):
        User.objects.filter(email=email).update(slack_member=True, slack_user_id=slack_id)
    connection.close()
    _set_config('SLACK_TEAM_ID', 'T0POD')
    pod = _nudge_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-pod-nudge-title')).to_have_text(NUDGE_TITLE)

    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    callout = page.get_by_test_id('pod-slack-callout')
    callout.get_by_test_id('pod-slack-input').fill('https://aishippinglabs.slack.com/archives/C0123456789')
    callout.get_by_role('button', name='Save link').click()
    expect(page.get_by_test_id('pod-slack-channel')).to_have_text('Slack channel - Open in Slack')

    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-pod-nudge-row')).to_have_count(0)
    context.close()
    _set_config('SLACK_TEAM_ID', None)


@browser_journey
def test_freshly_matched_pod_waits_until_staff_lowers_the_threshold(django_server, browser):
    _course, cohort, users = _seed()
    _store_course_slugs_like_studio()
    _nudge_pod(cohort, users, days_ago=1)

    context, page = _page(browser, 'mike@test.com')
    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-commitment-zones')).to_be_visible()
    expect(page.get_by_test_id('dashboard-pod-nudge-row')).to_have_count(0)

    staff_context, staff = _page(browser, 'admin@test.com')
    staff.goto(f'{django_server}/studio/settings/#community', wait_until='domcontentloaded')
    card = staff.locator('#integration-pods')
    card.locator('#field-PODS_FIRST_MEETING_NUDGE_DAYS').fill('0')
    card.get_by_role('button', name='Save Pods').click()
    expect(card.locator('[data-settings-source="PODS_FIRST_MEETING_NUDGE_DAYS"]')).to_have_attribute(
        'data-source-badge', 'db',
    )
    staff_context.close()

    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-pod-nudge-title')).to_have_text(NUDGE_TITLE)
    context.close()
    from community_base.config.service import unset

    for key in ('PODS_FIRST_MEETING_NUDGE_DAYS', 'PODS_COURSE_SLUGS'):
        unset(key, actor_ref='test', reason='issue 1928 fixture cleanup')
    _set_config('PODS_FIRST_MEETING_NUDGE_DAYS', None)


@browser_journey
def test_pod_whose_only_proposal_expired_is_nudged_again(django_server, browser):
    _course, cohort, users = _seed()
    pod = _nudge_pod(cohort, users)
    _meeting(pod, timezone.now() - datetime.timedelta(days=1), status='proposed')

    context, page = _page(browser, 'anna@test.com')
    _dashboard(page, django_server)
    expect(page.get_by_test_id('dashboard-pod-nudge-title')).to_have_text(NUDGE_TITLE)
    context.close()


@browser_journey
def test_member_with_nothing_else_this_week_still_sees_the_nudge(django_server, browser):
    _course, cohort, users = _seed()
    _nudge_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    _dashboard(page, django_server)
    week = page.get_by_test_id('dashboard-this-week')
    expect(week).to_be_visible()
    expect(week.get_by_test_id('dashboard-pod-nudge-row')).to_have_count(1)
    expect(week.get_by_test_id('dashboard-upcoming-event-row')).to_have_count(0)
    expect(week.get_by_test_id('dashboard-pod-meeting-row')).to_have_count(0)
    expect(week.get_by_test_id('dashboard-book-club-deadline')).to_have_count(0)
    expect(week.get_by_test_id('account-sprint-plan-card')).to_have_count(0)
    context.close()
