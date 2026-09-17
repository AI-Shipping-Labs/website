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
- The 404 template context: taking over Django's ``page_not_found`` view
  took over its ``exception``/``request_path`` context keys, which other
  apps' tests read to pin down *which* 404 a view raised (#1731).
- ``500.html`` renders standalone, with no request/context dependency and
  no chrome partials (Django's ``server_error`` view supplies neither).
"""

from datetime import date

from django.http import Http404
from django.template.loader import render_to_string
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
    tag,
)
from django.urls import Resolver404

from content.models import Workshop
from website.error_views import (
    WORKSHOPS_CATALOG_LABEL,
    WORKSHOPS_CATALOG_URL,
    default_404_context,
    resolve_workshop_secondary_cta,
)


def _make_workshop(slug='ws-1724', title='Prompting workshop', status='published'):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=date(2026, 4, 21),
    )


def _page_not_found_card(html):
    """Return just the 404 card's markup, from its testid to its section end.

    The 404 page deliberately carries the normal header and footer (#1724
    asked for a path back into the site), and both link to ``/workshops``
    on every page of the site. So "no fabricated workshop CTA" is only a
    meaningful assertion when it is scoped to the card -- which is what the
    Playwright test for this page does, and what the tests below keep
    honest by pinning the CTA's location to the card.
    """
    start = html.index('data-testid="page-not-found"')
    return html[start:html.index('</section>', start)]


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

    def test_secondary_cta_renders_inside_the_page_not_found_card(self):
        workshop = _make_workshop(slug='rag-in-prod', title='RAG in production')
        card = _page_not_found_card(
            self.client.get(
                '/workshops/rag-in-prod/tutorial/setup',
            ).content.decode(),
        )
        self.assertIn(f'href="{workshop.get_absolute_url()}"', card)
        self.assertEqual(card.count('<a '), 2)

    def test_arbitrary_dead_url_card_holds_only_the_homepage_cta(self):
        card = _page_not_found_card(
            self.client.get('/this-page-does-not-exist-1731').content.decode(),
        )
        self.assertEqual(card.count('<a '), 1)
        self.assertIn('href="/"', card)
        self.assertNotIn('/workshops', card)

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


class Default404ContextTest(SimpleTestCase):
    """``default_404_context`` mirrors ``django.views.defaults.page_not_found``."""

    def test_http404_message_is_used_verbatim(self):
        request = RequestFactory().get('/bookclub/1/readers/7')
        self.assertEqual(
            default_404_context(
                request, Http404('This reader has not started this book.'),
            ),
            {
                'request_path': '/bookclub/1/readers/7',
                'exception': 'This reader has not started this book.',
            },
        )

    def test_non_string_exception_arg_falls_back_to_the_class_name(self):
        # Resolver404 carries the resolver's ``tried``/``path`` dict as its
        # first argument, which is internals, not a message.
        context = default_404_context(
            RequestFactory().get('/nope-1731'),
            Resolver404({'tried': [], 'path': 'nope-1731'}),
        )
        self.assertEqual(context['exception'], 'Resolver404')

    def test_exception_without_args_falls_back_to_the_class_name(self):
        context = default_404_context(RequestFactory().get('/nope-1731'), Http404())
        self.assertEqual(context['exception'], 'Http404')

    def test_missing_exception_uses_the_http_reason_phrase(self):
        context = default_404_context(RequestFactory().get('/nope-1731'), None)
        self.assertEqual(context['exception'], 'Not Found')

    def test_request_path_is_percent_quoted(self):
        # Same escaping Django applies, so a template author who does
        # render the key cannot be handed raw attacker-controlled markup.
        context = default_404_context(
            RequestFactory().get('/nope-1731/%3Cscript%3E'), Http404(),
        )
        self.assertEqual(context['request_path'], '/nope-1731/%3Cscript%3E')


@tag('core')
@override_settings(DEBUG=False)
class NotFoundContextContractTest(TestCase):
    """The rendered 404 keeps Django's default context keys (#1731).

    ``bookclub``'s reader-profile test reads
    ``response.context['exception']`` to prove *which* 404 a view raised;
    the custom handler dropping the key turned that unrelated passing test
    into a ``KeyError`` and made main red.
    """

    def test_dead_url_exposes_the_requested_path(self):
        response = self.client.get('/this-page-does-not-exist-1731')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.context['request_path'], '/this-page-does-not-exist-1731',
        )

    def test_view_raised_http404_message_reaches_the_context(self):
        response = self.client.get('/workshops/no-such-workshop-1731')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            str(response.context['exception']),
            'No Workshop matches the given query.',
        )

    def test_http404_message_is_never_rendered_into_the_page(self):
        # A 404 message names the object that was not found, and routes
        # that 404 specifically to avoid disclosing one (#1550) rely on the
        # body saying nothing. The context key is for tests and template
        # authors only.
        response = self.client.get('/workshops/no-such-workshop-1731')
        self.assertNotContains(
            response, 'matches the given query', status_code=404,
        )


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
