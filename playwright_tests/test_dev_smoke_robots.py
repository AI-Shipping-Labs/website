"""Anonymous deployed-dev robots and crawlability smoke check (#1380)."""

from collections import Counter
from urllib.parse import urlsplit

import pytest

from playwright_tests.conftest import base_url_is_local, goto_with_retry

PRODUCTION_HOST = 'aishippinglabs.com'
PUBLIC_CRAWL_DIRECTIVES = (
    ('user-agent', '*'),
    ('allow', '/'),
)
PRODUCTION_ROBOTS_DIRECTIVES = PUBLIC_CRAWL_DIRECTIVES + (
    ('disallow', '/accounts/'),
    ('disallow', '/register'),
    ('disallow', '/api/'),
    ('disallow', '/blog?*tag='),
    ('disallow', '/blog?*topic='),
    ('disallow', '/projects?*difficulty='),
    ('disallow', '/projects?*tag='),
    ('disallow', '/resources?*tag='),
    ('disallow', '/downloads?*tag='),
    ('disallow', '/workshops/catalog?*tag='),
    ('disallow', '/workshops/catalog?*topic='),
    ('disallow', '/events?*filter='),
    ('disallow', '/events?*tag='),
    ('sitemap', 'https://aishippinglabs.com/sitemap.xml'),
)


def _parse_directives(body):
    directives = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, separator, value = line.partition(':')
        assert separator, f'Malformed robots directive: {line!r}'
        directives.append((name.strip().lower(), value.strip()))
    return tuple(directives)


def _is_production_target(django_server):
    target = django_server
    if base_url_is_local():
        from django.conf import settings

        target = settings.SITE_BASE_URL
    return urlsplit(str(target).strip()).hostname == PRODUCTION_HOST


def _assert_approved_robots_policy(directives, *, production):
    expected = (
        PRODUCTION_ROBOTS_DIRECTIVES if production else PUBLIC_CRAWL_DIRECTIVES
    )
    assert directives[:1] == (('user-agent', '*'),)
    assert directives.count(('user-agent', '*')) == 1
    assert Counter(directives) == Counter(expected)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_robots_allows_crawling_and_dev_targets_expose_noindex(django_server, page):
    response = goto_with_retry(page, f'{django_server}/robots.txt')
    assert response.status == 200
    assert response.headers['content-type'].startswith('text/plain')
    body = response.body().decode('utf-8')
    directives = _parse_directives(body)
    assert ('disallow', '/') not in directives

    production = _is_production_target(django_server)
    _assert_approved_robots_policy(directives, production=production)
    if production:
        return

    if base_url_is_local():
        return

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
