import re
from datetime import timedelta
from pathlib import Path

import yaml
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.utils import timezone

from accounts.templatetags.accounts_extras import button_classes
from content.models import SiteConfig
from events.models import Event
from tests.fixtures import TierSetupMixin

HOME_TEMPLATE = Path(settings.BASE_DIR) / 'templates' / 'home.html'


def _anchor_with_testid(source, testid):
    match = re.search(
        rf'<a\b(?=[^>]*\bdata-testid="{re.escape(testid)}")[^>]*>',
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f'Could not find anchor with data-testid={testid!r}')
    return match.group(0)


HOME_CTA_OWNERS = {
    'home-hero-activities-cta': (
        "{% button_classes 'primary' size='lg' extra='w-full sm:w-auto' %}",
        'primary',
        'lg',
        'w-full sm:w-auto',
    ),
    'home-hero-tiers-cta': (
        "{% button_classes 'secondary' size='lg' extra='w-full sm:w-auto' %}",
        'secondary',
        'lg',
        'w-full sm:w-auto',
    ),
    'home-upcoming-events-link': (
        "{% button_classes 'secondary' size='lg' extra='shrink-0' %}",
        'secondary',
        'lg',
        'shrink-0',
    ),
    'home-workshops-link': (
        "{% button_classes 'secondary' size='lg' extra='shrink-0' %}",
        'secondary',
        'lg',
        'shrink-0',
    ),
}


def _seed_site_config_tiers():
    fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
    with fixture_path.open(encoding='utf-8') as handle:
        tiers_data = yaml.safe_load(handle)
    SiteConfig.objects.update_or_create(key='tiers', defaults={'data': tiers_data})


