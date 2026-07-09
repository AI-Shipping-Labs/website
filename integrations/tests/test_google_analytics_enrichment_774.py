"""Tests for GA enrichment + conversion events (issues #774 and #1164).

Covers:

- ``site_context['aslab_anon_id']`` validates the ``aslab_aid`` cookie
  as a UUID; bad / empty values yield ``''``.
- ``site_context`` exposes GA-safe bootstrap JSON for login state,
  member tier, and the anonymous join key without leaking PII.
- ``templates/base.html`` keeps the direct ``gtag.js`` loader, adds the
  safe GA bootstrap payloads, and renders one-shot pending events only
  when ``GOOGLE_ANALYTICS_ID`` is configured.
- When GA is unset, no ``gtag``/``googletagmanager`` markup renders at
  all (regression on #771's gate).
- The one-shot ``gtag_event_pending`` session flag is popped (not just
  read) on render, and the partial renders a properly-quoted
  ``gtag('event', ...)`` call.
- The signup-start/completed-signup handlers, OAuth fallback path,
  dashboard purchase script, and event-detail registration script all
  route through the shared analytics helper with the documented params.
"""

import json
import uuid
from pathlib import Path

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


def _load_repo_file(*parts):
    return (
        Path(__file__).resolve().parent.parent.parent.joinpath(*parts)
    ).read_text()


def _configure_oauth_provider(provider='google', name='Google'):
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    SocialApp.objects.all().delete()
    app = SocialApp.objects.create(
        provider=provider,
        name=name,
        client_id=f'{provider}-cid',
        secret=f'{provider}-secret',
    )
    app.sites.add(Site.objects.get_current())


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


class GaContextSegmentationTest(TestCase):
    """GA bootstrap JSON distinguishes login state without leaking PII."""

    def setUp(self):
        clear_config_cache()
        self.anon_id = str(uuid.uuid4())

    def tearDown(self):
        clear_config_cache()

    def test_anonymous_context_exposes_join_key_and_login_state(self):
        request = RequestFactory().get('/')
        request.COOKIES['aslab_aid'] = self.anon_id

        context = site_context(request)

        self.assertEqual(
            json.loads(context['ga_user_properties_json']),
            {
                'login_state': 'anonymous',
                'aslab_aid': self.anon_id,
            },
        )
        self.assertEqual(
            json.loads(context['ga_config_json']),
            {
                'login_state': 'anonymous',
                'user_id': self.anon_id,
            },
        )
        self.assertEqual(
            json.loads(context['ga_client_context_json']),
            {
                'login_state': 'anonymous',
                'member_tier': '',
            },
        )

    def test_authenticated_context_exposes_member_tier_without_pii(self):
        from accounts.models import User

        user = User.objects.create_user(
            email='ga-auth@test.com',
            password='testpass',
            email_verified=True,
        )
        request = RequestFactory().get('/account/')
        request.user = user
        request.COOKIES['aslab_aid'] = self.anon_id

        context = site_context(request)
        user_properties_json = context['ga_user_properties_json']
        config_json = context['ga_config_json']

        self.assertEqual(
            json.loads(user_properties_json),
            {
                'login_state': 'authenticated',
                'aslab_aid': self.anon_id,
                'member_tier': 'free',
            },
        )
        self.assertEqual(
            json.loads(config_json),
            {
                'login_state': 'authenticated',
                'user_id': self.anon_id,
                'member_tier': 'free',
            },
        )
        self.assertNotIn(user.email, user_properties_json)
        self.assertNotIn(user.email, config_json)


class GtagEnrichmentRenderingTest(TestCase):
    """``base.html`` emits GA bootstrap data with the documented fields."""

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
        self.assertContains(response, "gtag('config', 'G-TESTXYZ',")
        self.assertContains(
            response,
            f'"login_state": "anonymous", "aslab_aid": "{self.anon_id}"',
        )
        self.assertContains(
            response,
            f'"login_state": "anonymous", "user_id": "{self.anon_id}"',
        )

    def test_anonymous_login_state_renders_even_without_cookie(self):
        _set_ga_id('G-TESTXYZ')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gtag('config', 'G-TESTXYZ',")
        self.assertContains(response, '"login_state": "anonymous"')
        self.assertNotContains(response, 'user_id:')
        self.assertNotContains(response, self.anon_id)

    def test_invalid_cookie_drops_join_key_but_keeps_login_state(self):
        _set_ga_id('G-TESTXYZ')
        self.client.cookies['aslab_aid'] = 'corrupted-value'
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gtag('config', 'G-TESTXYZ',")
        self.assertContains(response, '"login_state": "anonymous"')
        # The invalid value itself must not appear anywhere in the HTML.
        self.assertNotContains(response, 'corrupted-value')

    def test_authenticated_page_renders_member_tier_when_available(self):
        from accounts.models import User

        _set_ga_id('G-TESTXYZ')
        user = User.objects.create_user(
            email='ga-member@test.com',
            password='testpass',
            email_verified=True,
            signup_source='signup',
            account_activated=True,
        )
        self.client.force_login(user)
        self.client.cookies['aslab_aid'] = self.anon_id

        response = self.client.get('/account/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"login_state": "authenticated"')
        self.assertContains(response, '"member_tier": "free"')
        self.assertContains(response, f'"user_id": "{self.anon_id}"')


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
    """Newsletter signup uses the documented funnel params."""

    def test_subscribe_page_emits_signup_start_and_sign_up(self):
        response = self.client.get('/subscribe')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "analytics.trackSignup('signup_start'",
        )
        self.assertContains(
            response,
            "analytics.trackSignup('sign_up'",
        )
        self.assertContains(
            response,
            "method: 'newsletter'",
        )
        self.assertContains(
            response,
            "signup_kind: 'newsletter'",
        )


class InlineRegisterGtagEventTest(TestCase):
    """The inline register JS contains the email signup funnel contract."""

    def test_inline_register_js_emits_signup_start_and_sign_up(self):
        content = _load_repo_file('static', 'js', 'accounts', 'inline-register.js')
        self.assertIn("analytics.trackSignup('signup_start'", content)
        self.assertIn("analytics.trackSignup('sign_up'", content)
        self.assertIn("method: 'email'", content)
        self.assertIn("signup_kind: 'account'", content)


class OauthSignupStartTrackingTemplateTest(TestCase):
    """OAuth signup buttons emit signup_start only on sign-up surfaces."""

    def test_register_page_marks_oauth_links_for_signup_start_tracking(self):
        _configure_oauth_provider('google', 'Google')

        response = self.client.get('/accounts/register/')

        self.assertContains(response, 'data-ga-oauth-track-signup-start="true"')
        self.assertContains(response, 'data-ga-oauth-provider="google"')
        self.assertContains(response, "trackSignupWithNavigationFallback(")
        self.assertContains(response, "signup_kind: 'account'")
        self.assertContains(response, "window.location.assign(href)")

    def test_login_page_does_not_mark_oauth_links_as_signup_starts(self):
        _configure_oauth_provider('google', 'Google')

        response = self.client.get('/accounts/login/')

        self.assertNotContains(response, 'data-ga-oauth-provider="google"')


class EventDetailGtagEventTest(TestCase):
    """The event_detail.js fires event_register with reload gating."""

    def test_event_detail_js_emits_event_register_with_callback(self):
        content = _load_repo_file('static', 'js', 'events', 'event_detail.js')
        self.assertIn("trackWithNavigationFallback(", content)
        self.assertIn("'event_register'", content)
        self.assertIn('event_slug: slug', content)
        self.assertIn('1500', content)


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
        self.assertContains(response, "analytics.track('purchase', purchaseParams)")
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
