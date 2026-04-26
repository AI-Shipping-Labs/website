"""Round-trip test for the ContentSource consolidation migration (issue #310).

Builds a DB at the pre-0021 schema (with per-(repo, content_type, content_path)
``ContentSource`` rows + ``SyncLog`` rows pointing at each), applies 0021,
and asserts:

- One row per repo post-migration.
- Every ``SyncLog`` still resolves through its FK (no orphans).
- The canonical row carries the most-recent ``last_synced_commit`` from
  the legacy group.
- ``source_path`` on synced content rows is rewritten so the old per-source
  ``content_path`` is folded back in as a prefix.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = ('integrations', '0020_alter_contentsource_unique_together')
POST_MIGRATION = ('integrations', '0021_consolidate_content_source')

# Latest content/events migrations needed so historical Article / Course /
# Workshop / Event models exist when we seed legacy data. We use the
# migration leaves so the historical schema matches the current source_path
# columns the migration rewrites.
CONTENT_LATEST = None  # filled in lazily — see _content_leaf() below.
EVENTS_LATEST = None


def _content_leaf():
    """Return the leaf migration node for the ``content`` app."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    for node in executor.loader.graph.leaf_nodes():
        if node[0] == 'content':
            return node
    raise AssertionError('no content app leaf found')


def _events_leaf():
    """Return the leaf migration node for the ``events`` app."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    for node in executor.loader.graph.leaf_nodes():
        if node[0] == 'events':
            return node
    raise AssertionError('no events app leaf found')


def _migrate_to(*targets):
    """Migrate to the given targets and return the resulting apps registry."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(list(targets))
    return MigrationExecutor(connection).loader.project_state(
        list(targets),
    ).apps


