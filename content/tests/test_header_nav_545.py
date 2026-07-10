import datetime
import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from content.models import Download
from content.nav_availability import refresh_published_downloads_nav_cache
from plans.models import Plan, Sprint

User = get_user_model()


ABOUT_LINKS = [
    ('About', '/about'),
    ('Team', '/about#team'),
    ('FAQ', '/faq'),
]

COMMUNITY_LINKS = [
    ('Overview', '/community'),
    ('Membership', '/pricing'),
    ('Activities', '/activities#access-by-tier'),
    ('Community Sprints', '/sprints'),
    ('Events', '/events'),
    ('Past Recordings', '/events?filter=past'),
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
            start_date=timezone.localdate() - datetime.timedelta(days=7),
            status='active',
        )

    def setUp(self):
        refresh_published_downloads_nav_cache()

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

        # Top-level test ids in left-to-right order. Membership, Sprints,
        # and Events live only inside Community.
        top_level_ids = re.findall(
            r'data-testid="(nav-about-trigger|nav-membership|nav-community-trigger|nav-sprints|nav-events|nav-resources-trigger)"',
            primary,
        )
        self.assertEqual(
            top_level_ids,
            [
                'nav-about-trigger',
                'nav-community-trigger',
                'nav-resources-trigger',
            ],
        )
        self.assertNotIn('data-testid="nav-membership"', primary)
        self.assertNotIn('data-testid="nav-sprints"', primary)
        self.assertNotIn('data-testid="nav-events"', primary)

        # FAQ is no longer a top-level link — it only appears inside the
        # About dropdown, never as a sibling of the trigger buttons.
        self.assertNotIn('data-testid="nav-faq"', primary)
        faq_occurrences = re.findall(r'href="/faq"', primary)
        self.assertEqual(len(faq_occurrences), 1)
        about_start = primary.index('id="about-dropdown"')
        about_end = primary.index('id="community-dropdown-btn"')
        self.assertIn('href="/faq"', primary[about_start:about_end])

        # Activities is grouped inside Community, not promoted as a top-level
        # nav link (regression check from #555).
        self.assertNotIn('data-testid="nav-activities"', primary)

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
                'nav-community-link-overview',
                'nav-community-link-membership',
                'nav-community-link-activities',
                'nav-community-link-sprints',
                'nav-community-link-events',
                'nav-community-link-past-recordings',
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
        self.assertNotIn('Past Recordings', resources_panel)
        self.assertNotIn('Event Recordings', resources_panel)
        self.assertNotIn('/events?filter=past', resources_panel)

        # Mobile accordions: about, community, resources — in that order.
        mobile_section = header[header.index('id="mobile-menu"'):]
        mobile_toggle_ids = re.findall(
            r'id="(mobile-(?:about|community|resources)-toggle)"', mobile_section
        )
        self.assertEqual(
            mobile_toggle_ids,
            ['mobile-about-toggle', 'mobile-community-toggle', 'mobile-resources-toggle'],
        )

        # Mobile order between accordions: Community follows About, then Resources.
        # Membership, Sprints, and Events live only inside Community.
        self.assertNotIn('data-testid="mobile-nav-membership"', mobile_section)
        self.assertNotIn('data-testid="mobile-nav-sprints"', mobile_section)
        self.assertNotIn('data-testid="mobile-nav-events"', mobile_section)
        idx_community = mobile_section.index('id="mobile-community-toggle"')
        idx_resources = mobile_section.index('id="mobile-resources-toggle"')
        self.assertLess(idx_community, idx_resources)

        mobile_community = mobile_section[
            mobile_section.index('id="mobile-community-list"'):idx_resources
        ]
        mobile_community_link_ids = re.findall(
            r'data-testid="(mobile-nav-community-link-[^"]+)"',
            mobile_community,
        )
        self.assertEqual(
            mobile_community_link_ids,
            [
                'mobile-nav-community-link-overview',
                'mobile-nav-community-link-membership',
                'mobile-nav-community-link-activities',
                'mobile-nav-community-link-sprints',
                'mobile-nav-community-link-events',
                'mobile-nav-community-link-past-recordings',
            ],
        )
        self.assertIn('href="/events?filter=past"', mobile_community)

        mobile_resources = mobile_section[
            mobile_section.index('id="mobile-resources-list"'):
        ]
        mobile_resources_link_ids = re.findall(
            r'data-testid="(mobile-nav-resources-link-[^"]+)"',
            mobile_resources,
        )
        self.assertEqual(
            mobile_resources_link_ids[:7],
            [
                'mobile-nav-resources-link-blog',
                'mobile-nav-resources-link-courses',
                'mobile-nav-resources-link-workshops',
                'mobile-nav-resources-link-learning-paths',
                'mobile-nav-resources-link-projects',
                'mobile-nav-resources-link-interview',
                'mobile-nav-resources-link-curated-links',
            ],
        )
        self.assertNotIn('Past Recordings', mobile_resources)
        self.assertNotIn('Event Recordings', mobile_resources)
        self.assertNotIn('/events?filter=past', mobile_resources)

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

    def test_public_desktop_dropdowns_expose_keyboard_aria_contract(self):
        header = self._header_html()
        primary = self._primary_nav(header)

        for dropdown_id in ['about', 'community', 'resources']:
            with self.subTest(dropdown=dropdown_id):
                button_match = re.search(
                    rf'<button[^>]*id="{dropdown_id}-dropdown-btn"[^>]*>',
                    primary,
                )
                self.assertIsNotNone(button_match)
                button_html = button_match.group(0)
                self.assertIn('focus-visible:ring-2', button_html)
                self.assertIn('aria-haspopup="menu"', button_html)
                self.assertIn('aria-expanded="false"', button_html)
                self.assertIn(
                    f'aria-controls="{dropdown_id}-dropdown"',
                    button_html,
                )

                panel = self._slice_block(primary, f'{dropdown_id}-dropdown')
                self.assertIn('role="menu"', panel)
                self.assertIn(
                    f'aria-labelledby="{dropdown_id}-dropdown-btn"',
                    panel,
                )
                self.assertIn('role="menuitem"', panel)

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
            '/community',
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


