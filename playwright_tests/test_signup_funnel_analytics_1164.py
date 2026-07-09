"""Playwright coverage for GA signup funnel tracking (issue #1164)."""

import json
import os
import uuid

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_analytics_setting():
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.filter(key='GOOGLE_ANALYTICS_ID').delete()
    clear_config_cache()
    connection.close()


def _set_analytics_setting(value='G-TEST1164'):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='GOOGLE_ANALYTICS_ID',
        defaults={
            'value': value,
            'group': 'analytics',
            'is_secret': False,
        },
    )
    clear_config_cache()
    connection.close()


def _configure_oauth(*providers):
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    SocialApp.objects.all().delete()
    site = Site.objects.get_current()
    names = {
        'google': 'Google',
        'github': 'GitHub',
        'slack': 'Slack',
    }
    for provider in providers:
        app = SocialApp.objects.create(
            provider=provider,
            name=names[provider],
            client_id=f'{provider}-cid',
            secret=f'{provider}-secret',
        )
        app.sites.add(site)
    connection.close()


def _add_public_cookies(context, anon_id):
    context.add_cookies([
        {
            'name': 'aslab_aid',
            'value': anon_id,
            'domain': '127.0.0.1',
            'path': '/',
            'httpOnly': False,
        },
        {
            'name': 'csrftoken',
            'value': 'e2e-test-csrf-token-value',
            'domain': '127.0.0.1',
            'path': '/',
        },
    ])


def _normalized_data_layer(page):
    return page.evaluate(
        """() => (window.dataLayer || []).map((entry) => {
            try {
              return Array.from(entry);
            } catch (err) {
              return entry;
            }
        })"""
    )


def _event_payloads(page, event_name):
    payloads = []
    for entry in _normalized_data_layer(page):
        if (
            isinstance(entry, list)
            and len(entry) >= 3
            and entry[0] == 'event'
            and entry[1] == event_name
        ):
            payloads.append(entry[2])
    return payloads


def _last_command_payload(page, command_name, second_arg):
    for entry in reversed(_normalized_data_layer(page)):
        if (
            isinstance(entry, list)
            and len(entry) >= 3
            and entry[0] == command_name
            and entry[1] == second_arg
        ):
            return entry[2]
    raise AssertionError(
        f'No dataLayer command {command_name!r} with arg {second_arg!r} was recorded.'
    )


def _seed_user(db_blocker, email, **kwargs):
    with db_blocker.unblock():
        _create_user(email=email, **kwargs)


def _ensure_public_tiers(db_blocker):
    with db_blocker.unblock():
        _ensure_tiers()


