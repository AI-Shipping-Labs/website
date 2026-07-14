from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve
from django.views.generic import RedirectView

from tests.fixtures import TierSetupMixin


class CommunityLandingRedirectTest(TierSetupMixin, TestCase):
    def test_route_is_named_permanent_redirect(self):
        match = resolve('/community')
        self.assertEqual(match.url_name, 'community_landing')
        self.assertEqual(match.func.view_class, RedirectView)

    def test_anonymous_and_authenticated_users_redirect_permanently_home(self):
        anonymous = self.client.get('/community')
        self.assertEqual(anonymous.status_code, 301)
        self.assertEqual(anonymous['Location'], '/')

        user = get_user_model().objects.create_user(
            email='community-redirect@example.com', password='pw', tier=self.main_tier
        )
        self.client.force_login(user)
        authenticated = self.client.get('/community')
        self.assertEqual(authenticated.status_code, 301)
        self.assertEqual(authenticated['Location'], '/')

    def test_old_overview_is_absent_from_nav_and_sitemap(self):
        home = self.client.get('/').content.decode()
        header = home[:home.index('</header>')]
        self.assertNotIn('nav-community-link-overview', header)
        self.assertNotIn('mobile-nav-community-link-overview', header)
        self.assertNotIn('href="/community"', header)
        desktop = header[header.index('id="community-dropdown"'):]
        self.assertLess(
            desktop.index('nav-community-link-membership'),
            desktop.index('nav-community-link-activities'),
        )
        sitemap = self.client.get('/sitemap.xml')
        self.assertNotContains(sitemap, '/community</loc>')

    def test_slack_join_route_remains_distinct(self):
        response = self.client.get('/community/slack')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn('next=/community/slack', response['Location'])
