import uuid
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Workshop
from integrations.models import ContentSource, SyncLog
from integrations.services.sync_observability import (
    logical_history_page,
    logical_status,
    source_health,
    structure_errors,
)


class LogicalStatusTest(TestCase):
    def test_lifecycle_precedence_matrix(self):
        cases = [
            (['running', 'failed'], 'running'),
            (['queued', 'success'], 'queued'),
            (['failed', 'partial'], 'failed'),
            (['partial', 'success'], 'partial'),
            (['success', 'skipped'], 'success'),
            (['skipped', 'skipped'], 'skipped'),
        ]
        for statuses, expected in cases:
            with self.subTest(statuses=statuses):
                self.assertEqual(logical_status(statuses), expected)


class SourceHealthTest(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)

    def health(self, fresh_at):
        source = ContentSource(
            last_synced_at=fresh_at,
            last_sync_status='success',
        )
        return source_health(source, now=self.now)

    def test_freshness_boundary_and_never(self):
        self.assertFalse(self.health(self.now - timedelta(days=7))['stale'])
        self.assertTrue(
            self.health(self.now - timedelta(days=7, seconds=1))['stale'],
        )
        never = self.health(None)
        self.assertTrue(never['stale'])
        self.assertIsNone(never['content_age_seconds'])

    def test_failed_attempt_keeps_older_content_freshness(self):
        fresh_at = self.now - timedelta(days=2)
        source = ContentSource(
            last_synced_at=fresh_at,
            last_sync_status='failed',
        )
        health = source_health(source, now=self.now)
        self.assertEqual(health['status'], 'failed')
        self.assertEqual(health['content_fresh_at'], fresh_at)
        self.assertEqual(health['content_age_seconds'], 2 * 86400)

    def test_health_preserves_every_current_status_and_partial_error_counts(self):
        for status in ('queued', 'running', 'failed', 'partial', 'success', 'skipped'):
            with self.subTest(status=status):
                source = ContentSource(last_sync_status=status)
                health = source_health(source, now=self.now)
                self.assertEqual(health['status'], status)

        source = ContentSource(last_sync_status='partial')
        result_log = SyncLog(errors=[
            {'file': 'article.md', 'error': 'Parse failed'},
            {'file': 'article.md', 'error': 'Parse failed'},
        ])
        health = source_health(source, now=self.now, result_log=result_log)
        self.assertEqual(health['errors_total'], 2)
        self.assertEqual(health['errors_unique'], 1)


class StructuredErrorsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Unique article', slug='unique-article', date=date(2026, 1, 1),
        )
        cls.course = Course.objects.create(title='Unique course', slug='unique-course')
        cls.workshop = Workshop.objects.create(
            title='Unique workshop', slug='unique-workshop', date=date(2026, 1, 2),
        )
        Article.objects.create(
            title='Collision article', slug='shared-slug', date=date(2026, 1, 3),
        )
        Course.objects.create(title='Collision course', slug='shared-slug')

    def test_exact_dedupe_order_counts_and_bulk_targets(self):
        errors = [
            {'file': ' posts/unique-article.md ', 'error': ' Parse failed '},
            {'file': 'posts/unique-article.md', 'error': 'Parse failed'},
            {'file': 'other.md', 'error': 'Parse failed'},
            {'file': '', 'error': 'unique-course failed'},
            {'file': '', 'error': 'unique-workshop failed'},
            {'file': '', 'error': 'shared-slug failed'},
            {'file': '', 'error': 'unique-article and unique-course failed'},
        ]
        with self.assertNumQueries(3):
            result = structure_errors(errors, resolve_targets=True)

        self.assertEqual(result['total_count'], 7)
        self.assertEqual(result['unique_count'], 6)
        self.assertEqual(result['items'][0]['count'], 2)
        self.assertEqual(result['items'][0]['target']['type'], 'article')
        self.assertIsNone(result['items'][3 + 1]['target'])  # cross-model collision
        self.assertIsNone(result['items'][-1]['target'])  # two target slugs

    def test_blank_and_long_values_are_preserved_as_normalised_presentation(self):
        message = 'x' * 2000
        result = structure_errors([{'file': ' ', 'error': f' {message} '}])
        self.assertEqual(result['items'][0]['file'], '')
        self.assertEqual(result['items'][0]['message'], message)


class LogicalHistoryQueryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.one = ContentSource.objects.create(repo_name='org/one')
        cls.two = ContentSource.objects.create(repo_name='org/two')

    def test_source_scoping_happens_before_computed_status(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(source=self.one, batch_id=batch_id, status='failed')
        SyncLog.objects.create(source=self.two, batch_id=batch_id, status='running')

        _page, groups = logical_history_page(status='running')
        self.assertEqual(len(groups), 1)
        self.assertEqual(logical_status(groups[0][1]), 'running')

        page, groups = logical_history_page(source=self.one, status='failed')
        self.assertEqual(page.paginator.count, 1)
        self.assertEqual([log.source_id for log in groups[0][1]], [self.one.pk])

    def test_paginates_logical_keys_not_raw_logs(self):
        shared = uuid.uuid4()
        SyncLog.objects.create(source=self.one, batch_id=shared, status='success')
        SyncLog.objects.create(source=self.two, batch_id=shared, status='success')
        for _ in range(50):
            SyncLog.objects.create(source=self.one, status='success')

        first, groups = logical_history_page(page=1, page_size=50)
        second, groups_two = logical_history_page(page=2, page_size=50)
        self.assertEqual(first.paginator.count, 51)
        self.assertEqual(len(groups), 50)
        self.assertEqual(len(groups_two), 1)
        self.assertTrue(second.has_previous())

    def test_page_query_count_is_bounded_independently_of_history_size(self):
        SyncLog.objects.bulk_create([
            SyncLog(source=self.one, status='failed') for _ in range(75)
        ])
        with self.assertNumQueries(3):
            page, groups = logical_history_page(
                source=self.one, status='failed', page=1, page_size=50,
            )
            self.assertEqual(page.paginator.count, 75)
            self.assertEqual(len(groups), 50)

    def test_source_status_and_batch_history_plans_use_observability_indexes(self):
        batch_id = uuid.uuid4()
        SyncLog.objects.create(
            source=self.one, batch_id=batch_id, status='failed',
        )
        source_plan = SyncLog.objects.filter(
            source=self.one, status='failed',
        ).order_by('-started_at').explain()
        batch_plan = SyncLog.objects.filter(
            batch_id=batch_id,
        ).order_by('-started_at').explain()
        self.assertIn('sync_src_status_started_idx', source_plan)
        self.assertIn('sync_batch_started_idx', batch_plan)
