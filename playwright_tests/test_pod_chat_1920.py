"""Pod chat member and operator journeys (issue #1920).

Fixtures come from ``test_pods_1918``: enrolled anna (Anna K., Main,
Europe/Berlin), raj (Raj S., Main, Asia/Kolkata), mike (Main, not in the
pod), basic (Bea S., Basic with course access); staff admin. Every time is
relative to now so the journeys never rot.
"""

import datetime
import os
import re

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.test_pods_1918 import _page, _pod, _pod_url, _seed, _set_config
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

COURSE_SLUG = 'pods-buildcamp'
NOTICE = 'Admins can read this chat. Do not share private details. Slack is better for ongoing conversation.'
INVITE = 'Introduce yourself: what are you building and what is your timezone?'


def _message(pod, author, body, *, minutes_ago=0):
    from pods.models import PodMessage

    message = PodMessage.objects.create(pod=pod, author=author, body=body)
    if minutes_ago:
        PodMessage.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
        )
    connection.close()
    return message


def _read_at(pod, user, minutes_ago):
    from pods.models import PodMembership

    PodMembership.objects.filter(pod=pod, user=user).update(
        chat_read_at=timezone.now() - datetime.timedelta(minutes=minutes_ago),
    )
    connection.close()


def _rag_pod(cohort, users, *, second='raj'):
    return _pod(cohort, 'RAG evals', [users['anna'], users[second]], owner=users['anna'])


def _chat(page):
    return page.get_by_test_id('pod-chat')


def _send(page, text):
    _chat(page).get_by_test_id('pod-chat-input').fill(text)
    _chat(page).get_by_test_id('pod-chat-send').click()


def _bodies(page):
    return _chat(page).get_by_test_id('pod-chat-body')


def _direct_post(page, server, pod, body):
    """POST like the browser form would, with the page's own CSRF token."""
    token = page.locator('input[name="csrfmiddlewaretoken"]').first.get_attribute('value')
    return page.request.post(
        f'{server}/courses/{COURSE_SLUG}/home/pods/{pod.pk}/messages',
        form={'body': body, 'csrfmiddlewaretoken': token},
        headers={'X-CSRFToken': token, 'Referer': page.url},
        max_redirects=0,
    )


def _row(page, pod):
    return page.locator(f'[data-testid="pod-row"][data-pod-id="{pod.pk}"]')


