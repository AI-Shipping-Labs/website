"""Tests for Studio content sync dashboard views."""

import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from integrations.models import ContentSource, SyncLog

User = get_user_model()


class StudioSyncDashboardTest(TestCase):
    """Test the unified sync dashboard view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_returns_200(self):
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        response = self.client.get('/studio/sync/')
        self.assertTemplateUsed(response, 'studio/sync/dashboard.html')

    def test_dashboard_shows_repo_name(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'AI-Shipping-Labs/content')

    def test_dashboard_groups_sources_by_repo(self):
        """Multiple content types from same repo appear as one card."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.get('/studio/sync/')
        # Context should have exactly one repo entry
        self.assertEqual(len(response.context['repos']), 1)
        self.assertEqual(len(response.context['repos'][0]['sources']), 2)

    def test_dashboard_shows_content_type_count(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '2 content types')

    def test_dashboard_shows_sync_status(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'success')

    def test_dashboard_shows_never_synced(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Never synced')

    def test_dashboard_empty_state(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'No content sources configured')

    def test_dashboard_has_sync_all_button(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Sync All')

    def test_dashboard_per_repo_button_posts_to_repo_trigger(self):
        """The per-repo button must post to ``studio_sync_repo_trigger`` with
        the repo name, not to ``studio_sync_all`` (issue #232).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(
            response,
            'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"',
        )
        # Button label is the generic ``Sync now`` (not per-content-type).
        self.assertContains(response, 'Sync now')

    def test_dashboard_per_repo_button_does_not_post_to_sync_all(self):
        """Regression test for #232: per-repo button must not point to
        ``/studio/sync/all/`` (which would trigger every repo, not one).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        # Exactly one form on the page should post to /studio/sync/all/ —
        # the top-level Sync All button. The per-repo card must use the
        # new repo-trigger URL.
        self.assertEqual(html.count('action="/studio/sync/all/"'), 1)
        self.assertIn(
            'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"',
            html,
        )

    def test_dashboard_renders_one_button_per_repo(self):
        """A repo with N content sources renders ONE ``Sync now`` button
        (the fan-out happens server-side, see issue #232).
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        # Two per-repo trigger forms (Sync now + Force resync) for the
        # single repo card. Issue #235 added the Force resync form which
        # POSTs ``force=1`` to the same URL.
        self.assertEqual(
            html.count(
                'action="/studio/sync/AI-Shipping-Labs/content/trigger-repo/"'
            ),
            2,
        )

    def test_dashboard_has_history_link(self):
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '/studio/sync/history/')

    def test_dashboard_shows_last_batch_results(self):
        """Dashboard shows per-content-type breakdown from latest sync."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=source,
            batch_id=batch_id,
            status='success',
            items_created=3,
            items_updated=2,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '+3 created')
        self.assertContains(response, '2 updated')

    def test_dashboard_shows_tiers_synced(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            tiers_synced=True,
            tiers_count=3,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'Tiers')
        self.assertContains(response, '3 tiers')

    def test_dashboard_does_not_leak_other_repos_logs_via_batch_id(self):
        """A Sync All batch shares one batch_id across repos. Each card must
        only show its own repo's per-type rows, not the other repo's.
        """
        batch_id = uuid.uuid4()

        course_src = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=course_src,
            batch_id=batch_id,
            status='success',
            items_updated=10,
            finished_at=timezone.now(),
        )

        content_project_src = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=content_project_src,
            batch_id=batch_id,
            status='success',
            items_updated=10,
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        repos = {repo['repo_name']: repo for repo in response.context['repos']}

        course_card = repos['AI-Shipping-Labs/python-course']
        course_types = [row['content_type'] for row in course_card['last_batch']['per_type']]
        self.assertEqual(course_types, ['course'])

        content_card = repos['AI-Shipping-Labs/content']
        content_types = [row['content_type'] for row in content_card['last_batch']['per_type']]
        self.assertEqual(content_types, ['project'])

    def test_dashboard_shows_items_detail(self):
        """Changed items are listed with links."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=1,
            items_detail=[{
                'title': 'My New Article',
                'slug': 'my-new-article',
                'action': 'created',
                'content_type': 'article',
            }],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'My New Article')
        self.assertContains(response, '/blog/my-new-article')

    def test_dashboard_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 302)

    def test_dashboard_non_staff_gets_403(self):
        User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )
        client = Client()
        client.login(email='user@test.com', password='testpass')
        response = client.get('/studio/sync/')
        self.assertEqual(response.status_code, 403)


class StudioSyncDashboardUnchangedTest(TestCase):
    """Issue #252 - dashboard surfaces ``items_unchanged`` per source/batch.

    The aggregator already exposes ``per_type[*]['unchanged']`` and
    ``total_unchanged`` (issue #225); these tests pin the template to render
    them next to created/updated/deleted with muted styling.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_per_type_row_renders_unchanged_count(self):
        """The per-content-type table row shows the unchanged count cell."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=0,
            items_updated=3,
            items_unchanged=17,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        # The aggregator must expose unchanged so the template can render it.
        repo = response.context['repos'][0]
        self.assertEqual(repo['last_batch']['per_type'][0]['unchanged'], 17)
        self.assertEqual(repo['last_batch']['total_unchanged'], 17)
        # The dashboard renders the column header and the per-type cell value.
        self.assertContains(response, 'Unchanged')
        self.assertContains(
            response,
            '<td class="py-2 pr-2 sm:pr-4 text-right hidden sm:table-cell '
            'text-muted-foreground" data-unchanged>17</td>',
            html=False,
        )

    def test_summary_shows_unchanged_when_nonzero(self):
        """The compact summary above the table includes ``N unchanged``."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_unchanged=42,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, '42 unchanged')
        # Even with only unchanged items, the "No changes" fallback must NOT
        # render — the operator should see the unchanged count instead.
        self.assertNotContains(response, 'No changes')

    def test_summary_hides_unchanged_when_zero(self):
        """First-syncs (no unchanged items) must not render an empty pill."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=5,
            items_unchanged=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        # The summary line must show created but not "0 unchanged" clutter.
        self.assertContains(response, '+5 created')
        self.assertNotContains(response, 'unchanged</span>')

    def test_per_type_unchanged_cell_uses_muted_styling(self):
        """Unchanged counts use muted/secondary color, never green/blue/red."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_unchanged=4,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        html = response.content.decode()
        # Find the unchanged cell and check its class string is muted.
        marker = 'data-unchanged>4</td>'
        idx = html.find(marker)
        self.assertNotEqual(idx, -1, 'unchanged cell with value 4 not found')
        cell_open = html.rfind('<td', 0, idx)
        cell_html = html[cell_open:idx + len(marker)]
        self.assertIn('text-muted-foreground', cell_html)
        self.assertNotIn('text-green-400', cell_html)
        self.assertNotIn('text-blue-400', cell_html)
        self.assertNotIn('text-red-400', cell_html)


class StudioSyncDashboardCourseBreakdownTest(TestCase):
    """Issue #224 - course-type sources show per-level breakdown.

    A course sync touches three different content kinds:
    - course (one or a few)
    - module (several per course)
    - unit (many per module)

    Lumping them all into a single "Course   X created   Y updated" row
    hides what actually changed. The dashboard now renders one row per
    level plus an expandable list of changed pages with links to the
    studio edit pages.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def _build_course_items_detail(self, n_courses=1, n_modules=3, n_units=12):
        """Build a flat items_detail list mixing course/module/unit items.

        Mirrors what the sync writes out for a course-type source after
        issue #224. Uses placeholder PKs (1, 2, ...) so the studio edit
        URL fields exist; we don't need real DB rows for view-layer tests.
        """
        items = []
        for i in range(n_courses):
            items.append({
                'title': f'Course {i + 1}',
                'slug': f'course-{i + 1}',
                'action': 'updated',
                'content_type': 'course',
                'course_id': 100 + i,
                'course_slug': f'course-{i + 1}',
            })
        for i in range(n_modules):
            items.append({
                'title': f'Module {i + 1}',
                'slug': f'module-{i + 1}',
                'action': 'updated',
                'content_type': 'module',
                'course_id': 100,
                'course_slug': 'course-1',
                'module_id': 200 + i,
            })
        for i in range(n_units):
            items.append({
                'title': f'Unit {i + 1}',
                'slug': f'unit-{i + 1}',
                'action': 'updated',
                'content_type': 'unit',
                'course_id': 100,
                'course_slug': 'course-1',
                'module_id': 200,
                'module_slug': 'module-1',
                'unit_id': 300 + i,
            })
        return items

    def test_course_breakdown_present_in_context(self):
        """Course-type per_type entry carries a course_breakdown list."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=0,
            items_updated=16,
            items_detail=self._build_course_items_detail(
                n_courses=1, n_modules=3, n_units=12,
            ),
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        repos = {r['repo_name']: r for r in response.context['repos']}
        course_card = repos['AI-Shipping-Labs/python-course']
        per_type = course_card['last_batch']['per_type']
        self.assertEqual(len(per_type), 1)
        self.assertIn('course_breakdown', per_type[0])

        # The breakdown must list courses, modules, and units in that order.
        breakdown = per_type[0]['course_breakdown']
        self.assertEqual([b['level'] for b in breakdown],
                         ['course', 'module', 'unit'])

    def test_course_breakdown_counts_match_items(self):
        """Per-level counts equal the number of items at each level."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_updated=16,
            items_detail=self._build_course_items_detail(
                n_courses=1, n_modules=3, n_units=12,
            ),
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        repos = {r['repo_name']: r for r in response.context['repos']}
        breakdown = {
            b['level']: b
            for b in repos['AI-Shipping-Labs/python-course']
            ['last_batch']['per_type'][0]['course_breakdown']
        }
        self.assertEqual(breakdown['course']['updated'], 1)
        self.assertEqual(breakdown['module']['updated'], 3)
        self.assertEqual(breakdown['unit']['updated'], 12)

    def test_course_breakdown_separates_created_and_updated(self):
        """Per-level rows distinguish created vs updated counts."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        items_detail = [
            {'title': 'C', 'slug': 'c', 'action': 'updated',
             'content_type': 'course', 'course_id': 1, 'course_slug': 'c'},
            {'title': 'M new', 'slug': 'm-new', 'action': 'created',
             'content_type': 'module', 'course_id': 1, 'module_id': 1,
             'course_slug': 'c'},
            {'title': 'M old', 'slug': 'm-old', 'action': 'updated',
             'content_type': 'module', 'course_id': 1, 'module_id': 2,
             'course_slug': 'c'},
            {'title': 'U new', 'slug': 'u-new', 'action': 'created',
             'content_type': 'unit', 'course_id': 1, 'module_id': 1,
             'unit_id': 1, 'course_slug': 'c', 'module_slug': 'm-new'},
            {'title': 'U old', 'slug': 'u-old', 'action': 'updated',
             'content_type': 'unit', 'course_id': 1, 'module_id': 1,
             'unit_id': 2, 'course_slug': 'c', 'module_slug': 'm-new'},
        ]
        SyncLog.objects.create(
            source=source,
            status='success',
            items_detail=items_detail,
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        repos = {r['repo_name']: r for r in response.context['repos']}
        breakdown = {
            b['level']: b
            for b in repos['AI-Shipping-Labs/python-course']
            ['last_batch']['per_type'][0]['course_breakdown']
        }
        self.assertEqual(breakdown['course']['created'], 0)
        self.assertEqual(breakdown['course']['updated'], 1)
        self.assertEqual(breakdown['module']['created'], 1)
        self.assertEqual(breakdown['module']['updated'], 1)
        self.assertEqual(breakdown['unit']['created'], 1)
        self.assertEqual(breakdown['unit']['updated'], 1)

    def test_course_breakdown_renders_level_labels(self):
        """Dashboard HTML shows the per-level row labels for course sources."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_updated=16,
            items_detail=self._build_course_items_detail(
                n_courses=1, n_modules=3, n_units=12,
            ),
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        self.assertIn('Courses', body)
        self.assertIn('Modules', body)
        self.assertIn('Lessons (units)', body)

    def test_course_breakdown_lists_changed_unit_titles(self):
        """The expandable list includes every changed unit title."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_updated=16,
            items_detail=self._build_course_items_detail(
                n_courses=1, n_modules=3, n_units=12,
            ),
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        for i in range(1, 13):
            self.assertContains(response, f'Unit {i}')

    def test_course_breakdown_links_to_studio_edit_pages(self):
        """Each item row links to its studio edit page (not the public site)."""
        from content.models import Course, Module, Unit
        course = Course.objects.create(
            title='C', slug='c-slug',
            source_repo='AI-Shipping-Labs/python-course',
            status='published',
        )
        module = Module.objects.create(course=course, title='M', slug='m')
        unit = Unit.objects.create(module=module, title='U', slug='u')
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_updated=3,
            items_detail=[
                {'title': 'C', 'slug': 'c-slug', 'action': 'updated',
                 'content_type': 'course', 'course_id': course.pk,
                 'course_slug': 'c-slug'},
                {'title': 'M', 'slug': 'm', 'action': 'updated',
                 'content_type': 'module', 'course_id': course.pk,
                 'course_slug': 'c-slug', 'module_id': module.pk},
                {'title': 'U', 'slug': 'u', 'action': 'updated',
                 'content_type': 'unit', 'course_id': course.pk,
                 'course_slug': 'c-slug', 'module_id': module.pk,
                 'module_slug': 'm', 'unit_id': unit.pk},
            ],
            finished_at=timezone.now(),
        )

        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        # Course and module rows both link to the course edit page (modules
        # are managed inline within the course form).
        self.assertIn(f'/studio/courses/{course.pk}/edit', body)
        # Unit rows link to the unit edit page.
        self.assertIn(f'/studio/units/{unit.pk}/edit', body)
        # No public-site links for these — operators want the edit page.
        self.assertNotIn('/courses/c-slug', body)

    def test_non_course_types_unchanged(self):
        """Article/project/etc. rows still render with the original layout."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_created=1,
            items_detail=[{
                'title': 'Hello',
                'slug': 'hello',
                'action': 'created',
                'content_type': 'article',
            }],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        per_type = response.context['repos'][0]['last_batch']['per_type']
        # Non-course rows do not get a course_breakdown.
        self.assertNotIn('course_breakdown', per_type[0])
        # Article row still uses public /blog/<slug> link (unchanged behavior).
        self.assertContains(response, '/blog/hello')


class StudioSyncDashboardFragmentTest(TestCase):
    """Test the ?fragment=status auto-refresh endpoint (issue #243).

    The dashboard polls itself every ~3s while at least one source is in
    'running' state and swaps the per-repo cards in place. The fragment
    endpoint must return just the cards section, not the full chrome, so
    the payload stays small.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_fragment_returns_200(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertEqual(response.status_code, 200)

    def test_fragment_uses_partial_template(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertTemplateUsed(response, 'studio/sync/_repos_section.html')

    def test_fragment_does_not_render_full_chrome(self):
        """Fragment must not include the page header / Sync All button —
        otherwise the swap would inject duplicates of the chrome.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        body = response.content.decode()
        # Page chrome must NOT be in the fragment.
        self.assertNotIn('id="sync-all-form"', body)
        self.assertNotIn('id="sync-live-indicator"', body)
        # Cards section MUST be there.
        self.assertIn('id="sync-repos-section"', body)

    def test_fragment_includes_repo_cards(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'data-repo-card')

    def test_fragment_marks_any_running_true_when_a_source_is_running(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'data-any-running="true"')

    def test_fragment_marks_any_running_false_when_nothing_running(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'data-any-running="false"')

    def test_fragment_card_carries_status_dataset_for_poller(self):
        """The poller checks each card's data-status to decide whether
        anything is still running — the attribute must be present and
        reflect the source's current status.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'data-status="running"')

    def test_fragment_card_status_flips_after_worker_finishes(self):
        """End-to-end aggregation check: when the underlying source row's
        ``last_sync_status`` flips from 'running' to 'success', a fresh
        fragment fetch reflects that — proving the poller will see the
        update without a full page reload.
        """
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )
        # First fetch: still running.
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'data-any-running="true"')
        self.assertContains(response, 'data-status="running"')

        # Worker finishes — write the final status to the source row.
        source.last_sync_status = 'success'
        source.last_synced_at = timezone.now()
        source.save()

        # Second fetch: poller now sees the row as done.
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, 'data-any-running="false"')
        self.assertContains(response, 'data-status="success"')

    def test_fragment_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/?fragment=status')
        self.assertEqual(response.status_code, 302)

    def test_fragment_includes_course_breakdown(self):
        """Fragment endpoint reflects the new course-level breakdown so
        the dashboard's auto-refresh poller (issue #243) doesn't fall back
        to the rolled-up "Course X created Y updated" row when a course
        sync finishes mid-poll. See issue #224.
        """
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='success',
            items_updated=5,
            items_detail=[
                {'title': 'C', 'slug': 'c', 'action': 'updated',
                 'content_type': 'course', 'course_id': 1,
                 'course_slug': 'c'},
                {'title': 'M', 'slug': 'm', 'action': 'updated',
                 'content_type': 'module', 'course_id': 1,
                 'course_slug': 'c', 'module_id': 1},
                {'title': 'Lesson Alpha', 'slug': 'lesson-a',
                 'action': 'updated', 'content_type': 'unit',
                 'course_id': 1, 'course_slug': 'c',
                 'module_id': 1, 'module_slug': 'm', 'unit_id': 1},
            ],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/?fragment=status')
        # Per-level rows appear in the fragment.
        self.assertContains(response, 'Lessons (units)')
        # Changed unit titles appear so the operator sees them after the poll.
        self.assertContains(response, 'Lesson Alpha')


