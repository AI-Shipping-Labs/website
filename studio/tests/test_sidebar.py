"""Tests for the reorganised Studio sidebar (issue #570).

Covers the structural expectations the spec calls out:

- Top utility row order (``Back to website`` then theme toggle), placed
  above the section groups.
- The five collapsible sections render in the expected order with the
  expected labels.
- Every existing nav link is still present and points at the same URL,
  identified by ``href`` plus link text.
- Superuser-only links (``New user``, ``API tokens``) are gated on
  ``request.user.is_superuser``.
- ``data-testid`` attributes preserved for downstream callers.
- Sections render with the right initial ``aria-expanded`` state for the
  active page (server-rendered, no JS required).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioSidebarStructureTest(TestCase):
    """The reorganised sidebar renders the expected sections and links."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.superuser = User.objects.create_user(
            email='admin@test.com',
            password='pw',
            is_staff=True,
            is_superuser=True,
        )

    def _get_studio_dashboard(self, *, superuser=False):
        email = 'admin@test.com' if superuser else 'staff@test.com'
        self.client.login(email=email, password='pw')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        return response

    # ------------------------------------------------------------------
    # Top utility row + dashboard link order
    # ------------------------------------------------------------------

    def test_top_utility_row_renders_back_to_website_then_theme_toggle(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        back_idx = body.find('<span>Back to website</span>')
        theme_idx = body.find('data-testid="theme-toggle"')
        # Section group buttons share the ``data-studio-section-toggle``
        # marker — the first one is now the Events header.
        first_section_idx = body.find('data-studio-section-toggle')

        self.assertGreater(back_idx, -1, '"Back to website" link must render')
        self.assertGreater(theme_idx, -1, 'theme toggle button must render')
        self.assertGreater(first_section_idx, -1, 'section toggles must render')
        self.assertLess(back_idx, theme_idx, 'Back to website must precede theme toggle')
        self.assertLess(theme_idx, first_section_idx, 'utility row must precede section groups')

    def test_back_to_website_link_label_and_href(self):
        response = self._get_studio_dashboard()
        # Exact label + href; ensures the label was actually renamed from
        # the old "Back to site" copy.
        self.assertContains(
            response, '<span>Back to website</span>', html=True,
        )
        self.assertContains(response, 'href="/"')

    def test_back_to_site_label_no_longer_present(self):
        response = self._get_studio_dashboard()
        self.assertNotContains(response, '<span>Back to site</span>', html=True)

    def test_theme_toggle_testid_preserved(self):
        response = self._get_studio_dashboard()
        self.assertContains(response, 'data-testid="theme-toggle"')

    def test_dashboard_link_is_first_anchor_below_utility_row(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        theme_idx = body.find('data-testid="theme-toggle"')
        dashboard_link_idx = body.find('<span>Dashboard</span>')
        first_section_idx = body.find('data-studio-section-toggle')

        self.assertGreater(dashboard_link_idx, theme_idx)
        self.assertLess(dashboard_link_idx, first_section_idx)

    # ------------------------------------------------------------------
    # Section order + labels
    # ------------------------------------------------------------------

    def test_five_section_headers_render_in_expected_order(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        # The headers are <span>Content</span> etc. inside the toggle buttons.
        # Use exact ``<span>X</span>`` to avoid matching the nested ``Users``
        # link inside the People sub-group.
        expected_order = [
            'aria-controls="studio-section-events"',
            'aria-controls="studio-section-content"',
            'aria-controls="studio-section-people"',
            'aria-controls="studio-section-marketing"',
            'aria-controls="studio-section-operations"',
        ]
        positions = [body.find(needle) for needle in expected_order]
        for needle, idx in zip(expected_order, positions):
            self.assertGreater(idx, -1, f'expected section toggle missing: {needle!r}')
        self.assertEqual(positions, sorted(positions), 'section order changed unexpectedly')

    def test_operations_section_label_replaces_system(self):
        response = self._get_studio_dashboard()
        # The new header label is exactly "Operations".
        self.assertContains(response, '<span>Operations</span>', html=True)
        # The old "System" section header must be gone. (We assert on the
        # exact uppercase paragraph the old template rendered so we don't
        # accidentally match the word "System" inside body copy or alt
        # attributes.)
        self.assertNotContains(
            response,
            '<p class="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">System</p>',
        )

    def test_old_section_headings_are_removed(self):
        response = self._get_studio_dashboard()
        # The pre-refactor template rendered ``<p class="..."><uppercase
        # heading></p>`` block for every section. Those are now <button>
        # headers; the old paragraph form must be gone for the headings
        # we merged/renamed.
        gone_old_headings = [
            'tracking-wider mb-3">Members</p>',
            'tracking-wider mb-3">Events & Outreach</p>',
            'tracking-wider mb-3">Analytics</p>',
            'tracking-wider mb-3">Users</p>',
            'tracking-wider mb-3">System</p>',
        ]
        for fragment in gone_old_headings:
            self.assertNotContains(response, fragment)

    # ------------------------------------------------------------------
    # All expected nav links by (href, label)
    # ------------------------------------------------------------------

    NON_SUPERUSER_LINKS = [
        # Content
        ('/studio/articles/', 'Articles'),
        ('/studio/courses/', 'Courses'),
        ('/studio/projects/', 'Projects'),
        ('/studio/workshops/', 'Workshops'),
        ('/studio/recordings/', 'Recordings'),
        ('/studio/downloads/', 'Downloads'),
        # People
        ('/studio/users/', 'Users'),
        ('/studio/imports/', 'Imports'),
        ('/studio/users/tier-override/', 'Tier overrides'),
        ('/studio/crm/', 'CRM'),
        ('/studio/sprints/', 'Sprints'),
        ('/studio/plans/', 'Plans'),
        # Events
        ('/studio/events/', 'Events'),
        ('/studio/event-groups/', 'Event groups'),
        ('/studio/notifications/', 'Notifications'),
        # Marketing
        ('/studio/campaigns/', 'Email campaigns'),
        ('/studio/email-templates/', 'Email templates'),
        ('/studio/announcement/', 'Site banner'),
        ('/studio/utm-campaigns/', 'UTM links'),
        ('/studio/utm-analytics/', 'UTM analytics'),
        # Operations
        ('/studio/sync/', 'Content sync'),
        ('/studio/worker/', 'Worker'),
        ('/studio/redirects/', 'Redirects'),
        ('/studio/settings/', 'Settings'),
    ]

    SUPERUSER_ONLY_LINKS = [
        ('/studio/users/new/', 'New user'),
        ('/studio/api-tokens/', 'API tokens'),
    ]

    def test_all_expected_nav_links_render_for_staff(self):
        response = self._get_studio_dashboard()
        for href, label in self.NON_SUPERUSER_LINKS:
            with self.subTest(href=href, label=label):
                self.assertContains(response, f'href="{href}"')
                self.assertContains(
                    response, f'<span>{label}</span>', html=True,
                )

    def test_superuser_only_links_render_for_superuser(self):
        response = self._get_studio_dashboard(superuser=True)
        for href, label in self.SUPERUSER_ONLY_LINKS:
            with self.subTest(href=href, label=label):
                self.assertContains(response, f'href="{href}"')
                self.assertContains(
                    response, f'<span>{label}</span>', html=True,
                )

    def test_superuser_only_links_hidden_from_non_superuser(self):
        response = self._get_studio_dashboard()
        for href, label in self.SUPERUSER_ONLY_LINKS:
            with self.subTest(href=href, label=label):
                self.assertNotContains(response, f'href="{href}"')
                self.assertNotContains(
                    response, f'<span>{label}</span>', html=True,
                )

    # ------------------------------------------------------------------
    # Preserved test-id hooks
    # ------------------------------------------------------------------

    def test_event_groups_testid_preserved(self):
        response = self._get_studio_dashboard()
        self.assertContains(response, 'data-testid="sidebar-event-groups-link"')

    def test_api_tokens_testid_preserved_for_superuser(self):
        response = self._get_studio_dashboard(superuser=True)
        self.assertContains(response, 'data-testid="api-tokens-nav-link"')

    # ------------------------------------------------------------------
    # Renamed labels — make sure the OLD labels are gone
    # ------------------------------------------------------------------

    def test_old_marketing_labels_removed(self):
        response = self._get_studio_dashboard()
        # ``Campaigns`` (the bare label) and ``UTM Campaigns`` are renamed.
        self.assertNotContains(response, '<span>Campaigns</span>', html=True)
        self.assertNotContains(response, '<span>UTM Campaigns</span>', html=True)
        self.assertNotContains(response, '<span>Announcement</span>', html=True)
        # ``UTM Analytics`` becomes ``UTM analytics`` (case change).
        self.assertNotContains(response, '<span>UTM Analytics</span>', html=True)

    def test_old_user_section_labels_removed(self):
        response = self._get_studio_dashboard(superuser=True)
        # Old labels: ``Tier Overrides``, ``User imports``, ``New User``.
        self.assertNotContains(response, '<span>Tier Overrides</span>', html=True)
        self.assertNotContains(response, '<span>User imports</span>', html=True)
        self.assertNotContains(response, '<span>New User</span>', html=True)

    def test_old_system_labels_removed(self):
        response = self._get_studio_dashboard()
        self.assertNotContains(response, '<span>Content Sync</span>', html=True)

    # ------------------------------------------------------------------
    # Default expansion state — only Events open on the dashboard
    # ------------------------------------------------------------------

    def test_dashboard_only_expands_events_section(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        # Events' <ul> is un-hidden on the dashboard (default open section).
        self.assertIn('id="studio-section-events" class="space-y-1 mt-1"', body)
        # The other four sections render with the ``hidden`` class on
        # /studio/ (which is not in any section's deep path).
        for slug in ('content', 'people', 'marketing', 'operations'):
            self.assertIn(
                f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"',
                body,
                f'Expected section {slug!r} to be hidden by default on dashboard',
            )

    def test_aria_expanded_matches_initial_visibility_on_dashboard(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        # The Events button is aria-expanded="true" on the dashboard.
        self.assertIn(
            'aria-expanded="true"\n                  aria-controls="studio-section-events"',
            body,
        )
        # The four collapsed sections render aria-expanded="false".
        for slug in ('content', 'people', 'marketing', 'operations'):
            self.assertIn(
                f'aria-expanded="false"\n                  aria-controls="studio-section-{slug}"',
                body,
                f'Expected section {slug!r} aria-expanded=false on dashboard',
            )

    # ------------------------------------------------------------------
    # Auto-expand: visiting a page inside a section opens that section
    # ------------------------------------------------------------------

    def test_visiting_crm_auto_expands_people_section(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/crm/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # People <ul> is NOT hidden — section auto-expanded server-side.
        self.assertIn('id="studio-section-people" class="space-y-1 mt-1"', body)
        # Content / Events / Marketing / Operations remain collapsed —
        # Events no longer stays open once another section is active.
        for slug in ('content', 'events', 'marketing', 'operations'):
            self.assertIn(
                f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"',
                body,
            )

    def test_visiting_articles_auto_expands_content_section(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # Content <ul> is un-hidden — section auto-expanded server-side.
        self.assertIn('id="studio-section-content" class="space-y-1 mt-1"', body)
        # Events collapses back to hidden because Content is the active
        # section now (Events is only the dashboard default).
        for slug in ('events', 'people', 'marketing', 'operations'):
            self.assertIn(
                f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"',
                body,
            )

    def test_visiting_event_groups_keeps_events_section_expanded(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/event-groups/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # Events <ul> is un-hidden because /studio/event-groups/ is in
        # the Events section's deep path.
        self.assertIn('id="studio-section-events" class="space-y-1 mt-1"', body)
        # All other sections are collapsed.
        for slug in ('content', 'people', 'marketing', 'operations'):
            self.assertIn(
                f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"',
                body,
            )

    def test_visiting_imports_expands_people_and_users_subgroup(self):
        self.client.login(email='admin@test.com', password='pw')
        response = self.client.get('/studio/imports/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # People section open.
        self.assertIn('id="studio-section-people" class="space-y-1 mt-1"', body)
        # Users children <ul> is also un-hidden.
        self.assertIn('id="studio-users-children" class="ml-6 mt-1 space-y-1"', body)
        # And the chevron button reports aria-expanded="true".
        self.assertIn(
            'aria-expanded="true"\n                        aria-controls="studio-users-children"',
            body,
        )

    def test_visiting_marketing_page_expands_marketing_section(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/campaigns/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn('id="studio-section-marketing" class="space-y-1 mt-1"', body)
        for slug in ('content', 'people', 'events', 'operations'):
            self.assertIn(
                f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"',
                body,
            )

    def test_visiting_operations_page_expands_operations_section(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('id="studio-section-operations" class="space-y-1 mt-1"', body)

    # ------------------------------------------------------------------
    # Users row interaction — anchor + chevron button are siblings
    # ------------------------------------------------------------------

    def test_users_row_has_anchor_and_chevron_button_siblings(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()

        # The anchor points at studio_user_list and is NOT wrapped around
        # the chevron button — the chevron has its own data-testid hook
        # and aria-controls.
        self.assertIn('data-studio-users-toggle', body)
        self.assertIn('aria-controls="studio-users-children"', body)

        # The chevron button must NOT live inside any <a>. We assert by
        # confirming the button has no enclosing <a> tag from the row
        # wrapper to the button: search for the substring of the <a> tag
        # opening to its closing </a> and check the button is outside it.
        users_a_open = body.find("href=\"/studio/users/\"")
        users_a_close = body.find('</a>', users_a_open) + len('</a>')
        button_idx = body.find('data-studio-users-toggle', users_a_open)
        self.assertGreater(
            button_idx,
            users_a_close,
            'Users chevron <button> must be a sibling of the <a>, not nested inside it',
        )

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def test_version_footer_renders_once_at_bottom(self):
        response = self._get_studio_dashboard()
        body = response.content.decode()
        # The version line ``v...`` is rendered exactly once in the nav.
        count = body.count('text-xs text-muted-foreground">v')
        self.assertEqual(count, 1, 'expected exactly one VERSION footer line')