class ContentSourceConsolidationMigrationTest(TransactionTestCase):
    """Issue #310: 0021 consolidates per-type rows into one row per repo."""

    def tearDown(self):
        # Restore the DB to the latest migrations so subsequent tests see
        # the post-consolidation schema.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        targets = executor.loader.graph.leaf_nodes()
        executor.migrate(targets)

    def _seed_legacy(self):
        """Create 7 legacy ContentSource rows + SyncLogs + content rows.

        Layout matches the spec's required scenario:

        - Monorepo ``AI-Shipping-Labs/content`` with 5 type-rows
          (article, course, resource, project, interview_question), each
          with a distinct ``content_path``.
        - ``AI-Shipping-Labs/python-course`` standalone (content_type=course).
        - ``AI-Shipping-Labs/workshops-content`` standalone (content_type=workshop).

        Plus a handful of SyncLog rows per source so we can assert FKs survive.
        """
        apps_pre = _migrate_to(PRE_MIGRATION, _content_leaf(), _events_leaf())

        ContentSource = apps_pre.get_model('integrations', 'ContentSource')
        SyncLog = apps_pre.get_model('integrations', 'SyncLog')
        Article = apps_pre.get_model('content', 'Article')
        Course = apps_pre.get_model('content', 'Course')
        Module = apps_pre.get_model('content', 'Module')
        Project = apps_pre.get_model('content', 'Project')
        Workshop = apps_pre.get_model('content', 'Workshop')

        # --- 5 monorepo rows -----------------------------------------------
        monorepo = 'AI-Shipping-Labs/content'
        article_src = ContentSource.objects.create(
            repo_name=monorepo,
            content_type='article',
            content_path='blog',
            last_synced_at=datetime(2026, 4, 1, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
        )
        course_src_mono = ContentSource.objects.create(
            repo_name=monorepo,
            content_type='course',
            content_path='courses',
            last_synced_at=datetime(2026, 4, 10, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
        )
        resource_src = ContentSource.objects.create(
            repo_name=monorepo,
            content_type='resource',
            content_path='resources',
            last_synced_at=datetime(2026, 4, 5, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='cccc3333cccc3333cccc3333cccc3333cccc3333',
        )
        # Most-recent successful sync within the monorepo group: project.
        project_src = ContentSource.objects.create(
            repo_name=monorepo,
            content_type='project',
            content_path='projects',
            last_synced_at=datetime(2026, 4, 15, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='dddd4444dddd4444dddd4444dddd4444dddd4444',
        )
        interview_src = ContentSource.objects.create(
            repo_name=monorepo,
            content_type='interview_question',
            content_path='interview',
            last_synced_at=datetime(2026, 4, 12, tzinfo=dt_timezone.utc),
            last_sync_status='failed',  # not eligible as canonical
            last_synced_commit='eeee5555eeee5555eeee5555eeee5555eeee5555',
        )

        # --- Standalone python-course repo ---------------------------------
        py_course_repo = 'AI-Shipping-Labs/python-course'
        py_course_src = ContentSource.objects.create(
            repo_name=py_course_repo,
            content_type='course',
            content_path='',
            last_synced_at=datetime(2026, 4, 20, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='ffff6666ffff6666ffff6666ffff6666ffff6666',
        )

        # --- Standalone workshops repo -------------------------------------
        workshops_repo = 'AI-Shipping-Labs/workshops-content'
        workshops_src = ContentSource.objects.create(
            repo_name=workshops_repo,
            content_type='workshop',
            content_path='',
            last_synced_at=datetime(2026, 4, 22, tzinfo=dt_timezone.utc),
            last_sync_status='success',
            last_synced_commit='99996666999966669999666699996666aaaaaaaa',
        )

        # --- SyncLogs across the 7 sources ---------------------------------
        sync_log_ids = []
        for src in [
            article_src, course_src_mono, resource_src, project_src,
            interview_src, py_course_src, workshops_src,
        ]:
            for status in ('success', 'failed'):
                log = SyncLog.objects.create(
                    source=src,
                    status=status,
                    items_created=1,
                    items_updated=0,
                    items_deleted=0,
                )
                sync_log_ids.append(log.pk)

        # --- Content rows that exercise source_path rewriting --------------
        # Article from the blog/ subdir of the monorepo. source_path stored
        # WITHOUT the blog/ prefix (legacy behaviour).
        Article.objects.create(
            title='Hello', slug='hello',
            content_markdown='# Hello', author='Test',
            date='2026-04-01',
            source_repo=monorepo,
            source_path='hello.md',
        )
        # Course + Module from courses/ subdir of the monorepo.
        course = Course.objects.create(
            title='ML', slug='ml', status='published',
            instructor_name='Alexey',
            source_repo=monorepo,
            source_path='ml/course.yaml',
        )
        Module.objects.create(
            course=course,
            slug='intro', title='Intro', sort_order=1,
            source_repo=monorepo,
            source_path='ml/01-intro/module.yaml',
        )
        # Project from projects/ subdir of the monorepo.
        Project.objects.create(
            title='Demo', slug='demo', content_markdown='# Demo',
            date='2026-04-01',
            source_repo=monorepo,
            source_path='demo.yaml',
        )
        # Workshop in standalone workshops repo (content_path empty -> no rewrite).
        Workshop.objects.create(
            slug='ws-1', title='WS', date='2026-04-21',
            instructor_name='Alexey',
            source_repo=workshops_repo,
            source_path='2026/ws-1/workshop.yaml',
        )

        return {
            'sync_log_ids': sync_log_ids,
            'monorepo': monorepo,
            'py_course_repo': py_course_repo,
            'workshops_repo': workshops_repo,
        }

    def test_consolidation_round_trip(self):
        seeded = self._seed_legacy()

        # Sanity-check the pre-state.
        apps_pre = _migrate_to(PRE_MIGRATION, _content_leaf(), _events_leaf())
        ContentSource = apps_pre.get_model('integrations', 'ContentSource')
        SyncLog = apps_pre.get_model('integrations', 'SyncLog')
        self.assertEqual(ContentSource.objects.count(), 7)
        self.assertEqual(
            SyncLog.objects.count(),
            len(seeded['sync_log_ids']),
        )

        # Apply 0021. Pass the content/events leaves as well so the post-
        # state apps registry resolves Article/Course/Module/Project/Workshop.
        apps_post = _migrate_to(
            POST_MIGRATION, _content_leaf(), _events_leaf(),
        )
        ContentSource = apps_post.get_model('integrations', 'ContentSource')
        SyncLog = apps_post.get_model('integrations', 'SyncLog')
        Article = apps_post.get_model('content', 'Article')
        Course = apps_post.get_model('content', 'Course')
        Module = apps_post.get_model('content', 'Module')
        Project = apps_post.get_model('content', 'Project')
        Workshop = apps_post.get_model('content', 'Workshop')

        # 1. Three rows survive — one per repo.
        post_repos = sorted(
            ContentSource.objects.values_list('repo_name', flat=True),
        )
        self.assertEqual(post_repos, [
            seeded['monorepo'],
            seeded['py_course_repo'],
            seeded['workshops_repo'],
        ])

        # 2. Every original SyncLog still resolves through its FK (i.e. has
        #    a valid ``source_id`` pointing at a surviving ContentSource).
        post_log_ids = set(SyncLog.objects.values_list('pk', flat=True))
        self.assertEqual(post_log_ids, set(seeded['sync_log_ids']))
        valid_source_ids = set(
            ContentSource.objects.values_list('pk', flat=True),
        )
        for log in SyncLog.objects.all():
            self.assertIn(
                log.source_id,
                valid_source_ids,
                f'SyncLog {log.pk} points at missing ContentSource '
                f'{log.source_id}',
            )

        # 3. Canonical monorepo row carries the most-recent successful
        #    ``last_synced_commit`` from the legacy group (project_src,
        #    synced 2026-04-15).
        monorepo_row = ContentSource.objects.get(repo_name=seeded['monorepo'])
        self.assertEqual(
            monorepo_row.last_synced_commit,
            'dddd4444dddd4444dddd4444dddd4444dddd4444',
        )
        self.assertEqual(monorepo_row.last_sync_status, 'success')

        # Every monorepo SyncLog now points at the canonical row (5 sources
        # x 2 statuses = 10 logs).
        monorepo_logs = SyncLog.objects.filter(source_id=monorepo_row.pk)
        self.assertEqual(monorepo_logs.count(), 10)

        # 4. source_path rewriting: legacy rows that lived under a
        #    content_path subdir now carry the prefix.
        article = Article.objects.get(slug='hello')
        self.assertEqual(article.source_path, 'blog/hello.md')

        course = Course.objects.get(slug='ml')
        self.assertEqual(course.source_path, 'courses/ml/course.yaml')

        module = Module.objects.get(slug='intro')
        self.assertEqual(
            module.source_path,
            'courses/ml/01-intro/module.yaml',
        )

        project = Project.objects.get(slug='demo')
        self.assertEqual(project.source_path, 'projects/demo.yaml')

        # Workshops repo had empty content_path so no rewrite expected.
        workshop = Workshop.objects.get(slug='ws-1')
        self.assertEqual(
            workshop.source_path,
            '2026/ws-1/workshop.yaml',
        )

    def test_no_orphan_sync_logs(self):
        """Stronger assertion: every legacy SyncLog survives and resolves."""
        seeded = self._seed_legacy()

        apps_post = _migrate_to(
            POST_MIGRATION, _content_leaf(), _events_leaf(),
        )
        ContentSource = apps_post.get_model('integrations', 'ContentSource')
        SyncLog = apps_post.get_model('integrations', 'SyncLog')

        live_source_ids = set(
            ContentSource.objects.values_list('pk', flat=True),
        )
        log_count = 0
        for log in SyncLog.objects.all():
            log_count += 1
            self.assertIn(log.source_id, live_source_ids)
        # No SyncLog rows should have been dropped.
        self.assertEqual(log_count, len(seeded['sync_log_ids']))
