"""Playwright E2E tests for issue #750 — date-slug workshop URLs.

Scenarios:

1. Catalog click lands directly on the canonical date-slug URL (no 301).
2. Old slug-only landing URL 301s to the canonical URL.
3. Old slug-only video URL 301s to canonical + preserves ``?t=`` query.
4. Old slug-only tutorial URL 301s to canonical.
5. ``?t=`` deep-link survives the legacy video redirect.
6. Sitemap exposes the canonical URLs and not the bare-slug shape.
7. Malformed inputs return 404 (no slug-only fallback that 200s).

The collision scenario (two workshops sharing a slug) is covered by the
Django unit tests in ``content/tests/test_workshop_url_key_750.py`` —
``Workshop.slug`` still carries a DB-level unique constraint, so a real
two-row collision isn't reproducible through Playwright.

Usage:
    uv run pytest playwright_tests/test_workshop_url_key_750.py -v
"""

import datetime
import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Issue #656 / project convention: this module uses local-only fixtures
# (DB seeding, cookie injection, etc.) and cannot run against the
# deployed dev environment.
pytestmark = pytest.mark.local_only


# Fixed date so every URL we assert on is stable; matches the test
# fixture in ``content/tests/test_workshops_public.py``.
WORKSHOP_DATE = datetime.date(2026, 5, 14)
WORKSHOP_SLUG = 'build-search'
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
        title='Build Your Own Search Engine',
        status='published',
        date=date,
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description='Build a search engine from scratch.',
    )
    for i, (page_slug, title) in enumerate(
        [
            ('intro', 'Introduction'),
            ('indexing', 'Indexing'),
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
    def test_catalog_click_lands_on_date_slug_url_no_redirect(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        # Track every response in the navigation chain so we can prove
        # the click does not bounce through a 301.
        responses = []
        page.on('response', lambda r: responses.append(r))

        page.goto(
            f'{django_server}/workshops', wait_until='domcontentloaded',
        )

        # Catalog card link target is the canonical date-slug URL.
        card_link = page.locator(
            f'a[href="/workshops/{DATE_SLUG}"]',
        ).first
        assert card_link.count() == 1

        responses.clear()
        card_link.click()
        page.wait_for_load_state('domcontentloaded')

        # Browser ends up on the canonical URL...
        assert page.url == f'{django_server}/workshops/{DATE_SLUG}'
        # ...and no 301 was issued during the click navigation.
        navigation_redirects = [
            r for r in responses
            if 300 <= r.status < 400 and '/workshops/' in r.url
        ]
        assert navigation_redirects == [], (
            f'Expected no 301s through the catalog click, got: '
            f'{[(r.url, r.status) for r in navigation_redirects]}'
        )


@pytest.mark.django_db(transaction=True)
class TestLegacyUrlsRedirectToCanonical:
    def test_legacy_landing_redirects_to_canonical(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        response = page.goto(
            f'{django_server}/workshops/{WORKSHOP_SLUG}',
            wait_until='domcontentloaded',
        )
        # Browser ends up at the canonical date-slug URL.
        assert page.url == f'{django_server}/workshops/{DATE_SLUG}'
        # The landing renders normally (200 after the 301).
        assert response is not None and response.status == 200
        assert 'Build Your Own Search Engine' in page.content()

    def test_legacy_video_redirects_to_canonical(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        page.goto(
            f'{django_server}/workshops/{WORKSHOP_SLUG}/video',
            wait_until='domcontentloaded',
        )
        assert page.url == f'{django_server}/workshops/{DATE_SLUG}/video'

    def test_legacy_video_redirect_preserves_t_query(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        page.goto(
            f'{django_server}/workshops/{WORKSHOP_SLUG}/video?t=300',
            wait_until='domcontentloaded',
        )
        # ``?t=`` deep-link survives the 301 so the embed still drops
        # the viewer at the right offset.
        assert page.url == (
            f'{django_server}/workshops/{DATE_SLUG}/video?t=300'
        )

    def test_legacy_tutorial_redirects_to_canonical(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop()

        page.goto(
            f'{django_server}/workshops/{WORKSHOP_SLUG}/tutorial/intro',
            wait_until='domcontentloaded',
        )
        assert page.url == (
            f'{django_server}/workshops/{DATE_SLUG}/tutorial/intro'
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

        # Canonical landing + at least one tutorial page must be present.
        assert f'/workshops/{DATE_SLUG}' in body
        assert (
            f'/workshops/{DATE_SLUG}/tutorial/intro' in body
        )
        # Bare-slug landing entry must not appear as a ``<loc>``.
        # Sitemap entries use the production base URL, so just check
        # that the bare-slug path doesn't show up as a ``<loc>`` body.
        # We accept the substring may appear inside the canonical URL
        # (which ends with ``-{WORKSHOP_SLUG}``); scope to the wrapped
        # ``<loc>...</loc>`` shape.
        for line in body.split('\n'):
            if (
                f'/workshops/{WORKSHOP_SLUG}</loc>' in line
                and f'/workshops/{DATE_SLUG}/' not in line
                and f'/workshops/{DATE_SLUG}<' not in line
            ):
                pytest.fail(
                    f'Sitemap emitted slug-only URL: {line.strip()}'
                )


@pytest.mark.django_db(transaction=True)
class TestMalformedUrls:
    def test_malformed_input_returns_404(self, django_server, page):
        _clear_workshops()
        _create_workshop()

        # No date prefix and not a published slug -> 404.
        response = page.goto(
            f'{django_server}/workshops/totally-made-up-slug',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 404

    def test_invalid_date_prefix_returns_404(self, django_server, page):
        _clear_workshops()
        _create_workshop()

        # Matches the date-slug regex shape but the date is invalid.
        response = page.goto(
            f'{django_server}/workshops/9999-99-99-bad-date',
            wait_until='domcontentloaded',
        )
        assert response is not None and response.status == 404
