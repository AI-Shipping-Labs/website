import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from plans.models import Plan, Sprint

User = get_user_model()


ABOUT_LINKS = [
    ('About', '/about'),
    ('Team', '/about#team'),
    ('FAQ', '/faq'),
]

COMMUNITY_LINKS = [
    ('Membership', '/pricing'),
    ('Community Sprints', '/sprints'),
    ('Events', '/events'),
]

RESOURCES_LINKS = [
    ('Blog', '/blog'),
    ('Courses', '/courses'),
    ('Workshops', '/workshops'),
    ('Learning Paths', '/learning-path/ai-engineer'),
    ('Project Ideas', '/projects'),
    ('Interview Prep', '/interview'),
    ('Curated Links', '/resources'),
]


class HeaderTextNavigationIssue580Test(TestCase):
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

    def _primary_nav(self, header):
        return header[
            header.index('data-testid="desktop-primary-nav"'):
            header.index('<div class="hidden md:flex md:items-center md:gap-4">')
        ]

    def assert_public_navigation_ia(self, header):
        # Three desktop dropdown triggers in the new order: about, community, resources.
        primary = self._primary_nav(header)
        self.assertEqual(
            re.findall(r'id="([^"]+-dropdown-btn)"', primary),
            ['about-dropdown-btn', 'community-dropdown-btn', 'resources-dropdown-btn'],
        )

        # Top-level test ids in left-to-right order.
        top_level_ids = re.findall(
            r'data-testid="(nav-about-trigger|nav-membership|nav-community-trigger|nav-sprints|nav-events|nav-resources-trigger)"',
            primary,
        )
        self.assertEqual(
            top_level_ids,
            [
                'nav-about-trigger',
                'nav-membership',
                'nav-community-trigger',
                'nav-sprints',
                'nav-events',
                'nav-resources-trigger',
            ],
        )

        # FAQ is no longer a top-level link — it only appears inside the
        # About dropdown, never as a sibling of the trigger buttons.
        self.assertNotIn('data-testid="nav-faq"', primary)
        faq_occurrences = re.findall(r'href="/faq"', primary)
        self.assertEqual(len(faq_occurrences), 1)
        about_start = primary.index('id="about-dropdown"')
        about_end = primary.index('id="community-dropdown-btn"')
        self.assertIn('href="/faq"', primary[about_start:about_end])

        # Activities is not in top-level nav (regression check from #555).
        self.assertNotIn('>Activities</a>', primary)
        self.assertNotIn('href="/activities"', primary)

        # About dropdown contents and order.
        about_panel = self._slice_block(primary, 'about-dropdown')
        about_link_ids = re.findall(r'data-testid="(nav-about-link-[^"]+)"', about_panel)
        self.assertEqual(
            about_link_ids,
            ['nav-about-link-about', 'nav-about-link-team', 'nav-about-link-faq'],
        )
        for label, href in ABOUT_LINKS:
            self.assertIn(f'href="{href}"', about_panel)
            self.assertIn(label, about_panel)

        # Community dropdown contents and order.
        community_panel = self._slice_block(primary, 'community-dropdown')
        community_link_ids = re.findall(
            r'data-testid="(nav-community-link-[^"]+)"', community_panel
        )
        self.assertEqual(
            community_link_ids,
            [
                'nav-community-link-membership',
                'nav-community-link-sprints',
                'nav-community-link-events',
            ],
        )
        for label, href in COMMUNITY_LINKS:
            self.assertIn(f'href="{href}"', community_panel)
            self.assertIn(label, community_panel)

        # Resources dropdown contents and order — Blog is first, label is `Learning Paths`.
        resources_panel = self._slice_block(primary, 'resources-dropdown')
        resources_link_ids = re.findall(
            r'data-testid="(nav-resources-link-[^"]+)"', resources_panel
        )
        self.assertEqual(
            resources_link_ids,
            [
                'nav-resources-link-blog',
                'nav-resources-link-courses',
                'nav-resources-link-workshops',
                'nav-resources-link-learning-paths',
                'nav-resources-link-projects',
                'nav-resources-link-interview',
                'nav-resources-link-curated-links',
            ],
        )
        for label, href in RESOURCES_LINKS:
            self.assertIn(f'href="{href}"', resources_panel)
            self.assertIn(label, resources_panel)

        # Mobile accordions: about, community, resources — in that order.
        mobile_section = header[header.index('id="mobile-menu"'):]
        mobile_toggle_ids = re.findall(
            r'id="(mobile-(?:about|community|resources)-toggle)"', mobile_section
        )
        self.assertEqual(
            mobile_toggle_ids,
            ['mobile-about-toggle', 'mobile-community-toggle', 'mobile-resources-toggle'],
        )

        # Mobile order between accordions: Membership, then Community accordion,
        # then Sprints + Events as direct top-level links, then Resources accordion.
        idx_membership = mobile_section.index('data-testid="mobile-nav-membership"')
        idx_community = mobile_section.index('id="mobile-community-toggle"')
        idx_sprints = mobile_section.index('data-testid="mobile-nav-sprints"')
        idx_events = mobile_section.index('data-testid="mobile-nav-events"')
        idx_resources = mobile_section.index('id="mobile-resources-toggle"')
        self.assertLess(idx_membership, idx_community)
        self.assertLess(idx_community, idx_sprints)
        self.assertLess(idx_sprints, idx_events)
        self.assertLess(idx_events, idx_resources)

    @staticmethod
    def _slice_block(html, dropdown_id):
        """Return the HTML slice for a single dropdown panel by id."""
        start = html.index(f'id="{dropdown_id}"')
        # End at the next dropdown-btn or end of primary nav.
        next_ids = [
            html.find('id="about-dropdown-btn"', start + 1),
            html.find('id="community-dropdown-btn"', start + 1),
            html.find('id="resources-dropdown-btn"', start + 1),
        ]
        candidates = [i for i in next_ids if i != -1]
        end = min(candidates) if candidates else len(html)
        return html[start:end]

    def test_anonymous_header_exposes_groomed_public_navigation_ia(self):
        header = self._header_html()

        self.assert_public_navigation_ia(header)
        self.assertIn(reverse('account_login'), header)
        self.assertNotIn('id="notification-bell-btn"', header)
        self.assertNotIn('data-testid="account-menu"', header)

    def test_authenticated_header_preserves_existing_account_controls(self):
        user = User.objects.create_user(
            email='member580@example.com',
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
            email='staff580@example.com',
            password='pw',
            is_staff=True,
        )

        header = self._header_html(staff)

        self.assert_public_navigation_ia(header)
        self.assertIn(reverse('studio_dashboard'), header)
        self.assertIn('data-testid="header-admin-role-badge"', header)
        primary = self._primary_nav(header)
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
