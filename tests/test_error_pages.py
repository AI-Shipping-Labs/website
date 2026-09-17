"""Friendly 404/500 error pages (#1724).

Issue #1720 dropped the ``/tutorial/`` segment from workshop page URLs with
no redirect, leaving ~270 previously-indexed
``/workshops/<slug>/tutorial/<page_slug>`` URLs 404ing with no site chrome.
This covers:

- ``resolve_workshop_secondary_cta`` (the pure path-string helper) directly,
  unit-testable without a request/response cycle.
- The rendered ``404.html`` page: full site chrome, the friendly
  ``member_empty_state`` message, and a workshop-aware secondary CTA.
- The response contract: a genuine 404 (never a soft-404 200, never a
  redirect) is the SEO-relevant signal that lets stale URLs drop out of the
  index.
- ``500.html`` renders standalone, with no request/context dependency and
  no chrome partials (Django's ``server_error`` view supplies neither).
"""

from datetime import date

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings, tag

from content.models import Workshop
from website.error_views import (
    WORKSHOPS_CATALOG_LABEL,
    WORKSHOPS_CATALOG_URL,
    resolve_workshop_secondary_cta,
)


def _make_workshop(slug='ws-1724', title='Prompting workshop', status='published'):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=date(2026, 4, 21),
    )


class ResolveWorkshopSecondaryCtaTest(TestCase):
    """Direct unit tests against the path-string helper (no request/response)."""

    def test_path_outside_workshops_returns_none(self):
        self.assertIsNone(resolve_workshop_secondary_cta('/blog/some-article'))
        self.assertIsNone(resolve_workshop_secondary_cta('/this-page-does-not-exist-1724'))
        self.assertIsNone(resolve_workshop_secondary_cta('/'))

    def test_published_workshop_slug_links_to_its_landing_page(self):
        workshop = _make_workshop(slug='rag-in-prod', title='RAG in production')
        result = resolve_workshop_secondary_cta(
            '/workshops/rag-in-prod/tutorial/setup',
        )
        self.assertEqual(result, ('RAG in production', workshop.get_absolute_url()))
        self.assertEqual(workshop.get_absolute_url(), '/workshops/rag-in-prod')

    def test_unpublished_workshop_slug_falls_back_to_catalog(self):
        _make_workshop(slug='draft-ws', status='draft')
        result = resolve_workshop_secondary_cta('/workshops/draft-ws/tutorial/setup')
        self.assertEqual(result, (WORKSHOPS_CATALOG_LABEL, WORKSHOPS_CATALOG_URL))

    def test_unknown_workshop_slug_falls_back_to_catalog(self):
        result = resolve_workshop_secondary_cta(
            '/workshops/does-not-exist-1724/tutorial/x',
        )
        self.assertEqual(result, (WORKSHOPS_CATALOG_LABEL, WORKSHOPS_CATALOG_URL))

    def test_bare_workshops_catalog_path_has_no_slug_to_match(self):
        # No trailing slug segment, so nothing to resolve against Workshop.
        self.assertIsNone(resolve_workshop_secondary_cta('/workshops'))


@tag('core')
@override_settings(DEBUG=False)
class NotFoundPageTest(TestCase):
    """Response contract and rendered body for the friendly 404 page.

    Django's exception handling special-cases ``Http404``: with
    ``DEBUG=True`` it always renders the built-in technical debug page,
    regardless of any custom ``handler404``. The custom handler in
    ``website/error_views.py`` (and the plain default fallback) only ever
    run with ``DEBUG=False``, which is how the site actually runs in
    production -- so these tests force that the same way
    ``RenderedHomeNoPreLaunchTest`` does elsewhere in the suite.
    """

    def test_arbitrary_unknown_url_returns_genuine_404(self):
        # This specific-value assertion is also the soft-404 guard: a
        # soft-404 (200 with "not found" copy) would fail here just as
        # surely as it would fail an explicit "not 200" check, and it lets
        # Google drop the stale /tutorial/ URLs from its index on recrawl,
        # which a 200 would keep indexed indefinitely.
        response = self.client.get('/this-page-does-not-exist-1724')
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, '404.html')

    def test_arbitrary_unknown_url_carries_no_redirect(self):
        response = self.client.get('/this-page-does-not-exist-1724')
        self.assertNotIn(response.status_code, (301, 302, 307, 308))
        self.assertNotIn('Location', response)

    def test_arbitrary_unknown_url_shows_generic_chrome_and_homepage_cta(self):
        response = self.client.get('/this-page-does-not-exist-1724')
        self.assertContains(
            response, "We couldn't find that page", status_code=404,
        )
        self.assertContains(response, 'data-testid="desktop-primary-nav"', status_code=404)
        self.assertContains(response, 'data-testid="footer-social-row"', status_code=404)
        self.assertContains(response, 'href="/"', status_code=404)
        self.assertNotContains(response, WORKSHOPS_CATALOG_LABEL, status_code=404)

    def test_dead_url_under_published_workshop_links_to_its_landing_page(self):
        workshop = _make_workshop(slug='rag-in-prod', title='RAG in production')
        response = self.client.get('/workshops/rag-in-prod/tutorial/setup')
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            f'href="{workshop.get_absolute_url()}"',
            status_code=404,
        )
        self.assertContains(response, 'RAG in production', status_code=404)

    def test_dead_url_under_published_workshop_carries_no_redirect(self):
        _make_workshop(slug='rag-in-prod', title='RAG in production')
        response = self.client.get('/workshops/rag-in-prod/tutorial/setup')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(response.status_code, (301, 302, 307, 308))
        self.assertNotIn('Location', response)

    def test_dead_url_under_unpublished_workshop_falls_back_to_catalog_link(self):
        _make_workshop(slug='draft-ws', title='Unfinished draft', status='draft')
        response = self.client.get('/workshops/draft-ws/tutorial/setup')
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Unfinished draft', status_code=404)
        self.assertContains(response, f'href="{WORKSHOPS_CATALOG_URL}"', status_code=404)
        self.assertContains(response, WORKSHOPS_CATALOG_LABEL, status_code=404)

    def test_dead_url_under_unknown_workshop_slug_falls_back_to_catalog_link(self):
        response = self.client.get('/workshops/does-not-exist-1724/tutorial/x')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, f'href="{WORKSHOPS_CATALOG_URL}"', status_code=404)
        self.assertContains(response, WORKSHOPS_CATALOG_LABEL, status_code=404)


class ServerErrorPageTest(SimpleTestCase):
    """``500.html`` renders with no context and no chrome dependency."""

    def test_renders_with_zero_context_and_no_request(self):
        # Mirrors exactly how Django's server_error view renders this
        # template in production: template.render() with no context dict
        # and no request at all.
        html = render_to_string('500.html')
        self.assertIn('Something went wrong', html)
        self.assertIn('href="/"', html)

    def test_does_not_include_request_dependent_chrome(self):
        html = render_to_string('500.html')
        self.assertNotIn('data-testid="desktop-primary-nav"', html)
        self.assertNotIn('data-testid="footer-social-row"', html)
