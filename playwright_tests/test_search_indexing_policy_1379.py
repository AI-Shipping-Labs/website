"""Browser-visible private/public indexing boundaries for issue #1379."""

import datetime
import importlib
import os
import uuid

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
    ensure_tiers,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]

SITE_URL = 'https://aishippinglabs.com'
ROBOTS_HEADER = 'noindex, nofollow, noarchive'


def _prepare_production(settings):
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    settings.SITE_BASE_URL = SITE_URL
    IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
    clear_config_cache()
    connection.close()


def _seed_article(slug):
    from django.db import connection

    from content.models import Article

    article = Article.objects.create(
        title='Public Browser Boundary Article',
        slug=slug,
        description='Public browser boundary metadata.',
        content_markdown='Public browser boundary body.',
        date=datetime.date(2026, 8, 9),
        published=True,
    )
    connection.close()
    return article


def _assert_noindex(page, response):
    assert response.headers['x-robots-tag'] == ROBOTS_HEADER
    robots = page.locator('meta[name="robots"]')
    assert robots.count() == 1
    assert robots.get_attribute('content') == 'noindex,nofollow,noarchive'
    assert page.locator('link[rel="canonical"]').count() == 1


def _assert_indexable(page, response):
    assert 'x-robots-tag' not in response.headers
    robots = page.locator('meta[name="robots"]')
    assert robots.count() == 1
    assert 'max-snippet:-1' in (robots.get_attribute('content') or '')


def _activated_user(email, tier_slug='free'):
    from django.db import connection

    user = create_user(email, tier_slug=tier_slug)
    user.account_activated = True
    user.save(update_fields=['account_activated'])
    connection.close()
    return user


def test_anonymous_public_pages_and_article_remain_indexable(
    django_server,
    page,
    settings,
    django_db_blocker,
):
    with django_db_blocker.unblock():
        _prepare_production(settings)
        article = _seed_article('public-browser-boundary-1379')

    for path in ('/', article.get_absolute_url(), '/membership'):
        response = page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
        assert response.status == 200
        _assert_indexable(page, response)


def test_account_entry_and_token_results_are_noindex_and_rendered(
    django_server,
    page,
    settings,
    django_db_blocker,
):
    with django_db_blocker.unblock():
        _prepare_production(settings)

    account_pages = (
        ('/accounts/login/', 'Sign in'),
        ('/accounts/register/', 'Create account'),
        ('/accounts/password-reset-request', 'Reset your password'),
    )
    for path, visible_text in account_pages:
        response = page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
        assert response.status == 200
        _assert_noindex(page, response)
        assert visible_text in page.locator('body').inner_text()

    for path in (
        '/api/password-reset?token=invalid',
        '/api/verify-email?token=invalid',
        '/api/unsubscribe?token=invalid',
        '/api/maven-email-opt-out?token=invalid',
    ):
        response = page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
        _assert_noindex(page, response)
        assert page.locator('body').inner_text().strip()


