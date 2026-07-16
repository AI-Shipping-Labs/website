"""Browser acceptance coverage for the optional analytics consent gate."""

import os
import uuid

import pytest
from playwright.sync_api import expect

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers

pytestmark = pytest.mark.local_only

OPTIONAL_COOKIES = {'aslab_aid', 'aslab_ft', 'aslab_ft_ref'}


def _set_analytics_setting():
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key='GOOGLE_ANALYTICS_ID',
        defaults={
            'value': 'G-CONSENT1164',
            'group': 'analytics',
            'is_secret': False,
        },
    )
    clear_config_cache()
    connection.close()


def _cookie_names(context):
    return {cookie['name'] for cookie in context.cookies()}


def _configure_google_oauth():
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    SocialApp.objects.filter(provider='google').delete()
    app = SocialApp.objects.create(
        provider='google',
        name='Google',
        client_id='google-consent-test',
        secret='google-consent-secret',
    )
    app.sites.add(Site.objects.get_current())
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestAnalyticsConsent:
    def test_undecided_mobile_navigation_remains_operable(
        self, django_server, browser,
    ):
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        page = context.new_page()
        page.goto(f'{django_server}/', wait_until='domcontentloaded')

        expect(page.get_by_test_id('analytics-consent-panel')).to_be_visible()
        page.locator('#mobile-menu-btn').click()
        for section in ('about', 'community', 'resources'):
            trigger = page.get_by_test_id(f'mobile-nav-{section}-trigger')
            trigger.click()
            expect(trigger).to_have_attribute('aria-expanded', 'true')

        menu_clearance = page.evaluate(
            """() => {
                const menu = document.getElementById('mobile-menu');
                const panel = document.querySelector(
                    '[data-testid="analytics-consent-panel"]'
                );
                return panel.getBoundingClientRect().top
                    - menu.getBoundingClientRect().bottom;
            }"""
        )
        assert menu_clearance >= 15

        targets = page.locator('#mobile-menu button, #mobile-menu a')
        for index in range(targets.count()):
            target = targets.nth(index)
            if not target.is_visible():
                continue
            target.scroll_into_view_if_needed()
            hit = target.evaluate(
                """element => {
                    const rect = element.getBoundingClientRect();
                    const target = document.elementFromPoint(
                        rect.left + rect.width / 2,
                        rect.top + rect.height / 2
                    );
                    return target === element || element.contains(target);
                }"""
            )
            assert hit, f'mobile menu target {index} is obstructed'

        courses = page.get_by_test_id('mobile-nav-resources-link-courses')
        courses.scroll_into_view_if_needed()
        expect(courses).to_be_visible()
        courses.click()
        page.wait_for_url(f'{django_server}/courses')
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_visible()
        assert 'aslab_analytics_consent' not in _cookie_names(context)
        context.close()

    def test_undecided_mobile_footer_privacy_link_remains_operable(
        self, django_server, browser,
    ):
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        page = context.new_page()
        page.goto(f'{django_server}/blog', wait_until='domcontentloaded')

        panel = page.get_by_test_id('analytics-consent-panel')
        expect(panel).to_be_visible()
        footer = page.locator('footer')
        footer.scroll_into_view_if_needed()
        privacy = footer.get_by_role('link', name='Privacy Policy', exact=True)
        expect(privacy).to_be_visible()
        privacy.click()
        page.wait_for_url(f'{django_server}/privacy')
        expect(page.get_by_role('heading', name='Privacy Policy')).to_be_visible()
        expect(panel).to_be_visible()
        assert 'aslab_analytics_consent' not in _cookie_names(context)
        context.close()

    @pytest.mark.parametrize(
        'viewport',
        [
            {'width': 1280, 'height': 720},
            {'width': 393, 'height': 851},
        ],
        ids=['desktop', 'mobile'],
    )
    def test_undecided_staff_can_submit_bottom_of_studio_form(
        self, django_server, browser, django_db_blocker, viewport,
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            email = f'consent-staff-{viewport["width"]}@test.com'
            _create_staff_user(email)

        context = _auth_context(browser, email)
        page = context.new_page()
        page.set_viewport_size(viewport)
        slug = f'consent-host-{viewport["width"]}'

        page.goto(f'{django_server}/studio/hosts/new', wait_until='domcontentloaded')
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_visible()
        page.fill('input[name="name"]', 'Consent Test Host')
        page.fill('input[name="slug"]', slug)
        page.fill('input[name="email"]', f'{slug}@example.com')
        page.get_by_role('button', name='Save Host').click()

        page.wait_for_url(f'{django_server}/studio/hosts/')
        expect(page.get_by_text('Consent Test Host')).to_be_visible()
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_visible()
        assert 'aslab_analytics_consent' not in _cookie_names(context)
        context.close()

    def test_undecided_and_denied_send_nothing_then_grant_enables_tracking(
        self, django_server, browser,
    ):
        _set_analytics_setting()
        context = browser.new_context()
        ga_requests = []
        context.on(
            'request',
            lambda request: ga_requests.append(request.url)
            if 'googletagmanager.com' in request.url else None,
        )
        context.route('**/googletagmanager.com/**', lambda route: route.abort())
        page = context.new_page()

        page.goto(
            f'{django_server}/?utm_source=consent&utm_campaign=gate',
            wait_until='domcontentloaded',
        )
        assert page.get_by_role('heading', name='Optional analytics').is_visible()
        assert not ga_requests
        assert not (_cookie_names(context) & OPTIONAL_COOKIES)

        with page.expect_response('**/api/analytics/consent'):
            page.get_by_role('button', name='Keep analytics off').click()
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_hidden()
        assert not ga_requests
        assert not (_cookie_names(context) & OPTIONAL_COOKIES)
        assert page.get_by_test_id('analytics-consent-panel').is_hidden()

        page.get_by_test_id('analytics-preferences-open').click()
        assert page.get_by_test_id('analytics-consent-panel').is_visible()
        with page.expect_navigation(wait_until='domcontentloaded'):
            with page.expect_response('**/api/analytics/consent'):
                page.get_by_role('button', name='Allow analytics').click()
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_hidden()
        assert ga_requests

    def test_revocation_removes_optional_cookies_without_blocking_product(
        self, django_server, browser, django_db_blocker,
    ):
        _set_analytics_setting()
        with django_db_blocker.unblock():
            ensure_tiers()
            _configure_google_oauth()
        context = browser.new_context()
        context.add_cookies([{
            'name': 'aslab_analytics_consent',
            'value': 'granted',
            'domain': '127.0.0.1',
            'path': '/',
            'httpOnly': True,
        }, {
            'name': 'aslab_aid',
            'value': '1a85ca2e-6775-4be5-9c23-4946f32be75c',
            'domain': '127.0.0.1',
            'path': '/',
            'httpOnly': True,
        }])
        context.route('**/googletagmanager.com/**', lambda route: route.abort())
        page = context.new_page()
        page.goto(f'{django_server}/?utm_source=consent', wait_until='domcontentloaded')
        assert 'aslab_aid' in _cookie_names(context)

        page.get_by_test_id('analytics-preferences-open').click()
        with page.expect_navigation(wait_until='domcontentloaded'):
            with page.expect_response('**/api/analytics/consent'):
                page.get_by_role('button', name='Keep analytics off').click()
        expect(page.get_by_test_id('analytics-consent-panel')).to_be_hidden()
        assert not (_cookie_names(context) & OPTIONAL_COOKIES)

        page.route(
            '**/accounts/google/login/**',
            lambda route: route.fulfill(
                status=200,
                content_type='text/html',
                body='<h1>Mock Google OAuth start</h1>',
            ),
        )
        page.goto(
            f'{django_server}/accounts/register/',
            wait_until='domcontentloaded',
        )
        expect(page.get_by_role('heading', name='Create account')).to_be_visible()
        with page.expect_request('**/accounts/google/login/**'):
            page.get_by_role('link', name='Sign up with Google').click()
        expect(page.get_by_role('heading', name='Mock Google OAuth start')).to_be_visible()

        page.goto(
            f'{django_server}/accounts/register/',
            wait_until='domcontentloaded',
        )
        email = f'consent-denied-{uuid.uuid4().hex[:8]}@test.com'
        page.locator('#register-email').fill(email)
        page.locator('#register-password').fill('TestPass123!')
        page.locator('#register-password-confirm').fill('TestPass123!')
        page.locator('#register-submit').click()
        page.wait_for_url(f'{django_server}/')
        expect(page.get_by_test_id('account-menu-trigger')).to_be_visible()

        page.goto(f'{django_server}/account/', wait_until='domcontentloaded')
        expect(page.get_by_role('heading', name='Account', exact=True)).to_be_visible()
        page.goto(f'{django_server}/blog', wait_until='domcontentloaded')
        expect(
            page.get_by_role('heading', name='Insights & Updates', exact=True)
        ).to_be_visible()
        assert not (_cookie_names(context) & OPTIONAL_COOKIES)
