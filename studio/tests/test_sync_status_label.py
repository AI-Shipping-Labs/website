"""Tests for the sync-status label and pill (issue #245).

The DB enum keeps ``partial`` (no migration, stable test contracts) but the
operator-facing surface renders ``Completed with N error(s)``. The pill stays
amber/yellow so it remains distinct from the green ``success`` and red
``failed`` pills. These tests cover:

- The ``sync_status_label`` filter and ``sync_status_pill`` inclusion tag.
- The Studio sync dashboard repo-level pill.
- The Studio sync history batch-level label.
- The legacy ``/admin/sync/`` and ``/admin/sync/<id>/history/`` surfaces.
- That the per-type breakdown row still has an error count attached.
"""

import re
import uuid

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, TestCase
from django.utils import timezone

from integrations.models import ContentSource, SyncLog
from studio.templatetags.studio_filters import (
    _sync_status_label,
    sync_status_label,
)

User = get_user_model()


def _strip(html: str) -> str:
    """Collapse whitespace so assertions can ignore template indentation."""
    return re.sub(r'\s+', ' ', html).strip()


class SyncStatusLabelFilterTest(TestCase):
    """The pure-Python label helper produces the human-readable string."""

    def test_success_label(self):
        self.assertEqual(_sync_status_label('success'), 'success')

    def test_failed_label(self):
        self.assertEqual(_sync_status_label('failed'), 'failed')

    def test_running_label(self):
        self.assertEqual(_sync_status_label('running'), 'running')

    def test_skipped_label(self):
        self.assertEqual(_sync_status_label('skipped'), 'skipped')

    def test_partial_with_zero_errors_falls_back(self):
        # Defensive: status said partial but the count was missing — we still
        # avoid the bare word "partial" which is what the issue is about.
        self.assertEqual(
            _sync_status_label('partial', 0),
            'Completed with errors',
        )

    def test_partial_with_one_error_singular(self):
        self.assertEqual(
            _sync_status_label('partial', 1),
            'Completed with 1 error',
        )

    def test_partial_with_many_errors_plural(self):
        self.assertEqual(
            _sync_status_label('partial', 3),
            'Completed with 3 errors',
        )

    def test_partial_with_string_count_coerces(self):
        # The view sometimes hands us a string (e.g. from JSON); coerce it.
        self.assertEqual(
            _sync_status_label('partial', '2'),
            'Completed with 2 errors',
        )

    def test_filter_form_matches_helper(self):
        self.assertEqual(
            sync_status_label('partial', 5),
            _sync_status_label('partial', 5),
        )


class SyncStatusPillTagTest(TestCase):
    """The inclusion tag renders an amber pill for ``partial`` syncs."""

    def _render(self, status, error_count=0, size='sm'):
        template = Template(
            '{% load studio_filters %}'
            '{% sync_status_pill status err size %}'
        )
        return template.render(Context({
            'status': status,
            'err': error_count,
            'size': size,
        }))

    def test_partial_pill_uses_amber_classes(self):
        html = self._render('partial', 2)
        # Amber/warning treatment — distinct from success and failed.
        self.assertIn('bg-yellow-500/20', html)
        self.assertIn('text-yellow-400', html)
        # And no red/green leakage that would confuse triage.
        self.assertNotIn('bg-green-500', html)
        self.assertNotIn('bg-red-500', html)

    def test_partial_pill_renders_completed_with_n_errors(self):
        html = _strip(self._render('partial', 2))
        self.assertIn('Completed with 2 errors', html)
        # Critically: the bare word "partial" must NOT leak through.
        self.assertNotIn('>partial<', html)
        self.assertNotIn(' partial ', html)

    def test_success_pill_uses_green_classes(self):
        html = self._render('success')
        self.assertIn('bg-green-500/20', html)
        self.assertIn('text-green-400', html)
        self.assertIn('success', _strip(html))

    def test_failed_pill_uses_red_classes(self):
        html = self._render('failed')
        self.assertIn('bg-red-500/20', html)
        self.assertIn('text-red-400', html)

    def test_xs_variant_drops_pill_chrome(self):
        # The compact variant is used inside table cells. It should NOT add
        # the rounded background so it stays compact.
        html = self._render('partial', 4, size='xs')
        self.assertNotIn('rounded-full', html)
        self.assertIn('text-yellow-400', html)
        self.assertIn('Completed with 4 errors', _strip(html))


