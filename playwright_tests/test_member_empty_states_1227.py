"""Behavior-first Playwright journeys for shared empty states (issue #1227).

These tests assert copy, state markers, reset navigation, and access control.
They intentionally do not inspect Tailwind/layout classes, so they are not
``visual_regression`` tests. The tester-owned light/dark desktop/mobile
screenshot matrix remains separate from this deploy-safe behavior coverage.
"""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _reset_surfaces():
    from django.db import connection

    from content.models import Article, Course, CuratedLink, Download, Project
    from events.models import Event
    from voting.models import Poll

    Poll.objects.all().delete()
    Event.objects.all().delete()
    Article.objects.all().delete()
    Course.objects.all().delete()
    Download.objects.all().delete()
    CuratedLink.objects.all().delete()
    Project.objects.all().delete()
    connection.close()


def _create_article(title='Tagged article 1227', tags=None):
    from django.db import connection

    from content.models import Article

    article = Article.objects.create(
        title=title,
        slug=title.lower().replace(' ', '-'),
        date=datetime.date(2026, 7, 13),
        tags=tags or [],
        published=True,
    )
    connection.close()
    return article


def _create_link(title='Curated link 1227', tags=None):
    from django.db import connection

    from content.models import CuratedLink

    link = CuratedLink.objects.create(
        item_id=title.lower().replace(' ', '-'),
        title=title,
        url='https://example.com/curated-1227',
        category='other',
        tags=tags or [],
        published=True,
    )
    connection.close()
    return link


def _create_project(title='Beginner project 1227', tags=None):
    from django.db import connection

    from content.models import Project

    project = Project.objects.create(
        title=title,
        slug=title.lower().replace(' ', '-'),
        description='A visible project.',
        date=datetime.date(2026, 7, 13),
        difficulty='beginner',
        tags=tags or [],
        published=True,
    )
    connection.close()
    return project


def _create_gated_poll(title='Hidden Main poll 1227'):
    from django.db import connection

    from voting.models import Poll

    poll = Poll.objects.create(
        title=title,
        poll_type='topic',
        status='open',
    )
    connection.close()
    return poll


def _goto(page, url):
    response = page.goto(url, wait_until='domcontentloaded')
    assert response is not None
    assert response.status == 200
    expect(page.locator('main')).to_be_visible()
    return response


def test_visitor_understands_empty_tag_library(django_server, page):
    _reset_surfaces()

    _goto(page, f'{django_server}/tags')

    empty = page.get_by_test_id('member-empty-state')
    expect(empty).to_be_visible()
    expect(empty).to_have_attribute('data-empty-kind', 'fresh')
    expect(empty).to_contain_text('No tags yet')
    expect(empty).to_contain_text("Content will be tagged as it's published.")
    expect(empty.get_by_role('link')).to_have_count(0)


def test_visitor_recovers_from_unmatched_tag_url(django_server, page):
    _reset_surfaces()
    _create_article(tags=['agents'])

    _goto(page, f'{django_server}/tags/nonexistent')

    empty = page.get_by_test_id('member-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'filter')
    expect(empty).to_contain_text('No content found')
    expect(empty).to_contain_text(
        'No published content uses the "nonexistent" tag yet.',
    )
    empty.get_by_role('link', name='Browse all tags').click()
    page.wait_for_url(f'{django_server}/tags')
    expect(page.get_by_role('link', name='agents 1')).to_be_visible()


def test_visitor_sees_fresh_resources_state(django_server, page):
    _reset_surfaces()

    _goto(page, f'{django_server}/resources')

    empty = page.get_by_test_id('member-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'fresh')
    expect(empty).to_contain_text('No curated links yet')
    expect(empty).to_contain_text(
        'Check back soon for workshops, courses, and references.',
    )
    expect(empty.get_by_role('link')).to_have_count(0)


def test_visitor_clears_resources_topic_with_no_matches(django_server, page):
    _reset_surfaces()
    _create_link(tags=['python'])

    _goto(page, f'{django_server}/resources?tag=no-match')

    empty = page.get_by_test_id('member-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'filter')
    expect(empty).to_contain_text('No links found')
    empty.get_by_role('link', name='View all links').click()
    page.wait_for_url(f'{django_server}/resources')
    expect(page.get_by_text('Curated link 1227', exact=True)).to_be_visible()


def test_visitor_sees_fresh_projects_state(django_server, page):
    _reset_surfaces()

    _goto(page, f'{django_server}/projects')

    empty = page.get_by_test_id('projects-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'fresh')
    expect(empty).to_contain_text('No project ideas yet')
    expect(empty).to_contain_text(
        'Check back soon for pet and portfolio project ideas.',
    )
    expect(empty.get_by_role('link')).to_have_count(0)
    expect(page.get_by_test_id('member-empty-state')).to_have_count(1)


def test_visitor_clears_project_topic_with_no_matches(django_server, page):
    _reset_surfaces()
    _create_project(tags=['python'])

    _goto(page, f'{django_server}/projects?tag=no-match')

    empty = page.get_by_test_id('projects-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'filter')
    expect(empty).to_contain_text('No projects match these tags')
    empty.get_by_role('link', name='View all projects').click()
    page.wait_for_url(f'{django_server}/projects')
    expect(page.get_by_text('Beginner project 1227', exact=True)).to_be_visible()


def test_visitor_clears_project_difficulty_with_no_matches(
    django_server, page,
):
    _reset_surfaces()
    _create_project(tags=['python'])

    _goto(page, f'{django_server}/projects?difficulty=expert')

    empty = page.get_by_test_id('projects-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'filter')
    expect(empty).to_contain_text('No projects match this difficulty')
    expect(empty).to_contain_text('No projects match the selected difficulty.')
    empty.get_by_role('link', name='View all projects').click()
    page.wait_for_url(f'{django_server}/projects')
    expect(page.get_by_text('Beginner project 1227', exact=True)).to_be_visible()


def test_anonymous_visitor_can_sign_in_from_empty_poll_list(
    django_server, page,
):
    _reset_surfaces()
    _create_gated_poll()

    _goto(page, f'{django_server}/vote')

    empty = page.get_by_test_id('member-empty-state')
    expect(empty).to_have_attribute('data-empty-kind', 'fresh')
    expect(empty).to_contain_text('No active polls right now')
    expect(empty).to_contain_text('Check back soon!')
    expect(page.get_by_text('Hidden Main poll 1227', exact=True)).to_have_count(0)
    empty.get_by_role('link', name='Sign in').click()
    page.wait_for_url(f'{django_server}/accounts/login/')


def test_signed_in_free_member_is_not_told_to_sign_in(django_server, browser):
    _reset_surfaces()
    _create_gated_poll()
    ensure_tiers()
    create_user('free-empty-1227@example.com', tier_slug='free')
    context = auth_context(browser, 'free-empty-1227@example.com')
    page = context.new_page()
    try:
        _goto(page, f'{django_server}/vote')

        empty = page.get_by_test_id('member-empty-state')
        expect(empty).to_have_attribute('data-empty-kind', 'fresh')
        expect(empty).to_contain_text('No active polls right now')
        expect(empty.get_by_role('link', name='Sign in')).to_have_count(0)
        expect(page.get_by_text('Hidden Main poll 1227', exact=True)).to_have_count(0)
    finally:
        context.close()
