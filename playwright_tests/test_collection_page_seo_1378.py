"""Rendered-browser coverage for collection-page SEO metadata (issue #1378)."""

import datetime
import os
from urllib.parse import urlsplit

import pytest

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = pytest.mark.local_only


ROUTES = {
    '/about': 'About | AI Shipping Labs',
    '/blog': 'Blog | AI Shipping Labs',
    '/projects': 'Project Showcase | AI Shipping Labs',
    '/courses': 'Courses | AI Shipping Labs',
    '/events': 'Events | AI Shipping Labs',
    '/downloads': 'Downloads | AI Shipping Labs',
    '/tutorials': 'Tutorials | AI Shipping Labs',
    '/workshops': 'Hands-on AI Workshops | AI Shipping Labs',
    '/workshops/catalog': 'All Workshops | AI Shipping Labs',
    '/resources': 'Curated Links | AI Shipping Labs',
    '/tags': 'Tags | AI Shipping Labs',
    '/pricing': 'Pricing | AI Shipping Labs',
}


def _configured_site_url():
    from integrations.config import site_base_url

    return site_base_url().rstrip('/')


def _meta(page, selector):
    locator = page.locator(selector)
    assert locator.count() == 1
    return locator.get_attribute('content') or ''


def _canonical(page):
    locator = page.locator('link[rel="canonical"]')
    assert locator.count() == 1
    return locator.get_attribute('href') or ''