class HeaderDownloadsNavigationTest(TestCase):
    def setUp(self):
        refresh_published_downloads_nav_cache()

    def _header_html(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(
                'content_download' in query['sql']
                for query in ctx.captured_queries
            ),
            'Header render should not query downloads for nav availability.',
        )
        html = response.content.decode()
        return html[:html.index('</header>')]

    def test_downloads_link_hidden_when_no_published_downloads_exist(self):
        header = self._header_html()

        self.assertNotIn('data-testid="nav-resources-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-resources-link-downloads"', header,
        )

    def test_downloads_link_shown_on_desktop_and_mobile_when_published(self):
        Download.objects.create(
            title='Public Download',
            slug='public-download',
            file_url='https://example.com/download.pdf',
            published=True,
        )

        header = self._header_html()

        self.assertIn('data-testid="nav-resources-link-downloads"', header)
        self.assertIn('href="/downloads"', header)
        self.assertIn(
            'data-testid="mobile-nav-resources-link-downloads"', header,
        )

    def test_downloads_link_hidden_when_downloads_are_unpublished(self):
        Download.objects.create(
            title='Draft Download',
            slug='draft-download',
            file_url='https://example.com/draft.pdf',
            published=False,
        )

        header = self._header_html()

        self.assertNotIn('data-testid="nav-resources-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-resources-link-downloads"', header,
        )

    def test_downloads_link_updates_when_last_download_is_unpublished(self):
        download = Download.objects.create(
            title='Temporary Download',
            slug='temporary-download',
            file_url='https://example.com/temporary.pdf',
            published=True,
        )
        self.assertIn(
            'data-testid="nav-resources-link-downloads"',
            self._header_html(),
        )

        download.published = False
        download.save(update_fields=['published'])

        header = self._header_html()
        self.assertNotIn('data-testid="nav-resources-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-resources-link-downloads"', header,
        )

    def test_authenticated_home_skips_downloads_nav_query(self):
        user = User.objects.create_user(
            email='member-downloads-nav@example.com',
        )
        self.client.force_login(user)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')
        header = response.content.decode().split('</header>', 1)[0]
        self.assertNotIn('data-testid="nav-resources-link-downloads"', header)
        self.assertFalse(
            any(
                'content_download' in query['sql']
                for query in ctx.captured_queries
            ),
            'Authenticated home header should not query downloads.',
        )