@pytest.mark.core
@browser_journey
def test_pod_member_introduces_themselves_in_an_empty_chat(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    chat = _chat(page)
    chat.scroll_into_view_if_needed()
    expect(chat.get_by_test_id('pod-chat-empty')).to_contain_text(INVITE)
    expect(chat.get_by_test_id('pod-chat-notice')).to_have_text(NOTICE)
    # The notice sits right above the message box.
    notice_box = chat.get_by_test_id('pod-chat-notice').bounding_box()
    form_box = chat.get_by_test_id('pod-chat-form').bounding_box()
    assert notice_box['y'] + notice_box['height'] <= form_box['y']

    _send(page, 'Hi, I am building a RAG eval harness, Berlin time')
    expect(page).to_have_url(re.compile(r'#chat$'))
    expect(_bodies(page)).to_have_text(['Hi, I am building a RAG eval harness, Berlin time'])
    expect(_chat(page).get_by_test_id('pod-chat-author')).to_have_text(['Anna K. (you)'])
    expect(_chat(page).get_by_test_id('pod-chat-time')).to_have_text(re.compile(r'Europe/Berlin$'))
    expect(_chat(page).get_by_test_id('pod-chat-empty')).to_have_count(0)
    context.close()


@browser_journey
def test_pod_mate_catches_up_on_new_messages_from_the_pods_tab(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _read_at(pod, users['raj'], 24 * 60)
    _message(pod, users['anna'], 'Pushed the eval harness skeleton', minutes_ago=120)
    _message(pod, users['anna'], 'Can we meet Thursday?', minutes_ago=60)

    context, page = _page(browser, 'raj@test.com')
    page.goto(f'{django_server}/courses/{COURSE_SLUG}/home/pods?cohort=4', wait_until='domcontentloaded')
    expect(_row(page, pod).get_by_test_id('pod-row-unread')).to_have_text('2 new messages')
    _row(page, pod).get_by_test_id('pod-row-action').click()
    expect(_bodies(page)).to_have_text(['Pushed the eval harness skeleton', 'Can we meet Thursday?'])

    page.go_back(wait_until='domcontentloaded')
    page.reload(wait_until='domcontentloaded')
    expect(_row(page, pod).get_by_test_id('pod-row-badge')).to_be_visible()
    expect(_row(page, pod).get_by_test_id('pod-row-unread')).to_have_count(0)
    context.close()


@browser_journey
def test_member_replies_with_a_link_and_a_multi_line_message(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _message(pod, users['anna'], 'Where is your repo?', minutes_ago=5)

    context, page = _page(browser, 'raj@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _send(page, 'Here it is:\nhttps://github.com/raj/evals')
    reply = _bodies(page).last
    expect(reply).to_have_text('Here it is:\nhttps://github.com/raj/evals')
    link = reply.get_by_role('link', name='https://github.com/raj/evals')
    expect(link).to_have_attribute('href', 'https://github.com/raj/evals')
    expect(link).to_have_attribute('rel', re.compile('nofollow'))
    two_lines = reply.bounding_box()['height'] > 30
    assert two_lines

    _send(page, '<b>bold?</b>')
    literal = _bodies(page).last
    expect(literal).to_have_text('<b>bold?</b>')
    expect(literal.locator('b')).to_have_count(0)
    context.close()


@browser_journey
def test_member_who_is_not_in_the_pod_cannot_read_or_post(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _message(pod, users['anna'], 'Our private plan for week 2')

    context, page = _page(browser, 'mike@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(page.get_by_test_id('pod-purpose')).to_be_visible()
    expect(page.get_by_test_id('pod-request-form')).to_be_visible()
    expect(page.get_by_test_id('pod-chat')).to_have_count(0)
    assert 'Our private plan for week 2' not in page.content()

    response = _direct_post(page, django_server, pod, 'let me in')
    assert response.status == 403
    assert response.text() == 'Only pod members can post in this chat.'
    context.close()

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_bodies(page)).to_have_text(['Our private plan for week 2'])
    context.close()


@browser_journey
def test_pod_that_moved_to_slack_still_lets_a_basic_member_chat(django_server, browser):
    from pods.models import Pod

    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users, second='basic')
    Pod.objects.filter(pk=pod.pk).update(slack_channel_url='https://team.slack.com/archives/C0123456789')
    connection.close()

    context, page = _page(browser, 'basic@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_chat(page).get_by_test_id('pod-chat-slack')).to_contain_text('This pod has moved to Slack.')
    expect(_chat(page).get_by_test_id('pod-chat-slack-open')).to_have_attribute(
        'href', 'https://team.slack.com/archives/C0123456789',
    )
    expect(_chat(page).get_by_test_id('pod-chat-input')).to_be_visible()
    _send(page, 'I am on Basic, so I will keep checking here')
    expect(_bodies(page)).to_have_text(['I am on Basic, so I will keep checking here'])
    context.close()


@browser_journey
def test_over_eager_member_hits_the_message_rate_limit(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _set_config('PODS_CHAT_MESSAGES_PER_HOUR', '2')
    try:
        context, page = _page(browser, 'anna@test.com')
        page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
        _send(page, 'first')
        expect(_bodies(page)).to_have_text(['first'])
        _send(page, 'second')
        expect(_bodies(page)).to_have_text(['first', 'second'])
        _send(page, 'one more thing')
        expect(_chat(page).get_by_test_id('pod-chat-error')).to_have_text(
            'You are sending messages quickly. Try again in a few minutes.',
        )
        expect(_bodies(page)).to_have_text(['first', 'second'])
        expect(_chat(page).get_by_test_id('pod-chat-input')).to_have_value('one more thing')
        context.close()
    finally:
        _set_config('PODS_CHAT_MESSAGES_PER_HOUR', None)


@browser_journey
def test_member_tries_to_send_an_empty_or_too_long_message(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)

    context, page = _page(browser, 'anna@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    _send(page, '    ')
    expect(_chat(page).get_by_test_id('pod-chat-error')).to_have_text('Write a message first.')
    expect(_bodies(page)).to_have_count(0)

    response = _direct_post(page, django_server, pod, 'x' * 1001)
    assert response.status == 302
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_chat(page).get_by_test_id('pod-chat-error')).to_have_text('Messages can be up to 1000 characters.')
    expect(_bodies(page)).to_have_count(0)
    context.close()


@pytest.mark.core
@browser_journey
def test_staff_moderator_hides_an_off_topic_message(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _message(pod, users['anna'], 'Kickoff notes are in the doc', minutes_ago=30)
    crypto = _message(pod, users['raj'], 'buy crypto at example.com', minutes_ago=20)
    _message(pod, users['anna'], 'See you Thursday', minutes_ago=10)

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
    section = page.get_by_test_id('studio-pod-chat')
    expect(section.get_by_test_id('studio-pod-chat-heading')).to_have_text('Chat (3 messages, 0 hidden)')
    row = section.locator(f'[data-testid="studio-pod-chat-message"][data-message-id="{crypto.pk}"]')
    expect(row.get_by_test_id('studio-pod-chat-author')).to_contain_text('Raj S.')
    expect(row.get_by_test_id('studio-pod-chat-email')).to_have_text('raj@test.com')
    row.get_by_test_id('studio-pod-chat-hide').click()
    expect(page.get_by_text('Message hidden.')).to_be_visible()
    expect(row.get_by_test_id('studio-pod-chat-hidden')).to_contain_text('Hidden by admin@test.com')
    expect(section.get_by_test_id('studio-pod-chat-heading')).to_have_text('Chat (3 messages, 1 hidden)')
    context.close()

    member, member_page = _page(browser, 'anna@test.com')
    member_page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_bodies(member_page)).to_have_text(['Kickoff notes are in the doc', 'See you Thursday'])

    context, page = _page(browser, 'admin@test.com')
    page.goto(f'{django_server}/studio/pods/{pod.pk}/#chat', wait_until='domcontentloaded')
    page.locator(
        f'[data-testid="studio-pod-chat-message"][data-message-id="{crypto.pk}"]',
    ).get_by_test_id('studio-pod-chat-restore').click()
    expect(page.get_by_text('Message restored.')).to_be_visible()
    context.close()

    member_page.reload(wait_until='domcontentloaded')
    expect(_bodies(member_page)).to_have_text(
        ['Kickoff notes are in the doc', 'buy crypto at example.com', 'See you Thursday'],
    )
    member.close()


@browser_journey
def test_staff_reads_a_pod_chat_from_the_member_page_without_posting(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _message(pod, users['anna'], 'Week 1 retro: shipped the loader')

    context, page = _page(browser, 'admin@test.com')
    page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
    expect(_bodies(page)).to_have_text(['Week 1 retro: shipped the loader'])
    expect(_chat(page).get_by_test_id('pod-chat-notice')).to_have_text(NOTICE)
    expect(_chat(page).get_by_test_id('pod-chat-form')).to_have_count(0)
    expect(_chat(page).get_by_test_id('pod-chat-read-only')).to_have_text(
        'You can read this chat. Only pod members can post.',
    )
    context.close()


@browser_journey
def test_operator_switches_chat_off_for_the_pilot(django_server, browser):
    _course, cohort, users = _seed()
    pod = _rag_pod(cohort, users)
    _message(pod, users['raj'], 'Hello from Raj')
    _set_config('PODS_CHAT_ENABLED', 'false')
    try:
        context, page = _page(browser, 'anna@test.com')
        page.goto(f'{django_server}/courses/{COURSE_SLUG}/home/pods?cohort=4', wait_until='domcontentloaded')
        expect(_row(page, pod).get_by_test_id('pod-row-badge')).to_be_visible()
        expect(_row(page, pod).get_by_test_id('pod-row-unread')).to_have_count(0)
        page.goto(_pod_url(django_server, pod), wait_until='domcontentloaded')
        expect(page.get_by_test_id('pod-detail')).to_be_visible()
        expect(page.get_by_test_id('pod-chat')).to_have_count(0)
        context.close()

        context, page = _page(browser, 'admin@test.com')
        page.goto(f'{django_server}/studio/pods/{pod.pk}/', wait_until='domcontentloaded')
        expect(page.get_by_test_id('studio-pod-chat-body')).to_have_text(['Hello from Raj'])
        context.close()
    finally:
        _set_config('PODS_CHAT_ENABLED', None)
