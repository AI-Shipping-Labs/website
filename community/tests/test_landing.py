"""Focused tests for the public /community overview landing page."""

from pathlib import Path

import yaml
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve

from community.views import community_landing
from content.models import SiteConfig
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _tiers_fixture():
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / 'content'
        / 'tests'
        / 'fixtures'
        / 'tiers.yaml'
    )
    with fixture_path.open(encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def seed_tier_activity_config(data=None):
    SiteConfig.objects.update_or_create(
        key='tiers',
        defaults={'data': data if data is not None else _tiers_fixture()},
    )


class CommunityLandingRoutingTest(TestCase):
    def test_community_route_resolves_to_landing_view(self):
        match = resolve('/community')
        self.assertEqual(match.func, community_landing)

    def test_no_new_api_route_is_added_for_community_landing(self):
        match = resolve('/api/community')

        self.assertNotEqual(match.func, community_landing)
        self.assertNotEqual(match.url_name, 'community_landing')


class CommunityLandingViewTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        seed_tier_activity_config()

    def test_anonymous_visitor_gets_public_community_overview(self):
        response = self.client.get('/community')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/community_landing.html')
        self.assertContains(response, 'id="site-header"')
        self.assertContains(response, '<footer', html=False)
        self.assertContains(response, 'data-testid="community-landing-page"')
        self.assertContains(
            response,
            'Community for action-oriented AI builders',
        )
        self.assertContains(
            response,
            'Ship AI projects with structure, accountability, and people building beside you.',
        )
        self.assertContains(response, 'href="/pricing"')
        self.assertContains(response, 'href="/activities#access-by-tier"')
        self.assertContains(response, 'data-testid="community-landing-pricing-cta"')
        self.assertContains(response, 'data-testid="community-landing-activities-cta"')

    def test_all_viewer_types_can_open_landing_page(self):
        users = [
            ('free-member@example.com', self.free_tier, {}),
            ('basic-member@example.com', self.basic_tier, {}),
            ('main-member@example.com', self.main_tier, {}),
            ('premium-member@example.com', self.premium_tier, {}),
            ('staff-member@example.com', self.free_tier, {'is_staff': True}),
        ]

        anonymous_response = self.client.get('/community')
        self.assertEqual(anonymous_response.status_code, 200)

        for email, tier, flags in users:
            with self.subTest(email=email):
                user = User.objects.create_user(
                    email=email,
                    password='pw',
                    **flags,
                )
                user.tier = tier
                user.save(update_fields=['tier'])
                self.client.force_login(user)

                response = self.client.get('/community')

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Community sprints')
                self.client.logout()

    def test_page_explains_required_benefit_categories_and_paths(self):
        response = self.client.get('/community')

        expected_copy = [
            'Structure and accountability',
            'Private Slack community access',
            'group coding sessions',
            'community sprints',
            'live events',
            'workshops',
            'vote on future topics',
            'mini-courses',
            'Premium career/profile feedback',
        ]
        for text in expected_copy:
            self.assertContains(response, text)

        for href in [
            '/pricing',
            '/activities#access-by-tier',
            '/sprints',
            '/events',
            '/workshops',
        ]:
            self.assertContains(response, f'href="{href}"')

    def test_tier_summary_uses_shared_activity_config(self):
        custom_config = [
            {
                'name': 'Basic',
                'stripe_key': 'basic',
                'activities': [
                    {
                        'title': 'Config-backed Basic Benefit',
                        'icon': 'book-open',
                        'description': 'Loaded from SiteConfig.',
                        'features': ['Loaded from SiteConfig.'],
                    },
                ],
            },
            {
                'name': 'Main',
                'stripe_key': 'main',
                'activities': [
                    {
                        'title': 'Config-backed Main Accountability',
                        'icon': 'users',
                        'description': 'Loaded from SiteConfig.',
                        'features': ['Loaded from SiteConfig.'],
                    },
                ],
            },
            {
                'name': 'Premium',
                'stripe_key': 'premium',
                'activities': [
                    {
                        'title': 'Config-backed Premium Feedback',
                        'icon': 'sparkles',
                        'description': 'Loaded from SiteConfig.',
                        'features': ['Loaded from SiteConfig.'],
                    },
                ],
            },
        ]
        seed_tier_activity_config(custom_config)

        response = self.client.get('/community')

        self.assertContains(response, 'data-testid="community-tier-activity-grid"')
        self.assertContains(response, 'Config-backed Basic Benefit')
        self.assertContains(response, 'Config-backed Main Accountability')
        self.assertContains(response, 'Config-backed Premium Feedback')
        self.assertContains(response, 'data-tier="basic"')
        self.assertContains(response, 'data-tier="main"')
        self.assertContains(response, 'data-tier="premium"')
        self.assertNotContains(response, 'data-testid="community-activity-fallback"')

    def test_missing_activity_config_renders_useful_fallback(self):
        SiteConfig.objects.filter(key='tiers').delete()

        response = self.client.get('/community')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="community-activity-fallback"')
        self.assertContains(response, 'Membership activities are being refreshed')
        self.assertContains(response, 'href="/activities#access-by-tier"')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'data-testid="community-tier-card"')

    def test_header_navigation_links_to_community_overview(self):
        response = self.client.get('/community')
        content = response.content.decode()
        header = content[:content.index('</header>')]

        self.assertIn('data-testid="nav-community-link-overview"', header)
        self.assertIn('href="/community"', header)
        self.assertIn('Overview', header)
        for test_id in [
            'nav-community-link-membership',
            'nav-community-link-sprints',
            'nav-community-link-events',
            'mobile-nav-community-link-overview',
            'mobile-nav-community-link-membership',
            'mobile-nav-community-link-sprints',
            'mobile-nav-community-link-events',
        ]:
            self.assertIn(f'data-testid="{test_id}"', header)

    def test_landing_page_has_specific_post_launch_seo(self):
        response = self.client.get('/community')

        self.assertContains(
            response,
            '<title>AI Shipping Labs Community | Build AI Projects Together</title>',
            html=True,
        )
        self.assertContains(
            response,
            'Join AI Shipping Labs to ship AI projects with structure, accountability',
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://aishippinglabs.com/community">',
            html=True,
        )
        self.assertContains(response, '<meta property="og:title" content="AI Shipping Labs Community">', html=True)

    def test_page_uses_post_launch_copy_and_protects_private_slack_details(self):
        response = self.client.get('/community')
        body = response.content.decode()

        forbidden = [
            'when we launch',
            'Community Launch',
            'invite-only',
            '/community/slack',
            'slack.com/invite',
            'join.slack.com',
            'hooks.slack.com',
            'data-testid="event-registration-card"',
            'data-testid="event-post-resources"',
        ]
        for text in forbidden:
            self.assertNotIn(text, body)

    def test_sitemap_includes_community_landing(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/community</loc>')