class SyncDashboardLabelTest(TestCase):
    """The /studio/sync/ page renders the new label and never shows 'partial'."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def _make_partial_source(self, *, errors):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='partial',
            items_created=1,
            items_updated=1,
            errors=errors,
            finished_at=timezone.now(),
        )
        return source

    def test_dashboard_renders_completed_with_n_errors_for_partial(self):
        self._make_partial_source(errors=[
            {'file': 'a.md', 'error': 'parse'},
            {'file': 'b.md', 'error': 'parse'},
        ])
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        # Spec acceptance: 2 errors -> "Completed with 2 errors" visible.
        self.assertIn('Completed with 2 errors', body)

    def test_dashboard_does_not_render_raw_partial_label(self):
        self._make_partial_source(errors=[{'file': 'a.md', 'error': 'parse'}])
        response = self.client.get('/studio/sync/')
        body = _strip(response.content.decode())
        # The bare word "partial" must not appear as a visible label. We
        # check both the pill body and the table cell.
        self.assertNotIn('>partial<', body)
        self.assertNotIn(' partial ', body)

    def test_dashboard_partial_pill_uses_amber_distinct_from_success(self):
        self._make_partial_source(errors=[{'file': 'a.md', 'error': 'parse'}])
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        # Amber treatment present, tied to the new label.
        idx = body.find('Completed with 1 error')
        self.assertGreater(idx, -1, 'new label not rendered')
        # Walk backward to find the pill's opening <span>; verify it carries
        # the amber/yellow class and not the green/red ones.
        snippet = body[max(0, idx - 300):idx + 50]
        self.assertIn('bg-yellow-500/20', snippet)
        self.assertNotIn('bg-green-500', snippet)
        self.assertNotIn('bg-red-500', snippet)

    def test_dashboard_success_pill_still_green(self):
        # Regression: re-using the pill must not break the green path.
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='success',
            last_synced_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        self.assertIn('bg-green-500/20', body)
        self.assertNotIn('Completed with', body)

    def test_dashboard_partial_status_db_enum_unchanged(self):
        """Spec: DB enum stays 'partial' — view layer translates only."""
        source = self._make_partial_source(
            errors=[{'file': 'a.md', 'error': 'parse'}],
        )
        source.refresh_from_db()
        last_log = SyncLog.objects.get(source=source)
        self.assertEqual(source.last_sync_status, 'partial')
        self.assertEqual(last_log.status, 'partial')


class SyncHistoryLabelTest(TestCase):
    """The /studio/sync/history/ page renders the same new label."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_history_renders_completed_with_n_errors_for_partial_batch(self):
        SyncLog.objects.create(
            source=self.source,
            batch_id=uuid.uuid4(),
            status='partial',
            items_created=1,
            errors=[
                {'file': 'a.md', 'error': 'parse'},
                {'file': 'b.md', 'error': 'parse'},
                {'file': 'c.md', 'error': 'parse'},
            ],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/history/')
        body = response.content.decode()
        self.assertIn('Completed with 3 errors', body)
        # The bare batch-status word "partial" must not surface.
        body_compact = _strip(body)
        self.assertNotIn(' partial ', body_compact)


class AdminSyncLabelTest(TestCase):
    """The legacy /admin/sync/ surfaces use the same label."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@admin.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@admin.com', password='testpass')

    def test_admin_dashboard_uses_completed_with_n_errors(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='partial',
            items_created=1,
            errors=[
                {'file': 'a.md', 'error': 'parse'},
                {'file': 'b.md', 'error': 'parse'},
            ],
            finished_at=timezone.now(),
        )
        response = self.client.get('/admin/sync/')
        body = response.content.decode()
        self.assertIn('Completed with 2 errors', body)

    def test_admin_history_uses_completed_with_n_errors_for_log(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='partial',
            errors=[{'file': 'a.md', 'error': 'parse'}],
            finished_at=timezone.now(),
        )
        response = self.client.get(f'/admin/sync/{source.pk}/history/')
        body = response.content.decode()
        # Both the source's last-status header AND the per-log row should
        # use the new label, not the bare "partial".
        self.assertIn('Completed with 1 error', body)


class SyncAggregatorErrorCountTest(TestCase):
    """The aggregator surfaces ``errors_count`` so templates can render the
    label without re-querying SyncLog. Click-through still shows the full
    error list (see ``test_history_shows_errors`` in the existing suite)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@count.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@count.com', password='testpass')

    def test_per_type_row_carries_errors_count(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='partial',
            items_created=1,
            errors=[
                {'file': 'a.md', 'error': 'parse'},
                {'file': 'b.md', 'error': 'parse'},
            ],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        repo = response.context['repos'][0]
        # repo-level count and per-type row both populated
        self.assertEqual(repo['overall_errors_count'], 2)
        self.assertEqual(repo['last_batch']['errors_count'], 2)
        self.assertEqual(
            repo['last_batch']['per_type'][0]['errors_count'], 2,
        )

    def test_partial_log_full_errors_visible_on_dashboard(self):
        """Click-through detail (already inline on the dashboard) keeps showing
        the full per-error list — the new pill is additive, not a replacement
        for the existing error pane."""
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            last_sync_status='partial',
            last_synced_at=timezone.now(),
        )
        SyncLog.objects.create(
            source=source,
            status='partial',
            errors=[
                {'file': 'broken.md', 'error': 'YAML parse error'},
            ],
            finished_at=timezone.now(),
        )
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        self.assertIn('broken.md', body)
        self.assertIn('YAML parse error', body)
