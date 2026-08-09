"""Anonymous deployed-dev robots and crawlability smoke check (#1380)."""

import pytest

from playwright_tests.conftest import base_url_is_local, goto_with_retry


@pytest.mark.core
def test_robots_allows_crawling_and_dev_targets_expose_noindex(django_server, page):
    response = goto_with_retry(page, f'{django_server}/robots.txt')
    assert response.status == 200
    assert response.headers['content-type'].startswith('text/plain')
    body = response.body().decode('utf-8')
    directives = [line.strip() for line in body.splitlines() if line.strip()]
    assert 'Disallow:' not in body

    if base_url_is_local():
        from django.conf import settings

        expected = ['User-agent: *', 'Allow: /']
        if settings.SITE_BASE_URL.rstrip('/') == 'https://aishippinglabs.com':
            expected.append('Sitemap: https://aishippinglabs.com/sitemap.xml')
        assert directives == expected
        return

    assert directives == ['User-agent: *', 'Allow: /']
    assert 'Sitemap:' not in body

    expected_header = 'noindex, nofollow, noarchive'
    assert response.headers['x-robots-tag'] == expected_header

    homepage = goto_with_retry(page, django_server)
    assert homepage.status == 200
    assert homepage.headers['x-robots-tag'] == expected_header
    assert page.locator('meta[name="robots"]').get_attribute('content') == (
        'noindex,nofollow,noarchive'
    )
    assert page.locator('link[rel="canonical"]').count() == 0

    ping = goto_with_retry(page, f'{django_server}/ping')
    assert ping.status == 200
    assert ping.body()
    assert ping.headers['x-robots-tag'] == expected_header

    missing = goto_with_retry(page, f'{django_server}/missing-robots-target-1380')
    assert missing.status == 404
    assert missing.headers['x-robots-tag'] == expected_header