def _make_event(slug, *, start_datetime, **overrides):
    defaults = {
        'title': slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': start_datetime,
        'status': 'upcoming',
        'published': True,
        'description': f'Description for {slug}.',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class HomepageFunnelTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _seed_site_config_tiers()

    @staticmethod
    def _body(response):
        return response.content.decode()

    def _section(self, response, section_id):
        body = self._body(response)
        start = body.index(f'<section id="{section_id}"')
        end = body.find('<section ', start + 1)
        return body[start:end if end != -1 else len(body)]

    def _card(self, response, slug):
        tier = self._section(response, 'tiers')
        start = tier.index(f'data-tier-card="{slug}"')
        end = tier.find('data-tier-card="', start + 1)
        return tier[start:end if end != -1 else len(tier)]

    def test_changed_home_ctas_use_the_documented_button_owner(self):
        source = HOME_TEMPLATE.read_text(encoding='utf-8')

        for testid, (owner_call, _variant, _size, _extra) in HOME_CTA_OWNERS.items():
            with self.subTest(testid=testid):
                anchor = _anchor_with_testid(source, testid)
                self.assertIn(f'class="{owner_call}"', anchor)
                self.assertEqual(anchor.count('{% button_classes '), 1)

    def test_changed_home_ctas_render_exact_owned_variants_and_sizes(self):
        _make_event('cta-event', start_datetime=timezone.now() + timedelta(days=1))
        body = self._body(self.client.get('/'))

        for testid, (_owner_call, variant, size, extra) in HOME_CTA_OWNERS.items():
            with self.subTest(testid=testid):
                anchor = _anchor_with_testid(body, testid)
                class_match = re.search(r'\bclass="([^"]*)"', anchor)
                self.assertIsNotNone(class_match)
                self.assertEqual(
                    class_match.group(1),
                    button_classes(variant, size=size, extra=extra),
                )

    def test_anonymous_home_renders_binding_section_order(self):
        _make_event(
            'member-session',
            start_datetime=timezone.now() + timedelta(days=1),
        )
        response = self.client.get('/')
        body = self._body(response)
        markers = [
            'id="about"',
            'id="activities"',
            'id="sprint-story"',
            'id="upcoming-events"',
            'id="testimonials"',
            'id="tiers"',
            'id="join-free"',
            'id="blog"',
            'id="workshops"',
            'id="faq"',
            'id="newsletter"',
        ]
        offsets = [body.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))

    def test_value_story_and_hero_precede_membership(self):
        response = self.client.get('/')
        body = self._body(response)
        for title in [
            'Accountability circles',
            'Group learning',
            'Building sessions',
            'Trend breakdowns',
            'Career support',
        ]:
            self.assertContains(response, title, count=1)
        self.assertContains(response, 'A personalized onboarding plan')
        self.assertNotContains(response, 'Main + Premium')
        self.assertContains(response, 'href="/activities#access-by-tier"')
        self.assertContains(response, 'href="/#activities"')
        self.assertContains(response, 'href="/#tiers"')
        self.assertNotContains(response, 'Browse Resources')
        self.assertLess(body.index('id="activities"'), body.index('id="tiers"'))
        self.assertLess(body.index('id="sprint-story"'), body.index('id="tiers"'))
        self.assertLess(body.index('id="testimonials"'), body.index('id="tiers"'))

    def test_free_signup_lives_only_in_separate_section(self):
        response = self.client.get('/')
        body = self._body(response)
        tiers = self._section(response, 'tiers')
        join = self._section(response, 'join-free')
        self.assertNotIn('data-tier-card="free"', tiers)
        self.assertNotIn('Join free', tiers)
        self.assertLess(body.index('id="tiers"'), body.index('id="join-free"'))
        self.assertLess(body.index('id="join-free"'), body.index('id="blog"'))
        self.assertNotIn('id="register-form"', tiers)
        self.assertIn('data-testid="home-join-free-form"', join)
        self.assertEqual(join.count('id="register-form"'), 1)
        self.assertEqual(body.count('id="register-form"'), 1)
        self.assertIn('Start free', join)
        self.assertIn('Create your free account', join)
        self.assertIn('Already have an account?', join)
        self.assertIn('Sign in', join)

    def test_home_form_uses_shared_accessible_registration_assets(self):
        response = self.client.get('/')
        join = self._section(response, 'join-free')
        for expected in [
            'autocomplete="email"',
            'autocomplete="new-password"',
            'role="alert"',
            'aria-live="assertive"',
            'aria-busy="false"',
            'min-h-[44px]',
            'creating an account',
            'inline-register-opt-in',
        ]:
            self.assertIn(expected, join)
        body = self._body(response)
        self.assertEqual(body.count('/static/js/accounts/auth-helpers.js'), 1)
        self.assertEqual(body.count('/static/js/accounts/inline-register.js'), 1)
        self.assertEqual(body.count('id="auth-next-url"'), 1)
        self.assertIn('scroll-mt-24', join)

    def test_oauth_provider_is_only_in_separate_conversion_section(self):
        app = SocialApp.objects.create(
            provider='google', name='Google', client_id='home-google', secret='secret'
        )
        app.sites.add(Site.objects.get_current())
        response = self.client.get('/')
        self.assertIn('Sign up with Google', self._section(response, 'join-free'))
        self.assertNotIn('Sign up with Google', self._section(response, 'tiers'))
        # The email path now navigates to /accounts/register/ instead of
        # expanding an inline form, which made the page reflow.
        self.assertContains(response, 'data-testid="inline-register-email-link"')

    def test_no_oauth_provider_expands_email_form(self):
        SocialApp.objects.all().delete()
        response = self.client.get('/')
        join = self._section(response, 'join-free')
        self.assertIn('id="register-email"', join)
        self.assertNotIn('inline-register-email-block" hidden', join)
        self.assertNotIn('data-auth-oauth-providers', join)

    def test_paid_tiers_and_pricing_page_remain_independent(self):
        response = self.client.get('/')
        for slug, price in [('basic', 20), ('main', 50), ('premium', 100)]:
            card = self._card(response, slug)
            self.assertIn(f'&euro;{price}', card)
            self.assertIn(
                f'href="{settings.STRIPE_PAYMENT_LINKS[slug]["monthly"]}"', card
            )
            self.assertIn('data-link-annual=', card)
        self.assertIn('Most popular', self._card(response, 'main'))

        pricing = self.client.get('/pricing')
        self.assertContains(pricing, 'pricing-inline-register-embed')
        self.assertContains(pricing, 'data-testid="inline-register-card"')
        self.assertNotContains(pricing, 'data-testid="home-join-free-section"')

    def test_upcoming_section_is_live_schedule_and_omitted_when_empty(self):
        empty = self.client.get('/')
        self.assertNotContains(empty, 'id="upcoming-events"')
        self.assertNotContains(empty, 'home-upcoming-events-empty')
        self.assertNotIn('upcoming-events', [item['id'] for item in empty.context['section_nav']])

        _make_event('live-build', start_datetime=timezone.now() + timedelta(days=2))
        populated = self.client.get('/')
        self.assertContains(populated, 'Live sessions on the calendar')
        self.assertContains(populated, 'See what the community is doing live')
        self.assertContains(populated, 'href="/events?filter=upcoming"')
        self.assertIn('upcoming-events', [item['id'] for item in populated.context['section_nav']])

    def test_sampling_and_past_recordings_proof_are_removed(self):
        _make_event(
            'past-recording',
            start_datetime=timezone.now() - timedelta(days=2),
            status='completed',
            recording_url='https://video.test/recording',
        )
        response = self.client.get('/')
        self.assertNotIn('recordings', response.context)
        for forbidden in [
            'Sample the community before you commit',
            'home-past-recordings-section',
            'home-recordings-carousel',
            'View all past recordings',
        ]:
            self.assertNotContains(response, forbidden)

    def test_section_navigation_has_no_dead_target(self):
        response = self.client.get('/')
        body = self._body(response)
        ids = [item['id'] for item in response.context['section_nav']]
        self.assertEqual(
            ids,
            [
                'about', 'activities', 'sprint-story', 'testimonials', 'tiers',
                'join-free', 'blog', 'workshops', 'newsletter', 'faq',
            ],
        )
        for section_id in ids:
            self.assertIn(f'id="{section_id}"', body)

    def test_authenticated_member_keeps_dashboard_without_anonymous_assets(self):
        user = get_user_model().objects.create_user(
            email='member1241@example.com', password='pw', tier=self.main_tier
        )
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'content/dashboard.html')
        for forbidden in [
            'id="join-free"', 'id="register-form"', 'auth-next-url',
            '/static/js/accounts/inline-register.js', 'home-free-tier-cta',
        ]:
            self.assertNotContains(response, forbidden)

    def test_standalone_registration_remains_canonical(self):
        response = self.client.get('/accounts/register/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="register-form"')
        self.assertContains(response, 'Create account')
