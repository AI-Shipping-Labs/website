"""Playwright E2E tests for slug-only workshop URLs (#1064).

Usage:
    uv run pytest playwright_tests/test_workshop_url_key_750.py -v
"""

import datetime
import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

WORKSHOP_DATE = datetime.date(2026, 6, 17)
WORKSHOP_SLUG = 'cloudflare-workers-vectorize-agent'
DATE_SLUG = f'{WORKSHOP_DATE.isoformat()}-{WORKSHOP_SLUG}'


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(slug=WORKSHOP_SLUG, date=WORKSHOP_DATE):
    """Create a published workshop with three tutorial pages."""
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title='Cloudflare Workers Vectorize Agent',
        status='published',
        date=date,
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description='Build an agent on Cloudflare Workers and Vectorize.',
    )
    for i, (page_slug, title) in enumerate(
        [
            ('overview', 'Overview and setup'),
            ('intro', 'Introduction'),
            ('querying', 'Querying'),
        ],
        start=1,
    ):
        WorkshopPage.objects.create(
            workshop=workshop, slug=page_slug, title=title,
            sort_order=i, body=f'# {title}\n\nContent body.',
        )
    connection.close()
    return workshop


@pytest.mark.django_db(transaction=True)
class TestCanonicalUrlsRenderDirectly:
    @pytest.mark.core
    def test_catalog_click_lands_on_slug_only_url_no_redirect(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        responses = []
        page.on('response', lambda r: responses.append(r))

        page.goto(
            f'{django_server}/workshops', wait_until='domcontentloaded',
        )

        card_link = page.locator(
            f'a[href="/workshops/{WORKSHOP_SLUG}"]',
        ).first
        assert card_link.count() == 1

        responses.clear()
        card_link.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url == f'{django_server}/workshops/{WORKSHOP_SLUG}'
        navigation_redirects = [
            r for r in responses
            if 300 <= r.status < 400 and '/workshops/' in r.url
        ]
        assert navigation_redirects == []

    def test_canonical_tutorial_deep_link_renders_directly(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/{WORKSHOP_SLUG}/tutorial/intro',
            wait_until='domcontentloaded',
        )
        assert page.url == (
            f'{django_server}/workshops/{WORKSHOP_SLUG}/tutorial/intro'
        )
        assert response is not None and response.status == 200
        assert 'Introduction' in page.content()


@pytest.mark.django_db(transaction=True)
class TestLegacyDatedUrlsRedirect:
    @pytest.mark.core
    def test_legacy_landing_redirects_to_slug_only(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/{DATE_SLUG}',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        assert page.url == f'{django_server}/workshops/{WORKSHOP_SLUG}'
        assert 'Cloudflare Workers Vectorize Agent' in page.content()

    def test_legacy_video_redirect_preserves_t_query(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/{DATE_SLUG}/video?t=300',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        assert page.url == f'{django_server}/workshops/{WORKSHOP_SLUG}/video?t=300'

    def test_legacy_tutorial_redirects_to_slug_only(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/{DATE_SLUG}/tutorial/intro',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        assert page.url == (
            f'{django_server}/workshops/{WORKSHOP_SLUG}/tutorial/intro'
        )
        assert 'Introduction' in page.content()


@pytest.mark.django_db(transaction=True)
class TestSitemapShape:
    def test_sitemap_emits_canonical_workshop_urls(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/sitemap.xml',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 200
        body = response.text()

        assert f'/workshops/{WORKSHOP_SLUG}' in body
        assert f'/workshops/{WORKSHOP_SLUG}/tutorial/intro' in body
        assert f'<loc>https://aishippinglabs.com/workshops/{DATE_SLUG}</loc>' not in body


@pytest.mark.django_db(transaction=True)
class TestBadDatedUrls:
    def test_date_mismatch_returns_404(self, django_server, page):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/2026-06-18-{WORKSHOP_SLUG}',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 404

    def test_invalid_date_prefix_returns_404(self, django_server, page):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/9999-99-99-{WORKSHOP_SLUG}',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 404
