"""Keyboard focus and 44px action coverage for issue #1224."""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
    ensure_tiers,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.visual_regression,
]

MIN_TARGET_HEIGHT = 44


def _email(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:8]}@example.com'


def _tab_to(page, locator, *, max_tabs=100):
    """Reach ``locator`` through real Tab presses, never locator.focus()."""
    for _ in range(max_tabs):
        page.keyboard.press('Tab')
        if locator.evaluate('el => el === document.activeElement'):
            return
    raise AssertionError(f'Could not reach {locator} after {max_tabs} Tab presses')


def _assert_keyboard_focus_and_height(page, locator):
    locator.wait_for(state='visible')
    element = locator.element_handle()
    page.wait_for_function(
        "el => getComputedStyle(el).minHeight === '44px'",
        arg=element,
    )
    _tab_to(page, locator)
    page.wait_for_function(
        "el => getComputedStyle(el).boxShadow !== 'none'",
        arg=element,
    )
    box = locator.bounding_box()
    assert box is not None
    assert box['height'] >= MIN_TARGET_HEIGHT


def _configure_oauth(db_blocker):
    with db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        from django.db import connection

        SocialApp.objects.all().delete()
        site = Site.objects.get_current()
        for provider, label in (
            ('google', 'Google'),
            ('github', 'GitHub'),
            ('slack', 'Slack'),
        ):
            app = SocialApp.objects.create(
                provider=provider,
                name=label,
                client_id=f'{provider}-1224',
                secret='secret-1224',
            )
            app.sites.add(site)
        connection.close()


def test_auth_primary_and_oauth_actions_are_keyboard_accessible(
    django_server, page, django_db_blocker
):
    email = _email('login-a11y')
    with django_db_blocker.unblock():
        ensure_tiers()
        create_user(email=email, password=DEFAULT_PASSWORD)
    _configure_oauth(django_db_blocker)

    page.goto(f'{django_server}/accounts/login/', wait_until='domcontentloaded')
    slack_login = page.locator('[data-testid="oauth-slack-action"]')
    _assert_keyboard_focus_and_height(page, slack_login)
    page.locator('#login-email').fill(email)
    page.locator('#login-password').fill(DEFAULT_PASSWORD)
    page.locator('#login-password').focus()
    login_submit = page.locator('#login-submit')
    _assert_keyboard_focus_and_height(page, login_submit)
    page.keyboard.press('Enter')
    page.wait_for_url(f'{django_server}/', timeout=10000)

    page.context.clear_cookies()
    page.goto(
        f'{django_server}/accounts/register/?next=%2Fprojects%3Ftag%3Dpython',
        wait_until='domcontentloaded',
    )
    register_submit = page.locator('#register-submit')
    oauth_actions = [
        page.locator(f'[data-testid="oauth-{provider}-action"]')
        for provider in ('google', 'github')
    ]
    for action in (register_submit, *oauth_actions):
        _assert_keyboard_focus_and_height(page, action)
    expect(oauth_actions[0]).to_have_attribute(
        'href',
        '/accounts/google/login/?next=/projects%3Ftag%3Dpython',
    )

    page.route(
        '**/accounts/google/login/**',
        lambda route: route.fulfill(status=204),
    )
    # Move backwards to Google from the final GitHub action using the keyboard.
    page.keyboard.press('Shift+Tab')
    assert oauth_actions[0].evaluate('el => el === document.activeElement')
    with page.expect_request('**/accounts/google/login/**') as request_info:
        page.keyboard.press('Enter')
    assert 'next=/projects%3Ftag%3Dpython' in request_info.value.url


def test_password_reset_submit_is_focused_and_activates_by_keyboard(
    django_server, page
):
    page.goto(
        f'{django_server}/accounts/password-reset-request',
        wait_until='domcontentloaded',
    )
    page.locator('#password-reset-email').fill(_email('reset-a11y'))
    page.locator('#password-reset-email').focus()
    submit = page.locator('#password-reset-request-submit')
    _assert_keyboard_focus_and_height(page, submit)
    page.keyboard.press('Enter')
    expect(page.locator('#password-reset-request-success')).to_be_visible()
    expect(page.locator('#password-reset-request-success')).to_contain_text(
        'If an account exists for that email'
    )


