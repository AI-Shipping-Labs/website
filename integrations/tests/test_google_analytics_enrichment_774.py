"""Tests for GA enrichment + conversion events (issue #774).

Covers:

- ``site_context['aslab_anon_id']`` validates the ``aslab_aid`` cookie
  as a UUID; bad / empty values yield ``''``.
- ``templates/base.html`` emits ``gtag('set', 'user_properties', ...)``
  and a second ``gtag('config', ID, { user_id: ... })`` only when both
  ``GOOGLE_ANALYTICS_ID`` is configured AND a valid ``aslab_aid``
  cookie is present.
- When GA is unset, no ``gtag``/``googletagmanager`` markup renders at
  all (regression on #771's gate).
- The one-shot ``gtag_event_pending`` session flag is popped (not just
  read) on render, and the partial renders a properly-quoted
  ``gtag('event', ...)`` call.
- The newsletter subscribe form, inline register form, dashboard
  Stripe-success script, and event_detail.js all contain the literal
  ``gtag('event', ...)`` call wired to the right trigger.
"""

import json
import uuid

from django.test import RequestFactory, TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from website.context_processors import site_context


def _set_ga_id(value='G-TESTXYZ'):
    IntegrationSetting.objects.create(
        key='GOOGLE_ANALYTICS_ID',
        value=value,
        group='analytics',
    )
    clear_config_cache()


class AslabAnonIdContextProcessorTest(TestCase):
    """``site_context['aslab_anon_id']`` validates the cookie."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_missing_cookie_returns_empty_string(self):
        request = RequestFactory().get('/')
        context = site_context(request)
        self.assertEqual(context['aslab_anon_id'], '')

    def test_valid_uuid_cookie_is_exposed(self):
        anon_id = str(uuid.uuid4())
        request = RequestFactory().get('/')
        request.COOKIES['aslab_aid'] = anon_id
        context = site_context(request)
        self.assertEqual(context['aslab_anon_id'], anon_id)

    def test_invalid_uuid_cookie_yields_empty_string(self):
        # A forged / corrupted cookie must not leak into the inline
        # script block — defensive return of '' suppresses the
        # user_property + user_id calls in base.html.
        request = RequestFactory().get('/')
        request.COOKIES['aslab_aid'] = 'not-a-uuid'
        context = site_context(request)
        self.assertEqual(context['aslab_anon_id'], '')

    def test_empty_string_cookie_yields_empty_string(self):
        request = RequestFactory().get('/')
        request.COOKIES['aslab_aid'] = ''
        context = site_context(request)
        self.assertEqual(context['aslab_anon_id'], '')


class GtagEnrichmentRenderingTest(TestCase):
    """``base.html`` emits user_property + user_id only with GA + cookie."""

    def setUp(self):
        clear_config_cache()
        self.anon_id = str(uuid.uuid4())

    def tearDown(self):
        clear_config_cache()

    def test_no_ga_markup_when_setting_unset_regression_on_771(self):
        # Even with a valid aslab_aid cookie, GA being disabled must
        # suppress every line of GA loader markup on the page. The
        # conversion-event ``gtag('event', ...)`` calls that live in
        # subscribe-form / inline-register handlers ARE still emitted
        # (their JS guards on ``typeof gtag === 'function'``), so we
        # narrow the assertions to the loader-specific strings.
        self.client.cookies['aslab_aid'] = self.anon_id
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'googletagmanager')
        # The GA bootstrap calls (``gtag('js', ...)``, ``gtag('config',
        # ...)``) only render inside the gated loader block.
        self.assertNotContains(response, "gtag('js'")
        self.assertNotContains(response, "gtag('config'")
        self.assertNotContains(response, 'user_properties')
        # The validated anon_id must NOT appear anywhere when the
        # loader block is gated off.
        self.assertNotContains(response, self.anon_id)

    def test_enrichment_renders_when_ga_and_cookie_present(self):
        _set_ga_id('G-TESTXYZ')
        self.client.cookies['aslab_aid'] = self.anon_id
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # Plain config line is still emitted.
        self.assertContains(response, "gtag('config', 'G-TESTXYZ')")
        # user_property carries the validated UUID.
        self.assertContains(
            response,
            f"gtag('set', 'user_properties', {{ aslab_aid: '{self.anon_id}' }})",
        )
        # Second gtag('config', ...) call wires user_id.
        self.assertContains(
            response,
            f"gtag('config', 'G-TESTXYZ', {{ user_id: '{self.anon_id}' }})",
        )

    def test_enrichment_suppressed_when_cookie_missing(self):
        # GA configured but no cookie (bot / admin path / pre-middleware
        # request) — render the loader but NOT the user_property /
        # user_id lines, which would otherwise emit empty strings.
        _set_ga_id('G-TESTXYZ')
        # Explicitly do NOT set the aslab_aid cookie.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gtag('config', 'G-TESTXYZ')")
        self.assertNotContains(response, 'user_properties')
        self.assertNotContains(response, 'user_id:')

    def test_enrichment_suppressed_when_cookie_is_invalid(self):
        _set_ga_id('G-TESTXYZ')
        self.client.cookies['aslab_aid'] = 'corrupted-value'
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gtag('config', 'G-TESTXYZ')")
        self.assertNotContains(response, 'user_properties')
        # The invalid value itself must not appear anywhere in the HTML.
        self.assertNotContains(response, 'corrupted-value')


class GtagPendingEventContextProcessorTest(TestCase):
    """``gtag_event_pending`` session key is popped (one-shot)."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _request_with_session(self, session_data=None):
        from django.contrib.sessions.middleware import SessionMiddleware
        request = RequestFactory().get('/')
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        if session_data:
            for k, v in session_data.items():
                request.session[k] = v
            request.session.save()
        return request

    def test_no_pending_event_returns_none(self):
        request = self._request_with_session()
        context = site_context(request)
        self.assertIsNone(context['gtag_pending_event'])

    def test_pending_event_is_popped_on_read(self):
        request = self._request_with_session({
            'gtag_event_pending': {
                'event': 'course_enroll',
                'params': {'course_slug': 'demo'},
            },
        })
        context = site_context(request)
        self.assertIsNotNone(context['gtag_pending_event'])
        self.assertEqual(context['gtag_pending_event']['event'], 'course_enroll')
        self.assertEqual(
            json.loads(context['gtag_pending_event']['params_json']),
            {'course_slug': 'demo'},
        )
        # Key is popped — second call returns None even with the same
        # session instance.
        self.assertNotIn('gtag_event_pending', request.session)
        context2 = site_context(request)
        self.assertIsNone(context2['gtag_pending_event'])

    def test_malformed_event_name_is_rejected(self):
        # Attacker-supplied or buggy event names that don't match the
        # safe identifier pattern must yield no pending event.
        request = self._request_with_session({
            'gtag_event_pending': {
                'event': "'); evil(); //",
                'params': {},
            },
        })
        context = site_context(request)
        self.assertIsNone(context['gtag_pending_event'])

    def test_non_dict_payload_is_rejected(self):
        request = self._request_with_session({
            'gtag_event_pending': 'just a string',
        })
        context = site_context(request)
        self.assertIsNone(context['gtag_pending_event'])

    def test_non_dict_params_is_rejected(self):
        request = self._request_with_session({
            'gtag_event_pending': {
                'event': 'course_enroll',
                'params': ['not', 'a', 'dict'],
            },
        })
        context = site_context(request)
        self.assertIsNone(context['gtag_pending_event'])


