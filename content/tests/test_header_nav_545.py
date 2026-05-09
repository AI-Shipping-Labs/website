import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from plans.models import Plan, Sprint

User = get_user_model()


RESOURCES_LINKS = [
    ('Courses', '/courses'),
    ('Workshops', '/workshops'),
    ('Learning Path', '/learning-path/ai-engineer'),
    ('Project Ideas', '/projects'),
    ('Interview Prep', '/interview'),
    ('Blog', '/blog'),
]

COMMUNITY_LINKS = [
    ('Community Sprints', '/sprints'),
    ('Events', '/events'),
]


class HeaderTextNavigationIssue545Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def _header_html(self, user=None):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        return html[:html.index('</header>')]

    def assert_public_navigation_ia(self, header):
        self.assertIn('href="/about"', header)
        self.assertIn('>About</a>', header)
        self.assertIn('href="/pricing"', header)
        self.assertIn('>Membership</a>', header)
        self.assertIn('id="community-dropdown-btn"', header)
        self.assertIn('id="resources-dropdown-btn"', header)
        self.assertIn('href="/faq"', header)
        self.assertIn('>FAQ</a>', header)
        self.assertIn('id="mobile-community-toggle"', header)
        self.assertIn('id="mobile-resources-toggle"', header)
        self.assertNotIn('id="learn-dropdown-btn"', header)
        self.assertNotIn('id="mobile-learn-toggle"', header)

        primary = header[
            header.index('data-testid="desktop-primary-nav"'):
            header.index('<div class="hidden md:flex md:items-center md:gap-4">')
        ]
        self.assertEqual(
            re.findall(r'id="([^"]+-dropdown-btn)"', primary),
            ['community-dropdown-btn', 'resources-dropdown-btn'],
        )
        for label in ['About', 'Membership', 'Community', 'Resources', 'FAQ']:
            self.assertIn(label, primary)

        self.assertLess(primary.index('>About</a>'), primary.index('>Membership</a>'))
        self.assertLess(
            primary.index('>Membership</a>'),
            primary.index('id="community-dropdown-btn"'),
        )
        self.assertLess(
            primary.index('id="community-dropdown-btn"'),
            primary.index('id="resources-dropdown-btn"'),
        )
        self.assertLess(primary.index('id="resources-dropdown-btn"'), primary.index('>FAQ</a>'))

        self.assertNotIn('>Activities</a>', primary)
        self.assertNotIn('href="/activities"', primary)

        for label, href in COMMUNITY_LINKS + RESOURCES_LINKS + [('Curated Links', '/resources')]:
            self.assertIn(f'href="{href}"', header)
            self.assertIn(label, header)

    def test_anonymous_header_exposes_groomed_public_navigation_ia(self):
        header = self._header_html()

        self.assert_public_navigation_ia(header)
        self.assertIn(reverse('account_login'), header)
        self.assertNotIn('id="notification-bell-btn"', header)
        self.assertNotIn('data-testid="account-menu"', header)

    def test_authenticated_header_preserves_existing_account_controls(self):
        user = User.objects.create_user(
            email='member545@example.com',
            password='pw',
            first_name='Member',
        )
        plan = Plan.objects.create(member=user, sprint=self.sprint)

        header = self._header_html(user)

        self.assert_public_navigation_ia(header)
        self.assertIn('id="notification-bell-btn"', header)
        self.assertIn('data-testid="account-menu"', header)
        self.assertIn('data-testid="theme-toggle"', header)
        self.assertIn('href="/account/#profile"', header)
        self.assertIn(reverse('account_logout'), header)
        self.assertIn(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': self.sprint.slug, 'plan_id': plan.pk},
            ),
            header,
        )
        self.assertIn('data-testid="header-plan-link"', header)
        self.assertIn('data-testid="mobile-header-plan-link"', header)
        self.assertNotIn('>My Plan<', header)

    def test_staff_header_keeps_studio_inside_account_controls(self):
        staff = User.objects.create_user(
            email='staff545@example.com',
            password='pw',
            is_staff=True,
        )

        header = self._header_html(staff)

        self.assert_public_navigation_ia(header)
        self.assertIn(reverse('studio_dashboard'), header)
        self.assertIn('data-testid="header-admin-role-badge"', header)
        primary = header[
            header.index('data-testid="desktop-primary-nav"'):
            header.index('<div class="hidden md:flex md:items-center md:gap-4">')
        ]
        self.assertNotIn(reverse('studio_dashboard'), primary)

    def test_public_nav_destinations_continue_to_resolve(self):
        for path in [
            '/activities',
            '/about',
            '/pricing',
            '/faq',
            '/events',
            '/resources',
            '/courses',
            '/workshops',
            '/projects',
            '/interview',
            '/blog',
            '/learning-path/ai-engineer',
        ]:
            with self.subTest(path=path):
                match = resolve(path)
                self.assertIsNotNone(match.func)