def test_member_private_pages_and_onboarding_do_not_hide_public_article(
    django_server,
    browser,
    settings,
    django_db_blocker,
):
    email = f'private-member-{uuid.uuid4().hex[:8]}@test.com'
    with django_db_blocker.unblock():
        _prepare_production(settings)
        article = _seed_article('signed-browser-boundary-1379')
        _activated_user(email, tier_slug='main')

        from django.apps import apps as django_apps
        from django.db import connection

        seed_module = importlib.import_module(
            'questionnaires.migrations.0003_seed_personas_and_onboarding',
        )
        seed_module.seed(django_apps, None)
        connection.close()

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        for path in (
            '/',
            '/account/',
            '/notifications',
            '/onboarding/',
            '/onboarding/questions',
        ):
            response = page.goto(
                f'{django_server}{path}',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            _assert_noindex(page, response)
            assert page.locator('body').inner_text().strip()

        response = page.goto(
            f'{django_server}{article.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        _assert_indexable(page, response)
        expect(page.get_by_role('heading', name=article.title)).to_be_visible()
    finally:
        context.close()


def test_checkout_feedback_states_are_private_but_plain_pricing_is_public(
    django_server,
    browser,
    page,
    settings,
    django_db_blocker,
):
    email = f'checkout-member-{uuid.uuid4().hex[:8]}@test.com'
    with django_db_blocker.unblock():
        _prepare_production(settings)
        ensure_tiers()
        _activated_user(email)

    context = auth_context(browser, email)
    dashboard_page = context.new_page()
    try:
        response = dashboard_page.goto(
            f'{django_server}/?checkout=success',
            wait_until='domcontentloaded',
        )
        _assert_noindex(dashboard_page, response)
        expect(dashboard_page.locator('#checkout-success-banner')).to_be_visible()

        response = page.goto(
            f'{django_server}/membership?checkout=cancelled',
            wait_until='domcontentloaded',
        )
        _assert_noindex(page, response)
        expect(page.locator('#checkout-cancelled-banner')).to_be_visible()

        response = page.goto(f'{django_server}/membership', wait_until='domcontentloaded')
        _assert_indexable(page, response)
    finally:
        context.close()


def test_sprint_voting_and_course_workspaces_cross_the_private_boundary(
    django_server,
    browser,
    settings,
    django_db_blocker,
):
    email = f'workspace-member-{uuid.uuid4().hex[:8]}@test.com'
    with django_db_blocker.unblock():
        _prepare_production(settings)
        member = _activated_user(email, tier_slug='main')

        from django.db import connection
        from django.utils import timezone

        from content.models import Course
        from plans.models import Plan, Sprint, SprintEnrollment
        from voting.models import Poll

        course = Course.objects.create(
            title='Public Workspace Boundary Course',
            slug='workspace-boundary-course-1379',
            description='Public course body.',
            status='published',
            peer_review_enabled=True,
            required_level=20,
        )
        poll = Poll.objects.create(
            title='Private Workspace Boundary Poll',
            poll_type='topic',
            status='open',
        )
        sprint = Sprint.objects.create(
            name='Public Sprint Boundary',
            slug='public-sprint-boundary-1379',
            description='Public sprint discovery body.',
            start_date=timezone.localdate() - datetime.timedelta(days=7),
            duration_weeks=6,
            status='active',
            min_tier_level=20,
        )
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(
            member=member,
            sprint=sprint,
            goal='Keep this workspace private',
        )
        paths = {
            'course': course.get_absolute_url(),
            'submit': f'{course.get_absolute_url()}/submit',
            'reviews': f'{course.get_absolute_url()}/reviews',
            'poll': poll.get_absolute_url(),
            'sprint': sprint.get_absolute_url(),
            'board': f'{sprint.get_absolute_url()}/board',
            'plan': f'{sprint.get_absolute_url()}/plan/{plan.pk}',
        }
        connection.close()

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        for key in ('sprint', 'course'):
            response = page.goto(
                f'{django_server}{paths[key]}',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            _assert_indexable(page, response)

        for key in ('board', 'plan', 'poll', 'submit', 'reviews'):
            response = page.goto(
                f'{django_server}{paths[key]}',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            _assert_noindex(page, response)
            assert page.locator('body').inner_text().strip()
    finally:
        context.close()


def test_staff_studio_and_admin_are_private_but_public_content_stays_indexable(
    django_server,
    browser,
    settings,
    django_db_blocker,
):
    email = f'indexing-staff-{uuid.uuid4().hex[:8]}@test.com'
    with django_db_blocker.unblock():
        _prepare_production(settings)
        article = _seed_article('staff-public-boundary-1379')
        create_staff_user(email)

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        for path in ('/studio/', '/studio/articles/'):
            response = page.goto(
                f'{django_server}{path}',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            _assert_noindex(page, response)

        admin_response = page.goto(
            f'{django_server}/admin/',
            wait_until='domcontentloaded',
        )
        assert admin_response.status == 200
        assert admin_response.headers['x-robots-tag'] == ROBOTS_HEADER

        response = page.goto(
            f'{django_server}{article.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        _assert_indexable(page, response)
    finally:
        context.close()


def test_event_detail_is_public_while_join_and_cancel_actions_are_private(
    django_server,
    browser,
    settings,
    django_db_blocker,
):
    email = f'event-member-{uuid.uuid4().hex[:8]}@test.com'
    with django_db_blocker.unblock():
        _prepare_production(settings)
        member = _activated_user(email, tier_slug='main')

        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventRegistration

        event = Event.objects.create(
            title='Public Event Boundary',
            slug='public-event-boundary-1379',
            description='Public event detail body.',
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            end_datetime=timezone.now() + datetime.timedelta(days=2, hours=1),
            status='upcoming',
            published=True,
            required_level=20,
        )
        EventRegistration.objects.create(event=event, user=member)
        detail_path = event.get_absolute_url()
        join_path = event.get_join_url()
        connection.close()

    context = auth_context(browser, email)
    page = context.new_page()
    try:
        response = page.goto(
            f'{django_server}{detail_path}',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        _assert_indexable(page, response)

        response = page.goto(
            f'{django_server}{join_path}',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        _assert_noindex(page, response)
        assert 'not available yet' in page.locator('body').inner_text().lower()

        response = page.goto(
            f'{django_server}/events/{event.slug}/cancel-registration'
            '?token=invalid',
            wait_until='domcontentloaded',
        )
        assert response.status == 200
        _assert_noindex(page, response)
        assert 'invalid' in page.locator('body').inner_text().lower()
    finally:
        context.close()
