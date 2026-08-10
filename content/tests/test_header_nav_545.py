import datetime
import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from content.models import Download
from content.nav_availability import (
    refresh_marketing_pages_nav_cache,
    refresh_published_downloads_nav_cache,
)
from plans.models import Plan, Sprint

User = get_user_model()


ABOUT_LINKS = [
    ('Team', '/about'),
    ('FAQ', '/faq'),
]

COMMUNITY_LINKS = [
    ('Activities', '/activities#access-by-tier'),
    ('Events', '/events'),
    ('Community Sprints', '/sprints'),
    ('Book Club', '/books'),
]

LEARNING_LINKS = [
    ('Courses', '/courses'),
    ('Workshops', '/workshops'),
    ('Learning Paths', '/learning-path/ai-engineer'),
    ('Interview Prep', '/interview'),
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
        refresh_marketing_pages_nav_cache()

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
        # Aug 2026 redesign: three desktop dropdown triggers in order
        # community, learning, about; Blog and Membership are top-level
        # links (no dropdown). Resources dropdown dissolved.
        primary = self._primary_nav(header)
        self.assertEqual(
            re.findall(r'id="([^"]+-dropdown-btn)"', primary),
            ['community-dropdown-btn', 'learning-dropdown-btn', 'about-dropdown-btn'],
        )
        self.assertNotIn('id="resources-dropdown-btn"', primary)

        # Left-to-right sequence of interactive top-level items: the three
        # dropdown triggers plus the two bare links, interleaved in order.
        top_level_ids = re.findall(
            r'data-testid="(nav-community-trigger|nav-learning-trigger'
            r'|nav-blog-link|nav-membership-link|nav-about-trigger)"',
            primary,
        )
        self.assertEqual(
            top_level_ids,
            [
                'nav-community-trigger',
                'nav-learning-trigger',
                'nav-blog-link',
                'nav-membership-link',
                'nav-about-trigger',
            ],
        )

        # Blog and Membership are top-level links, not dropdown items.
        self.assertIn('data-testid="nav-blog-link"', primary)
        self.assertIn('href="/pricing"', primary)
        membership_links = re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>\s*Membership\s*</a>', primary
        )
        self.assertEqual(membership_links, ['/pricing'])

        # FAQ is not a top-level link — it only appears inside the About
        # dropdown, never as a sibling of the trigger buttons.
        self.assertNotIn('data-testid="nav-faq"', primary)
        faq_occurrences = re.findall(r'href="/faq"', primary)
        self.assertEqual(len(faq_occurrences), 1)

        # Activities is grouped inside Community, not promoted as a top-level
        # nav link (regression check from #555).
        self.assertNotIn('data-testid="nav-activities"', primary)

        # About dropdown contents and order.
        about_panel = self._slice_block(primary, 'about-dropdown')
        about_link_ids = re.findall(r'data-testid="(nav-about-link-[^"]+)"', about_panel)
        self.assertEqual(
            about_link_ids,
            ['nav-about-link-team', 'nav-about-link-faq'],
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
                'nav-community-link-activities',
                'nav-community-link-events',
                'nav-community-link-sprints',
                'nav-community-link-books',
            ],
        )
        for label, href in COMMUNITY_LINKS:
            self.assertIn(f'href="{href}"', community_panel)
            self.assertIn(label, community_panel)
        # Past Recordings and Membership were removed from Community.
        self.assertNotIn('nav-community-link-past-recordings', community_panel)
        self.assertNotIn('nav-community-link-membership', community_panel)
        self.assertNotIn('/events?filter=past', community_panel)

        # Learning dropdown contents and order.
        learning_panel = self._slice_block(primary, 'learning-dropdown')
        learning_link_ids = re.findall(
            r'data-testid="(nav-learning-link-[^"]+)"', learning_panel
        )
        self.assertEqual(
            learning_link_ids,
            [
                'nav-learning-link-courses',
                'nav-learning-link-workshops',
                'nav-learning-link-learning-paths',
                'nav-learning-link-interview',
            ],
        )
        for label, href in LEARNING_LINKS:
            self.assertIn(f'href="{href}"', learning_panel)
            self.assertIn(label, learning_panel)

        # Project Ideas and Curated Links were dropped entirely (#1355).
        self.assertNotIn('/projects', primary)
        self.assertNotIn('Project Ideas', primary)
        self.assertNotIn('Curated Links', primary)
        self.assertNotIn('href="/resources"', primary)

        # Mobile accordions: community, learning, about — in that order.
        mobile_section = header[header.index('id="mobile-menu"'):]
        mobile_toggle_ids = re.findall(
            r'id="(mobile-(?:community|learning|about)-toggle)"', mobile_section
        )
        self.assertEqual(
            mobile_toggle_ids,
            ['mobile-community-toggle', 'mobile-learning-toggle', 'mobile-about-toggle'],
        )

        # Mobile top-level links for Blog and Membership (no accordion).
        self.assertIn('data-testid="mobile-nav-blog-link"', mobile_section)
        self.assertIn('data-testid="mobile-nav-membership-link"', mobile_section)
        idx_community = mobile_section.index('id="mobile-community-toggle"')
        idx_learning = mobile_section.index('id="mobile-learning-toggle"')
        idx_about = mobile_section.index('id="mobile-about-toggle"')
        self.assertLess(idx_community, idx_learning)
        self.assertLess(idx_learning, idx_about)

        mobile_community = mobile_section[
            mobile_section.index('id="mobile-community-list"'):idx_learning
        ]
        mobile_community_link_ids = re.findall(
            r'data-testid="(mobile-nav-community-link-[^"]+)"',
            mobile_community,
        )
        self.assertEqual(
            mobile_community_link_ids,
            [
                'mobile-nav-community-link-activities',
                'mobile-nav-community-link-events',
                'mobile-nav-community-link-sprints',
                'mobile-nav-community-link-books',
            ],
        )
        self.assertNotIn('href="/events?filter=past"', mobile_community)

        mobile_learning = mobile_section[
            mobile_section.index('id="mobile-learning-list"'):idx_about
        ]
        mobile_learning_link_ids = re.findall(
            r'data-testid="(mobile-nav-learning-link-[^"]+)"',
            mobile_learning,
        )
        self.assertEqual(
            mobile_learning_link_ids[:4],
            [
                'mobile-nav-learning-link-courses',
                'mobile-nav-learning-link-workshops',
                'mobile-nav-learning-link-learning-paths',
                'mobile-nav-learning-link-interview',
            ],
        )
        self.assertNotIn('Past Recordings', mobile_section)
        self.assertNotIn('/events?filter=past', mobile_section)

    @staticmethod
    def _slice_block(html, dropdown_id):
        """Return the HTML slice for a single dropdown panel by id."""
        start = html.index(f'id="{dropdown_id}"')
        # End at the next dropdown trigger or top-level link, or end of nav.
        next_ids = [
            html.find('id="community-dropdown-btn"', start + 1),
            html.find('id="learning-dropdown-btn"', start + 1),
            html.find('id="about-dropdown-btn"', start + 1),
            html.find('data-testid="nav-blog-link"', start + 1),
            html.find('data-testid="nav-membership-link"', start + 1),
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

        for dropdown_id in ['community', 'learning', 'about']:
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

        self.assertNotIn('data-testid="nav-learning-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-learning-link-downloads"', header,
        )

    def test_downloads_link_shown_on_desktop_and_mobile_when_published(self):
        Download.objects.create(
            title='Public Download',
            slug='public-download',
            file_url='https://example.com/download.pdf',
            published=True,
        )

        header = self._header_html()

        self.assertIn('data-testid="nav-learning-link-downloads"', header)
        self.assertIn('href="/downloads"', header)
        self.assertIn(
            'data-testid="mobile-nav-learning-link-downloads"', header,
        )

    def test_downloads_link_hidden_when_downloads_are_unpublished(self):
        Download.objects.create(
            title='Draft Download',
            slug='draft-download',
            file_url='https://example.com/draft.pdf',
            published=False,
        )

        header = self._header_html()

        self.assertNotIn('data-testid="nav-learning-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-learning-link-downloads"', header,
        )

    def test_downloads_link_updates_when_last_download_is_unpublished(self):
        download = Download.objects.create(
            title='Temporary Download',
            slug='temporary-download',
            file_url='https://example.com/temporary.pdf',
            published=True,
        )
        self.assertIn(
            'data-testid="nav-learning-link-downloads"',
            self._header_html(),
        )

        download.published = False
        download.save(update_fields=['published'])

        header = self._header_html()
        self.assertNotIn('data-testid="nav-learning-link-downloads"', header)
        self.assertNotIn(
            'data-testid="mobile-nav-learning-link-downloads"', header,
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
        self.assertNotIn('data-testid="nav-learning-link-downloads"', header)
        self.assertFalse(
            any(
                'content_download' in query['sql']
                for query in ctx.captured_queries
            ),
            'Authenticated home header should not query downloads.',
        )