@pytest.mark.django_db(transaction=True)
class TestSignupFunnelAnalytics:
    @pytest.mark.core
    def test_newsletter_signup_pushes_signup_start_and_sign_up(
        self, django_server, browser
    ):
        _set_analytics_setting()
        context = browser.new_context()
        _add_public_cookies(context, str(uuid.uuid4()))
        page = context.new_page()

        email = f'newsletter-{uuid.uuid4().hex[:8]}@test.com'
        page.goto(f'{django_server}/subscribe', wait_until='domcontentloaded')
        page.fill('input[name="email"]', email)
        page.click('button[type="submit"]')
        page.locator('.subscribe-message').wait_for(state='visible')

        signup_start = _event_payloads(page, 'signup_start')[-1]
        sign_up = _event_payloads(page, 'sign_up')[-1]

        assert signup_start == {
            'method': 'newsletter',
            'signup_kind': 'newsletter',
            'entry_path': '/subscribe',
            'login_state': 'anonymous',
        }
        assert sign_up == signup_start
        serialized = json.dumps({'signup_start': signup_start, 'sign_up': sign_up})
        assert email not in serialized

    @pytest.mark.core
    def test_inline_email_registration_pushes_signup_start_and_sign_up(
        self, django_server, browser, django_db_blocker
    ):
        _set_analytics_setting()
        _ensure_public_tiers(django_db_blocker)
        context = browser.new_context()
        _add_public_cookies(context, str(uuid.uuid4()))
        page = context.new_page()

        email = f'inline-register-{uuid.uuid4().hex[:8]}@test.com'
        page.goto(f'{django_server}/pricing', wait_until='domcontentloaded')
        page.fill('#register-email', email)
        page.fill('#register-password', 'TestPass123!')
        page.fill('#register-password-confirm', 'TestPass123!')
        page.click('#register-submit')
        page.locator('#register-success').wait_for(state='visible')

        signup_start = _event_payloads(page, 'signup_start')[-1]
        sign_up = _event_payloads(page, 'sign_up')[-1]

        assert signup_start == {
            'method': 'email',
            'signup_kind': 'account',
            'entry_path': '/pricing',
            'login_state': 'anonymous',
        }
        assert sign_up == signup_start
        assert email not in json.dumps({'signup_start': signup_start, 'sign_up': sign_up})

    @pytest.mark.core
    def test_oauth_signup_click_pushes_signup_start(
        self, django_server, browser, django_db_blocker
    ):
        _set_analytics_setting()
        _ensure_public_tiers(django_db_blocker)
        _configure_oauth('google')
        context = browser.new_context()
        _add_public_cookies(context, str(uuid.uuid4()))
        page = context.new_page()
        page.route(
            '**/accounts/google/login/**',
            lambda route: route.fulfill(
                status=200,
                content_type='text/html',
                body='<html><body>mock google oauth start</body></html>',
            ),
        )

        page.goto(f'{django_server}/pricing', wait_until='domcontentloaded')
        page.evaluate(
            """() => {
                sessionStorage.removeItem('oauth-signup-start');
                var originalPush = window.dataLayer.push.bind(window.dataLayer);
                window.dataLayer.push = function(entry) {
                  try {
                    var normalized = Array.from(entry);
                    if (
                      normalized[0] === 'event'
                      && normalized[1] === 'signup_start'
                    ) {
                      sessionStorage.setItem(
                        'oauth-signup-start',
                        JSON.stringify(normalized[2] || {})
                      );
                    }
                  } catch (err) {}
                  return originalPush(entry);
                };
            }"""
        )
        google_link = page.get_by_role('link', name='Sign up with Google')
        google_link.click(no_wait_after=True)
        page.wait_for_url('**/accounts/google/login/**', timeout=2500)
        signup_start = page.evaluate(
            "() => JSON.parse(sessionStorage.getItem('oauth-signup-start'))"
        )
        assert signup_start == {
            'method': 'oauth',
            'provider': 'google',
            'signup_kind': 'account',
            'entry_path': '/pricing',
            'login_state': 'anonymous',
        }

    @pytest.mark.core
    def test_oauth_navigation_survives_blocked_analytics(
        self, django_server, browser, django_db_blocker
    ):
        _set_analytics_setting()
        _ensure_public_tiers(django_db_blocker)
        _configure_oauth('github')
        context = browser.new_context()
        _add_public_cookies(context, str(uuid.uuid4()))
        page = context.new_page()
        page.route(
            '**/accounts/github/login/**',
            lambda route: route.fulfill(
                status=200,
                content_type='text/html',
                body='<html><body>mock github oauth start</body></html>',
            ),
        )

        page.goto(f'{django_server}/accounts/register/', wait_until='domcontentloaded')
        page.evaluate(
            """() => {
                sessionStorage.removeItem('oauth-signup-start');
                window.gtag = function() {
                  var args = Array.from(arguments);
                  if (args[0] === 'event' && args[1] === 'signup_start') {
                    sessionStorage.setItem(
                      'oauth-signup-start',
                      JSON.stringify(args[2] || {})
                    );
                  }
                };
            }"""
        )
        page.get_by_role('link', name='Sign up with GitHub').click(no_wait_after=True)
        page.wait_for_url('**/accounts/github/login/**', timeout=2500)

        signup_start = page.evaluate(
            "() => JSON.parse(sessionStorage.getItem('oauth-signup-start'))"
        )
        assert signup_start == {
            'method': 'oauth',
            'provider': 'github',
            'signup_kind': 'account',
            'entry_path': '/accounts/register/',
            'login_state': 'anonymous',
        }

    @pytest.mark.core
    def test_ga_bootstrap_distinguishes_anonymous_and_authenticated_users(
        self, django_server, browser, django_db_blocker
    ):
        _set_analytics_setting()
        anon_id = str(uuid.uuid4())

        anon_context = browser.new_context()
        _add_public_cookies(anon_context, anon_id)
        anon_page = anon_context.new_page()
        anon_page.goto(f'{django_server}/blog', wait_until='domcontentloaded')

        anon_user_properties = _last_command_payload(
            anon_page, 'set', 'user_properties'
        )
        anon_config = _last_command_payload(anon_page, 'config', 'G-TEST1164')

        assert anon_user_properties == {
            'login_state': 'anonymous',
            'aslab_aid': anon_id,
        }
        assert anon_config == {
            'login_state': 'anonymous',
            'user_id': anon_id,
        }

        email = f'ga-member-{uuid.uuid4().hex[:8]}@test.com'
        _seed_user(django_db_blocker, email, tier_slug='free')
        auth_context = _auth_context(browser, email)
        _add_public_cookies(auth_context, anon_id)
        auth_page = auth_context.new_page()
        auth_page.goto(f'{django_server}/account/', wait_until='domcontentloaded')

        auth_user_properties = _last_command_payload(
            auth_page, 'set', 'user_properties'
        )
        auth_config = _last_command_payload(auth_page, 'config', 'G-TEST1164')

        assert auth_user_properties == {
            'login_state': 'authenticated',
            'aslab_aid': anon_id,
            'member_tier': 'free',
        }
        assert auth_config == {
            'login_state': 'authenticated',
            'user_id': anon_id,
            'member_tier': 'free',
        }

        analytics_payload = json.dumps(
            {
                'anon_user_properties': anon_user_properties,
                'anon_config': anon_config,
                'auth_user_properties': auth_user_properties,
                'auth_config': auth_config,
            }
        )
        assert email not in analytics_payload

    @pytest.mark.core
    def test_ga_disabled_renders_no_loader_and_email_signup_still_works(
        self, django_server, browser, django_db_blocker
    ):
        _clear_analytics_setting()
        _ensure_public_tiers(django_db_blocker)
        context = browser.new_context()
        _add_public_cookies(context, str(uuid.uuid4()))
        page = context.new_page()
        page_errors = []
        page.on('pageerror', lambda err: page_errors.append(str(err)))

        for path in ['/', '/pricing', '/accounts/register/']:
            page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
            html = page.content()
            assert 'googletagmanager.com' not in html
            assert "gtag('js'" not in html
            assert "gtag('config'" not in html

        email = f'ga-disabled-{uuid.uuid4().hex[:8]}@test.com'
        page.fill('#register-email', email)
        page.fill('#register-password', 'TestPass123!')
        page.fill('#register-password-confirm', 'TestPass123!')
        page.click('#register-submit')
        page.locator('#register-success').wait_for(state='visible')

        assert page.evaluate("() => typeof window.gtag") == 'undefined'
        assert page.evaluate("() => window.dataLayer === undefined") is True
        assert not page_errors