class StudioSyncDashboardLiveIndicatorTest(TestCase):
    """The Live indicator + poller wiring on the full dashboard (issue #243)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_dashboard_renders_live_indicator_element(self):
        """The Live indicator is always present in the DOM; the poller
        toggles its visibility. Test the markup is there.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'id="sync-live-indicator"')
        self.assertContains(response, '>Live<')

    def test_dashboard_wraps_repos_in_polling_wrapper(self):
        """The poller mounts on ``#sync-repos-wrapper`` and uses its
        ``data-fragment-url`` attribute to know what to fetch. The wrapper
        must point at the ?fragment=status endpoint.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, 'id="sync-repos-wrapper"')
        self.assertContains(
            response, 'data-fragment-url="/studio/sync/?fragment=status"',
        )


class StudioSyncHistoryTest(TestCase):
    """Test the aggregated sync history view."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog/',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_history_returns_200(self):
        response = self.client.get('/studio/sync/history/')
        self.assertEqual(response.status_code, 200)

    def test_history_uses_correct_template(self):
        response = self.client.get('/studio/sync/history/')
        self.assertTemplateUsed(response, 'studio/sync/history.html')

    def test_history_shows_batch_with_counts(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=self.source,
            batch_id=batch_id,
            status='success',
            items_created=5,
            items_updated=2,
            items_deleted=0,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'success')
        self.assertContains(response, '+5 created')
        self.assertContains(response, '2 updated')

    def test_history_shows_errors(self):
        SyncLog.objects.create(
            source=self.source,
            status='partial',
            errors=[{'file': 'test.md', 'error': 'parse error'}],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'test.md')
        self.assertContains(response, 'parse error')

    def test_history_empty_state(self):
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'No sync history yet')

    def test_history_requires_staff(self):
        client = Client()
        response = client.get('/studio/sync/history/')
        self.assertEqual(response.status_code, 302)

    def test_history_has_back_link(self):
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Back to Content Sync')

    def test_history_aggregates_batch(self):
        """Logs with same batch_id are aggregated into one entry."""
        batch_id = uuid.uuid4()
        source2 = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        SyncLog.objects.create(
            source=self.source,
            batch_id=batch_id,
            status='success',
            items_created=3,
            finished_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source2,
            batch_id=batch_id,
            status='success',
            items_created=1,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        # Should show aggregated count and source count
        self.assertContains(response, '2 sources')

    def test_history_shows_tiers_synced(self):
        SyncLog.objects.create(
            source=self.source,
            status='success',
            tiers_synced=True,
            tiers_count=4,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, 'tiers synced')

    def test_history_shows_unchanged_in_batch_summary(self):
        """Issue #252 - history batch summary surfaces ``total_unchanged``."""
        SyncLog.objects.create(
            source=self.source,
            status='success',
            items_created=1,
            items_unchanged=9,
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        self.assertContains(response, '9 unchanged')
        # Per-type table also gets the unchanged column.
        self.assertContains(response, 'Unchanged')


class StudioSyncTriggerTest(TestCase):
    """Test the sync trigger endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_trigger_redirects_to_sync_dashboard(self, mock_async):
        """After enqueuing, the user stays on /studio/sync/ — being yanked
        to the worker page interrupted operator flow (see issue #239)."""
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_trigger_calls_sync(self, mock_async):
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        mock_async.assert_called_once()
        args = mock_async.call_args
        self.assertEqual(args[0][0], 'integrations.services.github.sync_content_source')

    def test_trigger_requires_post(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 405)

    def test_trigger_requires_staff(self):
        client = Client()
        response = client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_trigger_nonexistent_source_returns_404(self):
        fake_id = uuid.uuid4()
        response = self.client.post(f'/studio/sync/{fake_id}/trigger/')
        self.assertEqual(response.status_code, 404)

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_trigger_handles_sync_error(self, mock_async):
        response = self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 302)

    @patch('django_q.tasks.async_task')
    def test_trigger_only_syncs_targeted_source(self, mock_async):
        """Regression for #232: posting to /studio/sync/<id>/trigger/ must
        sync only that source, not every configured source.
        """
        # Create additional sources to make sure they're NOT triggered.
        other = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post(f'/studio/sync/{self.source.pk}/trigger/')
        # Exactly one async_task call (vs three if it had hit sync_all).
        mock_async.assert_called_once()
        # The single call must be for the targeted source, not the others.
        synced_source = mock_async.call_args[0][1]
        self.assertEqual(synced_source.pk, self.source.pk)
        self.assertNotEqual(synced_source.pk, other.pk)


class StudioSyncRepoTriggerTest(TestCase):
    """Test the per-repo fan-out trigger endpoint (issue #232)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_fans_out_to_all_sources(self, mock_async):
        """Posting to /studio/sync/<repo_name>/trigger-repo/ enqueues one
        task per ContentSource sharing that repo_name.
        """
        article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        project = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_async.call_count, 3)
        # Every call must target a ContentSource from the targeted repo.
        synced_pks = {call.args[1].pk for call in mock_async.call_args_list}
        self.assertEqual(synced_pks, {article.pk, course.pk, project.pk})

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_creates_one_batch_id(self, mock_async):
        """All fan-out calls share one batch_id so the batch shows up as a
        single row in history and the dashboard aggregator finds them.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        batch_ids = [
            call.kwargs.get('batch_id')
            for call in mock_async.call_args_list
        ]
        self.assertEqual(len(batch_ids), 3)
        self.assertIsNotNone(batch_ids[0])
        self.assertEqual(len(set(batch_ids)), 1)

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_doesnt_touch_other_repos(self, mock_async):
        """Posting for one repo must NOT enqueue tasks for any other repo's
        sources (regression for the bug where the per-row button hit
        /studio/sync/all/).
        """
        target = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        other_repo_course = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            content_path='units/',
        )
        other_repo_article = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='article',
            content_path='posts/',
        )
        self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        synced_pks = {call.args[1].pk for call in mock_async.call_args_list}
        self.assertEqual(synced_pks, {target.pk})
        self.assertNotIn(other_repo_course.pk, synced_pks)
        self.assertNotIn(other_repo_article.pk, synced_pks)

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_redirects_to_sync_dashboard(self, mock_async):
        """Per #239, sync actions stay on /studio/sync/ rather than yanking
        the operator to /studio/worker/.
        """
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_repo_trigger_flash_names_the_repo(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/',
            follow=True,
        )
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, '2 sources')
        # Flash message includes a link back to the worker page so operators
        # can still watch the queue (consistent with sync_trigger / sync_all).
        self.assertContains(response, '/studio/worker/')

    def test_repo_trigger_unknown_repo_redirects_with_error(self):
        response = self.client.post(
            '/studio/sync/AI-Shipping-Labs/no-such-repo/trigger-repo/',
            follow=True,
        )
        self.assertContains(response, 'No content sources configured')

    def test_repo_trigger_requires_post(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        response = self.client.get(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 405)

    def test_repo_trigger_requires_staff(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        client = Client()
        response = client.post(
            '/studio/sync/AI-Shipping-Labs/content/trigger-repo/'
        )
        self.assertEqual(response.status_code, 302)


class StudioSyncAllTest(TestCase):
    """Test the sync all endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_sync_all_redirects_to_sync_dashboard(self, mock_async):
        """Sync All redirects back to the sync dashboard so the operator
        can watch every per-source row update in place (see issue #239)."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_sync_all_triggers_all_sources(self, mock_async):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects/',
        )
        self.client.post('/studio/sync/all/')
        self.assertEqual(mock_async.call_count, 2)

    @patch('django_q.tasks.async_task')
    def test_sync_all_passes_batch_id(self, mock_async):
        """Sync All passes a shared batch_id to all source syncs."""
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        self.client.post('/studio/sync/all/')
        # Both calls should have the same batch_id kwarg
        batch_ids = [call.kwargs.get('batch_id') for call in mock_async.call_args_list]
        self.assertEqual(len(batch_ids), 2)
        self.assertIsNotNone(batch_ids[0])
        self.assertEqual(batch_ids[0], batch_ids[1])

    def test_sync_all_requires_post(self):
        response = self.client.get('/studio/sync/all/')
        self.assertEqual(response.status_code, 405)

    def test_sync_all_requires_staff(self):
        client = Client()
        response = client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)

    @patch('django_q.tasks.async_task')
    def test_sync_all_with_no_sources(self, mock_async):
        response = self.client.post('/studio/sync/all/')
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


class StudioSyncStatusTest(TestCase):
    """Test the JSON status polling endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_status_returns_json(self):
        response = self.client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = json.loads(response.content)
        self.assertEqual(data['id'], str(self.source.pk))
        self.assertEqual(data['last_sync_status'], 'success')
        self.assertIsNotNone(data['last_synced_at'])

    def test_status_returns_null_for_never_synced(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
        )
        response = self.client.get(f'/studio/sync/{source.pk}/status/')
        data = json.loads(response.content)
        self.assertIsNone(data['last_sync_status'])
        self.assertIsNone(data['last_synced_at'])

    def test_status_requires_staff(self):
        client = Client()
        response = client.get(f'/studio/sync/{self.source.pk}/status/')
        self.assertEqual(response.status_code, 302)

    def test_status_nonexistent_returns_404(self):
        fake_id = uuid.uuid4()
        response = self.client.get(f'/studio/sync/{fake_id}/status/')
        self.assertEqual(response.status_code, 404)


class StudioSidebarSyncLinkTest(TestCase):
    """Test that the Content Sync link appears in the Studio sidebar."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_has_content_sync_link(self):
        response = self.client.get('/studio/')
        self.assertContains(response, '/studio/sync/')
        self.assertContains(response, 'Content Sync')


class SyncLogModelTest(TestCase):
    """Test the SyncLog model new fields."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
        )

    def test_batch_id_groups_logs(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=self.source, batch_id=batch_id, status='success',
        )
        source2 = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses/',
        )
        SyncLog.objects.create(
            source=source2, batch_id=batch_id, status='success',
        )
        batch_logs = SyncLog.objects.filter(batch_id=batch_id)
        self.assertEqual(batch_logs.count(), 2)

    def test_items_detail_stores_json(self):
        detail = [
            {'title': 'Test', 'slug': 'test', 'action': 'created', 'content_type': 'article'},
        ]
        log = SyncLog.objects.create(
            source=self.source, status='success', items_detail=detail,
        )
        log.refresh_from_db()
        self.assertEqual(len(log.items_detail), 1)
        self.assertEqual(log.items_detail[0]['title'], 'Test')

    def test_tiers_synced_field(self):
        log = SyncLog.objects.create(
            source=self.source, status='success',
            tiers_synced=True, tiers_count=3,
        )
        log.refresh_from_db()
        self.assertTrue(log.tiers_synced)
        self.assertEqual(log.tiers_count, 3)


class StudioSyncDashboardSeeInWorkersLinkTest(TestCase):
    """Issue #278: when a repo card is in flight (queued or running) the
    operator gets a one-click "See in workers" link next to the status pill,
    so they can jump to the worker queue/job page without manual navigation.

    Hidden once the repo settles into a terminal state, so it doesn't add
    noise after the run is done.
    """

    LINK_MARKER = 'data-see-in-workers'

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_link_visible_when_running(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, self.LINK_MARKER)
        self.assertContains(response, 'See in workers')

    def test_link_visible_when_queued(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )
        response = self.client.get('/studio/sync/')
        self.assertContains(response, self.LINK_MARKER)
        self.assertContains(response, 'See in workers')

    def test_link_points_to_worker_dashboard(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='running',
        )
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        idx = body.find(self.LINK_MARKER)
        self.assertGreater(idx, -1)
        # The href on the same anchor as the marker must target the worker
        # dashboard. Slice back to the opening <a so we only inspect this
        # tag, not random hrefs elsewhere on the page.
        anchor_open = body.rfind('<a', 0, idx)
        self.assertGreater(anchor_open, -1)
        anchor_html = body[anchor_open:idx]
        self.assertIn('href="/studio/worker/"', anchor_html)

    def test_link_hidden_when_success(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertNotContains(response, self.LINK_MARKER)

    def test_link_hidden_when_failed(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='failed',
            last_synced_at=timezone.now(),
        )
        # Failed needs an error log so the watchdog/aggregator treats it as
        # a real failure; otherwise overall_status stays None.
        SyncLog.objects.create(
            source=ContentSource.objects.get(),
            status='failed',
            errors=[{'file': 'x.md', 'error': 'boom'}],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertNotContains(response, self.LINK_MARKER)

    def test_link_hidden_when_partial(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertNotContains(response, self.LINK_MARKER)

    def test_link_hidden_when_skipped(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='skipped',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        self.assertNotContains(response, self.LINK_MARKER)

    def test_link_appears_then_disappears_across_polling_refresh(self):
        """End-to-end: poll fragment shows the link while queued/running and
        drops it once the source settles to a terminal state. Mirrors what
        the dashboard's auto-refresh poller (#243) sees as the worker
        progresses queued -> running -> success.
        """
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            last_sync_status='queued',
        )
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, self.LINK_MARKER)

        source.last_sync_status = 'running'
        source.save(update_fields=['last_sync_status', 'updated_at'])
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertContains(response, self.LINK_MARKER)

        source.last_sync_status = 'success'
        source.last_synced_at = timezone.now()
        source.save()
        response = self.client.get('/studio/sync/?fragment=status')
        self.assertNotContains(response, self.LINK_MARKER)
