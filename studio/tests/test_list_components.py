"""Regression tests for shared Studio content list components."""

import datetime
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event

User = get_user_model()
REPO_ROOT = Path(__file__).resolve().parents[2]


class StudioListComponentTemplateTest(TestCase):
    """The target list templates should render through shared list helpers."""

    template_paths = [
        'templates/studio/articles/list.html',
        'templates/studio/courses/list.html',
        'templates/studio/workshops/list.html',
        'templates/studio/events/list.html',
    ]
    compact_origin_template_paths = [
        'templates/studio/articles/list.html',
        'templates/studio/downloads/list.html',
        'templates/studio/recordings/list.html',
        'templates/studio/projects/list.html',
    ]

    def _template_source(self, relative_path):
        return (REPO_ROOT / relative_path).read_text()

    def test_target_lists_use_shared_filter_and_table_helpers(self):
        for path in self.template_paths:
            with self.subTest(path=path):
                source = self._template_source(path)
                self.assertIn('studio_list_filter', source)
                self.assertIn("studio_list_class 'wrapper'", source)
                self.assertIn("studio_list_class 'thead'", source)
                self.assertIn("studio_list_class 'tbody'", source)

    def test_shared_table_wrapper_opts_into_mobile_cards(self):
        from studio.templatetags.studio_filters import (
            studio_action_class,
            studio_list_class,
        )

        self.assertIn('studio-responsive-table', studio_list_class('wrapper'))
        self.assertIn('studio-actions-cell', studio_list_class('action_cell'))
        self.assertIn('studio-action-group', studio_list_class('action_group'))
        self.assertIn('whitespace-nowrap', studio_action_class('primary'))
        self.assertIn('border-accent', studio_action_class('primary'))
        self.assertIn('border-red-500/40', studio_action_class('destructive'))
        self.assertIn('border-blue-500/40', studio_action_class('async'))

    def test_target_lists_use_shared_badges_and_actions(self):
        for path in self.template_paths:
            with self.subTest(path=path):
                source = self._template_source(path)
                self.assertIn('studio_status_badge', source)
                self.assertIn('studio_list_action', source)

        for path in self.template_paths[1:3]:
            with self.subTest(path=path):
                source = self._template_source(path)
                self.assertIn('studio_origin_badge', source)

    def test_compact_lists_use_origin_badge_not_legacy_synced_badge(self):
        for path in self.compact_origin_template_paths:
            with self.subTest(path=path):
                source = self._template_source(path)
                self.assertIn('studio_origin_badge', source)
                self.assertNotIn('studio_synced_badge', source)
                self.assertNotIn('data-testid="synced-badge"', source)
                self.assertNotIn('studio-synced-badge', source)


