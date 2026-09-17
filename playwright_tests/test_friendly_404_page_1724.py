"""Playwright E2E tests for the friendly 404 page (#1724).

Issue #1720 dropped the ``/tutorial/`` segment from workshop page URLs with
no redirect, leaving roughly 270 previously-indexed
``/workshops/<slug>/tutorial/<page_slug>`` URLs 404ing with no site chrome
in production. These are the two scenarios groomed in the issue:

1. An anonymous visitor follows a dead workshop tutorial URL from an old
   search result and finds a real 404 with full site chrome and a visible
   link to the workshop's still-live landing page.
2. An anonymous visitor hits an arbitrary dead URL elsewhere on the site
   and finds the same friendly page with only the generic homepage CTA
   (no fabricated workshop link).

Usage:
    uv run pytest playwright_tests/test_friendly_404_page_1724.py -v
"""

import datetime
import os

import pytest

from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding via the ORM)
# and cannot run against the deployed dev environment. See
# _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _create_workshop(*, slug, title, pages_data=None):
    """Create a published workshop with tutorial pages (ORM fixture)."""
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description='# Workshop\n\nDescription body.',
    )
    pages_data = pages_data or [('setup', 'Setup', '# Setup\n\nBody.')]
    for i, (page_slug, page_title, body) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=page_slug, title=page_title,
            sort_order=i, body=body,
        )
    connection.close()
    return workshop


# ---------------------------------------------------------------------
# Scenario 1: Anonymous visitor follows a dead workshop tutorial link
# from an old search result.
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDeadWorkshopTutorialUrlShowsWorkshopLink:
    @browser_journey
    def test_dead_tutorial_url_links_to_the_still_live_workshop(
        self, django_server, page,
    ):
        workshop = _create_workshop(
            slug='friendly-404-ws',
            title='Friendly 404 Demo Workshop',
            pages_data=[('setup', 'Setup', '# Setup\n\nBody.')],
        )

        # The removed URL shape from #1720 — no redirect exists for it.
        response = page.goto(
            f'{django_server}/workshops/{workshop.slug}/tutorial/setup',
            wait_until='domcontentloaded',
        )
        assert response is not None
        assert response.status == 404

        # Real 404, full site chrome, friendly copy — not a bare
        # technical error.
        assert page.locator('[data-testid="desktop-primary-nav"]').count() == 1
        assert page.locator('[data-testid="footer-social-row"]').count() == 1
        assert "We couldn't find that page" in page.content()

        # A visible link to the specific workshop's still-live landing
        # page, labeled with its title.
        workshop_link = page.locator(
            f'a[href="{workshop.get_absolute_url()}"]',
        )
        assert workshop_link.count() == 1
        assert workshop.title in workshop_link.first.text_content()

        workshop_link.first.click()
        page.wait_for_load_state('domcontentloaded')

        # The visitor lands on the workshop's live landing page, not a
        # further dead end.
        assert page.url == f'{django_server}{workshop.get_absolute_url()}'
        assert workshop.title in page.content()


# ---------------------------------------------------------------------
# Scenario 2: Anonymous visitor hits an arbitrary dead URL elsewhere on
# the site.
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestArbitraryDeadUrlShowsGenericHomepageCta:
    @browser_journey
    def test_arbitrary_dead_url_has_no_workshop_link_and_returns_home(
        self, django_server, page,
    ):
        response = page.goto(
            f'{django_server}/this-page-does-not-exist-1724',
            wait_until='domcontentloaded',
        )
        assert response is not None
        assert response.status == 404

        assert page.locator('[data-testid="desktop-primary-nav"]').count() == 1
        assert page.locator('[data-testid="footer-social-row"]').count() == 1
        assert "We couldn't find that page" in page.content()

        # No workshop-context link fabricated for an unrelated dead path.
        assert page.locator('a[href="/workshops"]').count() == 0

        home_cta = page.locator(
            '[data-testid="page-not-found"] a', has_text='Go to homepage',
        )
        assert home_cta.count() == 1

        home_cta.first.click()
        page.wait_for_load_state('domcontentloaded')

        assert page.url == f'{django_server}/'
        # The visitor can keep browsing normally via the header nav.
        assert page.locator('[data-testid="desktop-primary-nav"]').count() == 1