class GtagPendingEventRenderingTest(TestCase):
    """``base.html`` renders the pending event as a gtag('event', ...) call."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_pending_event_renders_when_ga_configured(self):
        _set_ga_id('G-TESTXYZ')
        # Use the test client's session helper to stash the flag.
        session = self.client.session
        session['gtag_event_pending'] = {
            'event': 'course_enroll',
            'params': {'course_slug': 'demo-course'},
        }
        session.save()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # The event name and JSON-serialised params appear inline.
        self.assertContains(response, "gtag('event', 'course_enroll'")
        self.assertContains(response, '"course_slug": "demo-course"')

    def test_pending_event_suppressed_when_ga_unset(self):
        # GA disabled — the GA loader block is gated off so the
        # ``gtag('event', 'course_enroll', ...)`` must not render
        # even when the session flag is present. The session key
        # should still be popped though, so it doesn't pile up.
        session = self.client.session
        session['gtag_event_pending'] = {
            'event': 'course_enroll',
            'params': {'course_slug': 'demo-course'},
        }
        session.save()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # The event-specific call is gated off (course_enroll is
        # specific enough to not collide with the subscribe-form
        # ``sign_up`` event handlers).
        self.assertNotContains(response, 'course_enroll')
        self.assertNotContains(response, "gtag('event', 'course_enroll'")
        self.assertNotContains(response, 'googletagmanager')


class NewsletterSubscribeGtagEventTest(TestCase):
    """The newsletter subscribe form fires ``sign_up`` on success."""

    def test_subscribe_form_template_emits_sign_up_event(self):
        # The handler lives in templates/includes/subscribe_form.html.
        # Any public surface that includes the partial picks up the
        # event handler. Pick the home page as the canonical surface.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "gtag('event', 'sign_up', { method: 'newsletter' })",
        )


class InlineRegisterGtagEventTest(TestCase):
    """The inline register JS contains the email ``sign_up`` event."""

    def test_inline_register_js_emits_email_sign_up(self):
        # The script is served as a static asset. Read it from disk
        # rather than hitting the URL (collectstatic isn't run in tests).
        from pathlib import Path
        path = (
            Path(__file__).resolve().parent.parent.parent
            / 'static' / 'js' / 'accounts' / 'inline-register.js'
        )
        content = path.read_text()
        self.assertIn("gtag('event', 'sign_up', { method: 'email' })", content)
        # Guarded so pages without GA configured don't ReferenceError.
        self.assertIn("typeof gtag === 'function'", content)


class EventDetailGtagEventTest(TestCase):
    """The event_detail.js fires event_register with reload gating."""

    def test_event_detail_js_emits_event_register_with_callback(self):
        from pathlib import Path
        path = (
            Path(__file__).resolve().parent.parent.parent
            / 'static' / 'js' / 'events' / 'event_detail.js'
        )
        content = path.read_text()
        # Event name and the slug parameter.
        self.assertIn("gtag('event', 'event_register'", content)
        self.assertIn('event_slug: slug', content)
        # event_callback wires the reload to the gtag callback.
        self.assertIn('event_callback: reload', content)
        # Fallback timer guarantees the reload still happens if gtag
        # is blocked / missing.
        self.assertIn('setTimeout(reload, 1500)', content)


class DashboardPurchaseGtagEventTest(TestCase):
    """Dashboard ?checkout=success script fires purchase event.

    The dashboard (``templates/content/dashboard.html``) is rendered
    by ``content.views.home._dashboard`` for authenticated users who
    visit ``/``. Newsletter-only users get redirected to ``/account/``
    instead, so we need a regular signup-source user with
    ``account_activated=True``.
    """

    def test_dashboard_template_emits_purchase_event_on_success_param(self):
        from accounts.models import User
        User.objects.create_user(
            email='dash@test.com',
            password='testpass',
            email_verified=True,
            signup_source='signup',
            account_activated=True,
        )
        self.client.login(email='dash@test.com', password='testpass')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # The script block fires the purchase event when the success
        # query string is present. The check happens client-side, so
        # we assert the literal source — Playwright covers the
        # in-browser behavior.
        self.assertContains(response, "gtag('event', 'purchase'")
        self.assertContains(response, "currency: 'EUR'")
        # Tier / value / billing are read from URLSearchParams.
        self.assertContains(response, "params.get('tier')")
        self.assertContains(response, "params.get('value')")
        self.assertContains(response, "params.get('billing')")
        # URL cleanup drops the new tier/value/billing keys too so a
        # refresh doesn't refire the event.
        self.assertContains(response, "cleanUrl.searchParams.delete('tier')")
        self.assertContains(response, "cleanUrl.searchParams.delete('value')")
        self.assertContains(response, "cleanUrl.searchParams.delete('billing')")


@override_settings(GOOGLE_ANALYTICS_ID='')
class NoGaLoaderMarkupRegressionTest(TestCase):
    """Pages render no GA-loader markup when the setting is empty.

    The GA loader script and the user_property / user_id config calls
    are gated on ``GOOGLE_ANALYTICS_ID``. The conversion-event
    ``gtag('event', ...)`` calls in form handlers ARE still emitted —
    they guard themselves with ``typeof gtag === 'function'`` at
    runtime — so the assertions here pin the loader-specific markup.
    """

    def test_home_page_has_no_ga_loader(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'googletagmanager')
        self.assertNotContains(response, "gtag('js'")
        self.assertNotContains(response, "gtag('config'")
        self.assertNotContains(response, 'user_properties')

    def test_pricing_page_has_no_ga_loader(self):
        response = self.client.get('/pricing')
        if response.status_code in (301, 302):
            response = self.client.get(response.url)
        self.assertNotContains(response, 'googletagmanager')
        self.assertNotContains(response, "gtag('js'")
        self.assertNotContains(response, "gtag('config'")