def _assert_page_head(page, expected_url, expected_title):
    description = _meta(page, 'meta[name="description"]')
    assert page.title() == expected_title
    assert _canonical(page) == expected_url
    assert _meta(page, 'meta[property="og:url"]') == expected_url
    assert _meta(page, 'meta[property="og:title"]') == expected_title
    assert _meta(page, 'meta[name="twitter:title"]') == expected_title
    assert _meta(page, 'meta[property="og:description"]') == description
    assert _meta(page, 'meta[name="twitter:description"]') == description
    assert _meta(page, 'meta[property="og:type"]') == 'website'
    assert _meta(page, 'meta[property="og:image"]').endswith(
        '/static/ai-shipping-labs.jpg',
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_each_scoped_page_has_its_own_canonical_and_social_context(
    django_server,
    page,
):
    site_url = _configured_site_url()
    for path, title in ROUTES.items():
        goto_with_retry(
            page,
            f'{django_server}{path}?utm_source=browser&unknown=value',
        )
        _assert_page_head(page, f'{site_url}{path}', title)
        assert _canonical(page) != site_url
        assert _meta(page, 'meta[property="og:title"]') != (
            'AI Shipping Labs | A Technical Community'
        )


def _seed_filterable_collections():
    from django.db import connection

    from content.models import (
        Article,
        Course,
        CuratedLink,
        Download,
        Project,
        Workshop,
    )

    Article.objects.create(
        title='Agents Browser Article',
        slug='agents-browser-article-1378',
        description='Browser article.',
        content_markdown='Browser article body.',
        date=datetime.date(2026, 8, 1),
        tags=['agents'],
        published=True,
    )
    Project.objects.create(
        title='Agents Browser Project',
        slug='agents-browser-project-1378',
        description='Browser project.',
        date=datetime.date(2026, 8, 1),
        difficulty='beginner',
        tags=['agents'],
        published=True,
    )
    Course.objects.create(
        title='Python Browser Course',
        slug='python-browser-course-1378',
        description='Browser course.',
        tags=['python'],
        status='published',
    )
    Download.objects.create(
        title='Agents Browser Download',
        slug='agents-browser-download-1378',
        description='Browser download.',
        file_url='https://files.example/download.pdf',
        tags=['agents'],
        published=True,
    )
    CuratedLink.objects.create(
        item_id='python-browser-link-1378',
        title='Python Browser Link',
        description='Browser curated link.',
        url='https://example.com/python',
        category='tools',
        tags=['python'],
        published=True,
    )
    Workshop.objects.create(
        title='Python Browser Workshop',
        slug='python-browser-workshop-1378',
        description='Browser workshop.',
        date=datetime.date(2026, 8, 1),
        tags=['python'],
        core_tools=['Claude Code'],
        skill_level='beginner',
        status='published',
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_collection_filters_and_pricing_recovery_keep_ui_but_drop_queries(
    django_server,
    page,
    settings,
    django_db_blocker,
):
    with django_db_blocker.unblock():
        _seed_filterable_collections()

    site_url = _configured_site_url()
    cases = (
        ('/blog?tag=agents', '/blog', 'Agents Browser Article'),
        (
            '/projects?difficulty=beginner&tag=agents',
            '/projects',
            'Agents Browser Project',
        ),
        ('/courses?tag=python', '/courses', 'Python Browser Course'),
        ('/downloads?tag=agents', '/downloads', 'Agents Browser Download'),
        ('/resources?tag=python', '/resources', 'Python Browser Link'),
    )
    for query_url, canonical_path, visible_copy in cases:
        goto_with_retry(page, f'{django_server}{query_url}')
        assert query_url.split('?', 1)[1] in page.url
        assert _canonical(page) == f'{site_url}{canonical_path}'
        assert _meta(page, 'meta[property="og:url"]') == (
            f'{site_url}{canonical_path}'
        )
        assert visible_copy in page.locator('body').inner_text()

    goto_with_retry(page, f'{django_server}/workshops')
    _assert_page_head(
        page,
        f'{site_url}/workshops',
        'Hands-on AI Workshops | AI Shipping Labs',
    )
    page.get_by_test_id('browse-workshops-cta').click()
    page.wait_for_url(f'{django_server}/workshops/catalog')
    _assert_page_head(
        page,
        f'{site_url}/workshops/catalog',
        'All Workshops | AI Shipping Labs',
    )

    workshop_query = (
        '/workshops/catalog?tag=python&tool=Claude+Code&access=free'
        '&skill_level=beginner'
    )
    goto_with_retry(page, f'{django_server}{workshop_query}')
    assert workshop_query.split('?', 1)[1] in page.url
    assert _canonical(page) == f'{site_url}/workshops/catalog'
    assert _meta(page, 'meta[property="og:url"]') == (
        f'{site_url}/workshops/catalog'
    )
    assert page.get_by_test_id('workshop-access-filter-free').get_attribute(
        'aria-current'
    ) == 'page'
    assert page.get_by_test_id('workshop-skill-filter-beginner').get_attribute(
        'aria-current'
    ) == 'page'
    assert page.get_by_test_id('workshop-active-tool').inner_text().strip() == (
        'Claude Code'
    )
    assert page.get_by_test_id('workshop-active-tag').inner_text().strip() == (
        'python'
    )
    assert 'Python Browser Workshop' in page.locator('body').inner_text()

    goto_with_retry(
        page,
        f'{django_server}/pricing?checkout_error=temporarily_unavailable'
        '&utm_source=browser',
    )
    assert page.get_by_test_id('checkout-recovery-banner').is_visible()
    assert _canonical(page) == f'{site_url}/pricing'
    assert _meta(page, 'meta[property="og:url"]') == f'{site_url}/pricing'

    with django_db_blocker.unblock():
        from django.db import connection

        from integrations.config import clear_config_cache
        from integrations.models import IntegrationSetting

        IntegrationSetting.objects.update_or_create(
            key='SITE_BASE_URL',
            defaults={'value': site_url, 'group': 'site'},
        )
        clear_config_cache()
        connection.close()

    attacker_host = 'attacker.localhost'
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, attacker_host]
    attacker_url = (
        f'http://{attacker_host}:{urlsplit(django_server).port}'
    )
    response = goto_with_retry(
        page,
        f'{attacker_url}/pricing?checkout_error=temporarily_unavailable'
        '&utm_source=untrusted-host',
    )
    assert response.status == 200
    assert page.get_by_test_id('checkout-recovery-banner').is_visible()
    assert _canonical(page) == f'{site_url}/pricing'
    assert _meta(page, 'meta[property="og:url"]') == f'{site_url}/pricing'
    assert attacker_host not in page.content()


def _seed_event_pages():
    from django.db import connection
    from django.utils import timezone

    from events.models import Event

    now = timezone.now()
    for index in range(25):
        Event.objects.create(
            title=f'Python Browser Recording {index:02d}',
            slug=f'python-browser-recording-1378-{index:02d}',
            description='Recorded browser event.',
            start_datetime=now - datetime.timedelta(days=index + 2),
            end_datetime=now - datetime.timedelta(days=index + 2, hours=-1),
            status='completed',
            recording_url=f'https://video.example/browser/{index}',
            tags=['python'],
            published=True,
        )
    for index in range(2):
        Event.objects.create(
            title=f'Upcoming Browser Event {index:02d}',
            slug=f'upcoming-browser-event-1378-{index:02d}',
            description='Upcoming browser event.',
            start_datetime=now + datetime.timedelta(days=index + 2),
            end_datetime=now + datetime.timedelta(days=index + 2, hours=1),
            status='upcoming',
            tags=['python'],
            published=True,
        )
    connection.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_events_past_taxonomy_facets_and_pagination_are_normalized(
    django_server,
    page,
    django_db_blocker,
):
    with django_db_blocker.unblock():
        _seed_event_pages()

    site_url = _configured_site_url()
    goto_with_retry(page, f'{django_server}/events?filter=past')
    _assert_page_head(
        page,
        f'{site_url}/events?filter=past',
        'Past Event Recordings | AI Shipping Labs',
    )
    assert 'recorded AI Shipping Labs events' in _meta(
        page, 'meta[name="description"]',
    )

    goto_with_retry(
        page,
        f'{django_server}/events?filter=past&tag=python&page=2',
    )
    assert page.get_by_test_id('active-tag-filters').is_visible()
    assert 'python' in page.get_by_test_id('active-tag-filters').inner_text()
    assert page.get_by_test_id('events-filter-past').get_attribute(
        'aria-selected'
    ) == 'true'
    assert 'Page 2 of 2' in page.get_by_test_id(
        'events-past-pagination'
    ).inner_text()
    assert _canonical(page) == f'{site_url}/events?filter=past'

    pagination_cases = (
        ('/events?page=1', f'{site_url}/events', 1),
        ('/events?page=2', f'{site_url}/events?page=2', 2),
        ('/events?page=bad', f'{site_url}/events', 1),
        ('/events?page=999', f'{site_url}/events?page=2', 2),
        ('/events?filter=past&page=1', f'{site_url}/events?filter=past', 1),
        (
            '/events?filter=past&page=2',
            f'{site_url}/events?filter=past&page=2',
            2,
        ),
        ('/events?filter=past&page=bad', f'{site_url}/events?filter=past', 1),
        (
            '/events?filter=past&page=999',
            f'{site_url}/events?filter=past&page=2',
            2,
        ),
    )
    for query_url, expected_url, resolved_page in pagination_cases:
        goto_with_retry(page, f'{django_server}{query_url}')
        assert _canonical(page) == expected_url
        assert _meta(page, 'meta[property="og:url"]') == expected_url
        assert f'Page {resolved_page} of 2' in page.get_by_test_id(
            'events-past-pagination'
        ).inner_text()

    view_cases = (
        ('', 'all', True, True),
        ('?filter=all', 'all', True, True),
        ('?filter=upcoming', 'upcoming', True, False),
        ('?filter=not-a-view', 'all', True, True),
    )
    for query, selected, upcoming_visible, past_visible in view_cases:
        goto_with_retry(page, f'{django_server}/events{query}')
        assert _canonical(page) == f'{site_url}/events'
        assert page.get_by_test_id(f'events-filter-{selected}').get_attribute(
            'aria-selected'
        ) == 'true'
        assert page.get_by_test_id('events-upcoming-section').count() == int(
            upcoming_visible
        )
        assert page.get_by_test_id('events-past-section').count() == int(
            past_visible
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_homepage_and_all_required_detail_pages_keep_existing_metadata(
    django_server,
    page,
    django_db_blocker,
):
    from django.db import connection
    from django.utils import timezone

    from content.models import (
        Article,
        Course,
        MarketingPage,
        Tutorial,
        Workshop,
    )
    from events.models import Event

    with django_db_blocker.unblock():
        article = Article.objects.create(
            title='Browser Detail Regression',
            slug='browser-detail-regression-1378',
            description='Detail metadata stays model-specific.',
            content_markdown='Detail body.',
            date=datetime.date(2026, 8, 1),
            published=True,
        )
        course = Course.objects.create(
            title='Browser Detail Course',
            slug='browser-detail-course-1378',
            description='Course detail metadata stays model-specific.',
            status='published',
        )
        event = Event.objects.create(
            title='Browser Detail Event',
            slug='browser-detail-event-1378',
            description='Event detail metadata stays model-specific.',
            start_datetime=timezone.now() + datetime.timedelta(days=5),
            status='upcoming',
            published=True,
        )
        tutorial = Tutorial.objects.create(
            title='Browser Detail Tutorial',
            slug='browser-detail-tutorial-1378',
            description='Tutorial detail metadata stays model-specific.',
            content_markdown='Tutorial detail body.',
            date=datetime.date(2026, 8, 1),
            published=True,
        )
        workshop = Workshop.objects.create(
            title='Browser Detail Workshop',
            slug='browser-detail-workshop-1378',
            description='Workshop detail metadata stays model-specific.',
            date=datetime.date(2026, 8, 1),
            status='published',
        )
        marketing_page = MarketingPage.objects.create(
            title='Browser Detail Marketing Page',
            public_path='/browser-detail-marketing-page-1378',
            description='Marketing page detail content.',
            meta_description='Marketing page metadata stays model-specific.',
            content_markdown='Marketing page detail body.',
            status='published',
        )
        detail_objects = (
            (
                article,
                'Detail metadata stays model-specific.',
                'Detail metadata stays model-specific.',
            ),
            (
                course,
                'Course detail metadata stays model-specific.',
                'Course detail metadata stays model-specific.',
            ),
            (
                event,
                'Event detail metadata stays model-specific.',
                'Event detail metadata stays model-specific.',
            ),
            (
                tutorial,
                'Tutorial detail metadata stays model-specific.',
                'Tutorial detail metadata stays model-specific.',
            ),
            (
                workshop,
                'Workshop detail metadata stays model-specific.',
                'Workshop detail metadata stays model-specific.',
            ),
            (
                marketing_page,
                'Marketing page metadata stays model-specific.',
                'Marketing page detail content.',
            ),
        )
        detail_cases = tuple(
            (
                obj.get_absolute_url(),
                obj.title,
                expected_meta_description,
                expected_social_description,
            )
            for (
                obj,
                expected_meta_description,
                expected_social_description,
            ) in detail_objects
        )
        connection.close()

    site_url = _configured_site_url()
    goto_with_retry(page, f'{django_server}/')
    assert _canonical(page) == site_url
    assert _meta(page, 'meta[property="og:url"]') == site_url
    assert _meta(page, 'meta[property="og:title"]') == (
        'AI Shipping Labs | A Technical Community'
    )

    for (
        detail_path,
        detail_title,
        expected_meta_description,
        expected_social_description,
    ) in detail_cases:
        goto_with_retry(page, f'{django_server}{detail_path}')
        assert _canonical(page) == f'{site_url}{detail_path}'
        assert _meta(page, 'meta[property="og:url"]') == (
            f'{site_url}{detail_path}'
        )
        description = _meta(page, 'meta[name="description"]')
        assert expected_meta_description in description
        assert detail_title in _meta(page, 'meta[property="og:title"]')
        assert detail_title in _meta(page, 'meta[name="twitter:title"]')
        social_description = _meta(page, 'meta[property="og:description"]')
        assert expected_social_description in social_description
        assert _meta(page, 'meta[name="twitter:description"]') == (
            social_description
        )
        assert _meta(page, 'meta[property="og:title"]') != (
            'AI Shipping Labs | A Technical Community'
        )
        assert detail_title in page.locator('body').inner_text()