def test_project_difficulty_can_be_selected_and_cleared_by_keyboard(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        from django.db import connection

        from content.models import Project

        Project.objects.all().delete()
        for difficulty, slug in (
            ('beginner', 'beginner-a11y-1224'),
            ('intermediate', 'intermediate-a11y-1224'),
            ('advanced', 'advanced-a11y-1224'),
        ):
            Project.objects.create(
                title=f'{difficulty.title()} keyboard project',
                slug=slug,
                description='Keyboard filter fixture',
                date=timezone.localdate(),
                difficulty=difficulty,
                tags=['python'],
                published=True,
            )
        connection.close()

    page.goto(
        f'{django_server}/projects?tag=python', wait_until='domcontentloaded'
    )
    intermediate = page.locator(
        '[data-testid="project-difficulty-intermediate"]'
    )
    _assert_keyboard_focus_and_height(page, intermediate)
    page.keyboard.press('Enter')
    page.wait_for_url('**/projects?difficulty=intermediate&tag=python')
    expect(intermediate).to_have_attribute('aria-current', 'page')
    expect(page.get_by_text('Intermediate keyboard project')).to_be_visible()
    expect(page.get_by_text('Beginner keyboard project')).to_have_count(0)

    clear = page.locator('[data-testid="project-difficulty-clear"]')
    _assert_keyboard_focus_and_height(page, clear)
    page.keyboard.press('Enter')
    page.wait_for_url('**/projects?tag=python')
    expect(page.get_by_text('Intermediate keyboard project')).to_be_visible()
    expect(page.get_by_text('Beginner keyboard project')).to_be_visible()


def test_certificate_and_about_ctas_keep_keyboard_destinations(
    django_server, browser
):
    from django.db import connection

    from content.models import Course, CourseCertificate

    ensure_tiers()
    user = create_user(_email('certificate-a11y'), tier_slug='free')
    course = Course.objects.create(
        title='Accessible certificate course',
        slug='accessible-certificate-course-1224',
        status='published',
    )
    certificate = CourseCertificate.objects.create(
        user=user,
        course=course,
        pdf_url='https://example.invalid/certificate-1224.pdf',
    )
    connection.close()

    context = browser.new_context()
    context.route('https://example.invalid/**', lambda route: route.fulfill(status=200))
    page = context.new_page()
    try:
        page.goto(
            f'{django_server}/certificates/{certificate.id}',
            wait_until='domcontentloaded',
        )
        download = page.locator('[data-testid="certificate-pdf-link"]')
        _assert_keyboard_focus_and_height(page, download)
        expect(download).to_have_attribute('target', '_blank')
        expect(download).to_have_attribute('rel', 'noopener noreferrer')
        with page.expect_popup() as popup_info:
            page.keyboard.press('Enter')
        popup = popup_info.value
        popup.wait_for_url('https://example.invalid/certificate-1224.pdf')
        popup.close()

        page.goto(f'{django_server}/about', wait_until='domcontentloaded')
        pricing = page.locator('[data-testid="about-pricing-cta"]')
        _assert_keyboard_focus_and_height(page, pricing)
        page.keyboard.press('Enter')
        page.wait_for_url(f'{django_server}/pricing')
    finally:
        context.close()


def _create_series():
    from django.db import connection

    from events.models import Event, EventSeries

    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    series = EventSeries.objects.create(
        name='Accessible office hours',
        slug=f'accessibility-series-{uuid.uuid4().hex[:8]}',
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone='UTC',
    )
    Event.objects.create(
        title='Accessible office hours — Session 1',
        slug=f'{series.slug}-session-1',
        start_datetime=timezone.now() + timedelta(days=7),
        end_datetime=timezone.now() + timedelta(days=7, hours=1),
        status='upcoming',
        origin='studio',
        required_level=0,
        event_series=series,
        series_position=1,
    )
    connection.close()
    return series


def test_series_register_and_cancel_actions_work_by_keyboard(
    django_server, browser
):
    from django.db import connection

    ensure_tiers()
    series = _create_series()

    anonymous_context = browser.new_context()
    anonymous_page = anonymous_context.new_page()
    anonymous_page.goto(
        f'{django_server}{series.get_absolute_url()}',
        wait_until='domcontentloaded',
    )
    login_cta = anonymous_page.locator(
        '[data-testid="series-register-login-cta"]'
    )
    _assert_keyboard_focus_and_height(anonymous_page, login_cta)
    anonymous_page.keyboard.press('Enter')
    anonymous_page.wait_for_url('**/accounts/login/**')
    assert series.get_absolute_url() in anonymous_page.url
    anonymous_context.close()

    email = _email('series-member-a11y')
    create_user(email, tier_slug='main')
    context = auth_context(browser, email)
    page = context.new_page()
    try:
        page.goto(
            f'{django_server}{series.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        register = page.locator('[data-testid="series-register-button"]')
        _assert_keyboard_focus_and_height(page, register)
        page.keyboard.press('Enter')
        cancel = page.locator('[data-testid="series-cancel-button"]')
        cancel.wait_for(state='visible')
        _assert_keyboard_focus_and_height(page, cancel)
        page.once('dialog', lambda dialog: dialog.accept())
        page.keyboard.press('Enter')
        expect(page.locator('[data-testid="series-register-button"]')).to_be_visible()
    finally:
        context.close()
        connection.close()


def _create_poll(*, poll_type='topic', allow_proposals=False):
    from django.db import connection

    from voting.models import Poll, PollOption, PollVote

    PollVote.objects.all().delete()
    PollOption.objects.all().delete()
    Poll.objects.all().delete()
    poll = Poll.objects.create(
        title='Accessible poll',
        poll_type=poll_type,
        status='open',
        allow_proposals=allow_proposals,
        max_votes_per_user=2,
    )
    option = PollOption.objects.create(poll=poll, title='Keyboard option')
    connection.close()
    return poll, option


def test_gated_poll_pricing_action_works_by_keyboard(django_server, browser):
    ensure_tiers()
    email = _email('poll-free-a11y')
    create_user(email, tier_slug='free')
    poll, _ = _create_poll()
    context = auth_context(browser, email)
    page = context.new_page()
    try:
        page.goto(f'{django_server}/vote/{poll.id}', wait_until='domcontentloaded')
        pricing = page.locator('[data-testid="poll-pricing-cta"]')
        _assert_keyboard_focus_and_height(page, pricing)
        page.keyboard.press('Enter')
        page.wait_for_url(f'{django_server}/pricing')
    finally:
        context.close()


def test_poll_vote_and_proposal_actions_work_by_keyboard(django_server, browser):
    ensure_tiers()
    email = _email('poll-main-a11y')
    create_user(email, tier_slug='main')
    poll, option = _create_poll(allow_proposals=True)
    context = auth_context(browser, email)
    page = context.new_page()
    try:
        page.goto(f'{django_server}/vote/{poll.id}', wait_until='domcontentloaded')
        vote = page.locator(f'.vote-btn[data-option-id="{option.id}"]')
        _assert_keyboard_focus_and_height(page, vote)
        page.keyboard.press('Enter')
        expect(vote).to_have_attribute('data-voted', 'true')
        expect(vote).to_contain_text('Voted')

        page.locator('#proposal-title').fill('Keyboard proposal')
        page.locator('#proposal-description').fill('Submitted without a mouse')
        proposal = page.locator('[data-testid="poll-proposal-submit"]')
        _assert_keyboard_focus_and_height(page, proposal)
        page.keyboard.press('Enter')
        expect(page.locator('#propose-message')).to_contain_text(
            'Proposal submitted!'
        )
        expect(page.get_by_text('Keyboard proposal')).to_be_visible(timeout=10000)
    finally:
        context.close()