class StudioListComponentRenderTest(TestCase):
    """Rendered pages keep behavior while using centralized markup."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-components@test.com',
            password='testpass',
            is_staff=True,
        )
        cls.now = timezone.now()
        Article.objects.create(
            title='Shared Article',
            slug='shared-article',
            date=cls.now.date(),
            published=True,
            source_repo='AI-Shipping-Labs/content',
        )
        Download.objects.create(
            title='Shared Download',
            slug='shared-download',
            file_url='https://example.com/shared.pdf',
            published=True,
            source_repo='AI-Shipping-Labs/content',
        )
        Course.objects.create(
            title='Shared Course',
            slug='shared-course',
            status='draft',
            source_repo='AI-Shipping-Labs/content',
        )
        Workshop.objects.create(
            slug='shared-workshop',
            title='Shared Workshop',
            date=datetime.date(2026, 4, 21),
            description='Hands-on intro.',
            status='published',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
            source_repo='AI-Shipping-Labs/content',
            source_commit='abc1234def5678901234567890123456789abcde',
        )
        Event.objects.create(
            title='Shared Event',
            slug='shared-event',
            start_datetime=cls.now,
            status='upcoming',
            kind='workshop',
            platform='custom',
        )
        Event.objects.create(
            title='Shared Recording',
            slug='shared-recording',
            start_datetime=cls.now,
            status='completed',
            recording_url='https://youtube.com/watch?v=shared',
            published=True,
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )
        Project.objects.create(
            title='Shared Project',
            slug='shared-project',
            date=cls.now.date(),
            status='published',
            published=True,
            source_repo='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client.login(email='staff-components@test.com', password='testpass')

    def test_shared_filter_shell_preserves_query_names_and_selection(self):
        response = self.client.get('/studio/events/?q=Shared&status=upcoming')
        self.assertContains(response, 'data-component="studio-list-filter"')
        self.assertContains(response, 'name="q" value="Shared"')
        self.assertContains(response, 'placeholder="Search events..."')
        self.assertContains(response, 'name="status"')
        self.assertContains(
            response,
            '<option value="upcoming" selected>Upcoming</option>',
            html=True,
        )
        self.assertContains(response, 'Shared Event')
        self.assertContains(response, 'Kind / Platform')
        self.assertContains(response, 'Workshop')
        self.assertContains(response, 'Custom URL')

    def test_shared_status_badges_keep_publication_and_event_colors(self):
        article_response = self.client.get('/studio/articles/')
        self.assertContains(article_response, 'data-component="studio-status-badge"')
        self.assertContains(article_response, 'bg-green-500/20 text-green-400')
        self.assertContains(article_response, 'Published')

        event_response = self.client.get('/studio/events/')
        self.assertContains(event_response, 'data-component="studio-status-badge"')
        self.assertContains(event_response, 'bg-blue-500/20 text-blue-400')
        self.assertContains(event_response, 'Upcoming')

    def test_shared_origin_badge_and_actions_preserve_list_links(self):
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'data-testid="origin-badge"')
        self.assertContains(response, 'data-origin="synced"')
        self.assertNotContains(response, 'data-testid="synced-badge"')
        self.assertNotContains(response, 'data-component="studio-synced-badge"')
        self.assertContains(response, 'data-testid="view-on-site"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, '/courses/shared-course')
        self.assertContains(response, 'View')
        self.assertContains(response, 'studio-responsive-table')
        self.assertContains(response, 'data-label="Status"')
        self.assertContains(response, 'data-label="Actions"')
        self.assertContains(response, 'studio-actions-cell')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'studio-action')
        self.assertContains(response, 'border-accent bg-accent')
        self.assertContains(response, 'border-border bg-secondary')
        self.assertContains(response, 'whitespace-nowrap')

    def test_compact_lists_render_shared_origin_badges(self):
        paths = [
            '/studio/articles/',
            '/studio/downloads/',
            '/studio/recordings/',
            '/studio/projects/',
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, 'data-testid="origin-badge"')
                self.assertContains(response, 'data-origin="synced"')
                self.assertNotContains(response, 'data-testid="synced-badge"')
                self.assertNotContains(response, 'data-component="studio-synced-badge"')

    def test_user_list_uses_primary_and_secondary_action_hierarchy(self):
        user = User.objects.create_user(
            email='action-user@test.com',
            password='testpass',
            is_staff=False,
        )
        response = self.client.get('/studio/users/?q=action-user')

        self.assertContains(response, f'/studio/users/{user.pk}/')
        self.assertContains(response, 'data-testid="user-view-link"')
        self.assertContains(response, f'/studio/impersonate/{user.pk}/')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'border-accent bg-accent')
        self.assertContains(response, 'border-border bg-secondary')

    def test_redirect_list_uses_destructive_action_confirming_item(self):
        from integrations.models import Redirect

        Redirect.objects.create(source_path='/old', target_path='/new')

        response = self.client.get('/studio/redirects/')

        self.assertContains(response, 'border-red-500/40')
        self.assertContains(
            response,
            "Delete redirect from /old to /new? This cannot be undone.",
        )

    def test_workshop_special_columns_and_empty_state_remain_intact(self):
        response = self.client.get('/studio/workshops/')
        self.assertContains(response, 'data-testid="workshop-row"')
        self.assertContains(response, 'Landing gate')
        self.assertContains(response, 'Pages gate')
        self.assertContains(response, 'Recording gate')
        self.assertContains(response, 'abc1234de')
        self.assertContains(response, 'rel="noopener noreferrer"')

        empty_response = self.client.get('/studio/workshops/?q=no-such-workshop')
        self.assertContains(empty_response, 'data-testid="workshops-empty-state"')
        self.assertContains(empty_response, 'No workshops match your filters.')
        self.assertContains(empty_response, 'Clear filters')


class StudioListRowActionPillStyleTest(TestCase):
    """Issue #740 — row-action cells must use the canonical pill style.

    The right-aligned actions ``<td>`` may not contain bare ``text-accent``
    underlined links; every action must go through ``studio_list_action``
    or ``studio_action_class`` so the geometry stays consistent across
    Studio list pages.
    """

    # Each tuple: (template_path, [(expected_substring, description), ...])
    # The expected_substring patterns appear in the action cell and prove
    # the cell uses the canonical helpers.
    canonical_call_sites = [
        ('templates/studio/plans/list.html', [
            "studio_list_action plan_detail_url 'View plan' 'primary'",
            "studio_list_action plan_edit_url 'Edit' 'secondary'",
        ]),
        ('templates/studio/sprints/list.html', [
            "studio_list_action sprint_edit_url 'Edit' 'secondary'",
        ]),
        ('templates/studio/downloads/list.html', [
            "studio_list_action download.get_absolute_url 'View on site' 'secondary' True 'noopener noreferrer'",
        ]),
        ('templates/studio/email_templates/list.html', [
            "studio_action_class 'destructive'",
            "studio_action_class 'secondary'",
        ]),
        ('templates/studio/crm/list.html', [
            "studio_action_class 'secondary'",
            'data-testid="crm-row-profile-link"',
        ]),
        ('templates/studio/api_tokens/list.html', [
            "studio_action_class 'destructive'",
        ]),
    ]

    # Files inspected — none of these may contain bare text-accent links
    # inside the right-aligned actions <td>. The regex below extracts the
    # innermost actions <td> by matching the literal data-label="Actions"
    # anchor that the canonical cells emit.
    row_action_template_paths = [
        'templates/studio/plans/list.html',
        'templates/studio/sprints/list.html',
        'templates/studio/downloads/list.html',
        'templates/studio/email_templates/list.html',
        'templates/studio/crm/list.html',
        'templates/studio/api_tokens/list.html',
    ]

    def _template_source(self, relative_path):
        return (REPO_ROOT / relative_path).read_text()

    def _action_td_blocks(self, source):
        """Return the bodies of <td> elements that mark themselves Actions.

        Every canonical row-action cell carries ``data-label="Actions"``;
        the matching close-tag is the next ``</td>`` after that marker.
        """
        blocks = []
        marker = 'data-label="Actions"'
        cursor = 0
        while True:
            anchor = source.find(marker, cursor)
            if anchor == -1:
                break
            # Walk back to the opening <td so the block is the whole cell.
            td_start = source.rfind('<td', 0, anchor)
            td_end = source.find('</td>', anchor)
            if td_start == -1 or td_end == -1:
                break
            blocks.append(source[td_start:td_end])
            cursor = td_end + len('</td>')
        return blocks

    def test_row_action_cells_have_no_bare_text_accent_links(self):
        text_accent_pattern = re.compile(r'class="[^"]*\btext-accent\b[^"]*"')
        for path in self.row_action_template_paths:
            with self.subTest(path=path):
                source = self._template_source(path)
                blocks = self._action_td_blocks(source)
                self.assertTrue(
                    blocks,
                    msg=f"{path} has no data-label=\"Actions\" <td> block",
                )
                for block in blocks:
                    self.assertIsNone(
                        text_accent_pattern.search(block),
                        msg=(
                            f"{path}: row-action <td> still contains a bare "
                            'text-accent class. Use studio_list_action / '
                            'studio_action_class instead.'
                        ),
                    )

    def test_row_action_cells_use_canonical_helpers(self):
        for path, expected_calls in self.canonical_call_sites:
            with self.subTest(path=path):
                source = self._template_source(path)
                blocks = self._action_td_blocks(source)
                self.assertTrue(
                    blocks,
                    msg=f"{path} has no data-label=\"Actions\" <td> block",
                )
                joined = '\n'.join(blocks)
                for expected in expected_calls:
                    self.assertIn(
                        expected,
                        joined,
                        msg=(
                            f"{path}: row-action <td> is missing the expected "
                            f"canonical call '{expected}'."
                        ),
                    )

    def test_email_templates_no_longer_uses_literal_actions_cell_class(self):
        source = self._template_source(
            'templates/studio/email_templates/list.html'
        )
        # The literal 'studio-actions-cell' string must come from
        # studio_list_class('action_cell') — not hand-typed.
        self.assertNotIn('px-4 py-3 studio-actions-cell', source)
        self.assertIn("studio_list_class 'action_cell'", source)

    def test_api_token_revoke_uses_destructive_kind(self):
        source = self._template_source(
            'templates/studio/api_tokens/list.html'
        )
        self.assertIn("studio_action_class 'destructive'", source)
        self.assertNotIn("studio_action_class 'danger'", source)


class StudioListRowActionRenderTest(TestCase):
    """Rendered pages render the canonical action pill classes.

    Issue #740 — the live HTML for the five updated lists must carry the
    shared ``studio-action-group`` wrapper and the kind-specific border
    colours so the operator sees consistent pills.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='row-action-staff@test.com',
            password='testpass',
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client.login(
            email='row-action-staff@test.com', password='testpass',
        )

    def test_plans_list_action_cell_renders_pill_buttons(self):
        from plans.models import Plan, Sprint

        sprint = Sprint.objects.create(
            name='Sprint A',
            slug='sprint-a',
            start_date=datetime.date(2026, 1, 1),
            duration_weeks=4,
        )
        member = User.objects.create_user(
            email='plan-member@test.com', password='testpass',
        )
        Plan.objects.create(member=member, sprint=sprint, goal='Ship it.')

        response = self.client.get('/studio/plans/')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'border-accent bg-accent')
        self.assertContains(response, 'border-border bg-secondary')
        # No bare text-accent link inside the row.
        self.assertNotContains(
            response,
            'class="text-sm text-accent hover:underline">View plan',
        )

    def test_sprints_list_action_cell_renders_pill_buttons(self):
        from plans.models import Sprint

        Sprint.objects.create(
            name='Sprint B',
            slug='sprint-b',
            start_date=datetime.date(2026, 1, 1),
            duration_weeks=4,
        )
        response = self.client.get('/studio/sprints/')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'border-accent bg-accent')
        self.assertContains(response, 'border-border bg-secondary')
        self.assertNotContains(
            response,
            'class="text-sm text-accent hover:underline">Edit',
        )

    def test_downloads_list_action_cell_preserves_view_on_site_testid(self):
        Download.objects.create(
            title='Pill Download',
            slug='pill-download',
            file_url='https://example.com/pill.pdf',
            published=True,
        )
        response = self.client.get('/studio/downloads/')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'border-accent bg-accent')
        self.assertContains(response, 'data-testid="view-on-site"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    def test_email_templates_list_uses_destructive_pill_for_reset(self):
        from email_app.models import EmailTemplateOverride

        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Welcome',
            body_markdown='Hi there.',
        )
        response = self.client.get('/studio/email-templates/')
        self.assertContains(response, 'studio-action-group')
        # Edit pill (primary)
        self.assertContains(response, 'border-accent bg-accent')
        # Reset to default pill (destructive)
        self.assertContains(response, 'border-red-500/40')
        self.assertContains(response, 'Reset to default')

    def test_crm_list_profile_action_renders_secondary_pill_with_testid(self):
        from crm.models import CRMRecord

        tracked = User.objects.create_user(
            email='crm-tracked@test.com', password='testpass',
        )
        CRMRecord.objects.create(user=tracked, status='active')
        response = self.client.get('/studio/crm/')
        self.assertContains(response, 'studio-action-group')
        self.assertContains(response, 'data-testid="crm-row-profile-link"')
        self.assertContains(response, 'border-border bg-secondary')

    def test_api_tokens_revoke_button_renders_destructive_pill(self):
        from accounts.models import Token

        Token.objects.create(user=self.staff, name='Test token')
        response = self.client.get('/studio/api-tokens/')
        self.assertContains(response, 'data-testid="api-token-revoke"')
        self.assertContains(response, 'border-red-500/40')
