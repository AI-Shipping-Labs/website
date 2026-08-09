"""Browser-visible production/dev search-indexing contracts (#1376)."""

import os
from datetime import date

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.db import connection  # noqa: E402

from content.models import Article  # noqa: E402
from integrations.config import clear_config_cache  # noqa: E402
from integrations.models import IntegrationSetting  # noqa: E402
from website.search_indexing import DEV_ROBOTS_DIRECTIVE  # noqa: E402

pytestmark = pytest.mark.local_only


def _reset_site_base_url():
    IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
    clear_config_cache()
    connection.close()


def _article(slug):
    return Article.objects.create(
        title='Search policy browser article',
        slug=slug,
        content_markdown='# Search policy browser article',
        date=date(2026, 8, 9),
        published=True,
        required_level=0,
    )


def _assert_dev_html(page, response):
    assert response.headers['x-robots-tag'] == DEV_ROBOTS_DIRECTIVE
    robots = page.locator('meta[name="robots"]')
    assert robots.count() == 1
    assert robots.get_attribute('content') == 'noindex,nofollow,noarchive'
    assert page.locator('link[rel="canonical"]').count() == 0


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_production_home_and_article_stay_indexable(django_server, page, settings):
    settings.SITE_BASE_URL = 'https://aishippinglabs.com'
    _reset_site_base_url()
    article = _article('search-policy-production-1376')

    response = page.goto(django_server, wait_until='domcontentloaded')
    assert response.status == 200
    assert 'x-robots-tag' not in response.headers
    assert page.locator('link[rel="canonical"]').get_attribute('href') == (
        'https://aishippinglabs.com'
    )

    response = page.goto(
        f'{django_server}{article.get_absolute_url()}',
        wait_until='domcontentloaded',
    )
    assert response.status == 200
    assert 'x-robots-tag' not in response.headers
    assert page.locator('link[rel="canonical"]').get_attribute('href') == (
        f'https://aishippinglabs.com{article.get_absolute_url()}'
    )

    _reset_site_base_url()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_dev_home_and_article_are_usable_but_excluded(django_server, page, settings):
    settings.SITE_BASE_URL = 'https://dev.aishippinglabs.com'
    _reset_site_base_url()
    article = _article('search-policy-dev-1376')

    response = page.goto(django_server, wait_until='domcontentloaded')
    assert response.status == 200
    _assert_dev_html(page, response)
    assert page.locator('meta[property="og:url"]').get_attribute('content') == (
        'https://dev.aishippinglabs.com'
    )
    assert page.url.startswith(django_server)

    response = page.goto(
        f'{django_server}{article.get_absolute_url()}',
        wait_until='domcontentloaded',
    )
    assert response.status == 200
    _assert_dev_html(page, response)
    assert page.get_by_role('heading', name=article.title).is_visible()
    assert page.url.startswith(django_server)

    _reset_site_base_url()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_authenticated_dev_account_and_studio_stay_excluded(
    django_server,
    browser,
    settings,
):
    settings.SITE_BASE_URL = 'https://dev.aishippinglabs.com'
    _reset_site_base_url()
    ensure_tiers()
    email = 'search-policy-staff-1376@test.com'
    create_staff_user(email)
    context = auth_context(browser, email)
    page = context.new_page()

    for path in ('/account/', '/studio/'):
        response = page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
        assert response.status == 200
        _assert_dev_html(page, response)

    context.close()
    _reset_site_base_url()
