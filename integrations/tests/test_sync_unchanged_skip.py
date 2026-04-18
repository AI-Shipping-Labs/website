"""Tests for issue #225 - sync only marks items as 'updated' when changed.

Includes both end-to-end tests (one per content type) and direct unit
tests for the ``_defaults_differ`` comparison helper, since it is the
hot-path that decides whether each row is re-saved.

Before this issue the sync called ``update_or_create`` unconditionally on
every existing row, so a re-sync of an unchanged repo would report every
item as 'updated' and add every item to ``items_detail``. The dashboard
treats ``items_detail`` as an audit log of what changed, so spurious
entries made it useless.

These tests cover every per-content-type sync helper:

- articles, projects, interview questions
- courses (course + module + unit, including README-as-unit)
- curated links, downloads
- events

For each helper we assert:

1. A second sync of identical content produces 0 created, 0 updated,
   N unchanged, and an empty ``items_detail``.
2. Touching one source file marks only that one as 'updated'; the rest
   stay quiet.
"""

import os
import shutil
import tempfile
import uuid
from datetime import date

from django.test import TestCase

from content.models import Article, CuratedLink, Unit
from events.models import Event
from integrations.models import ContentSource
from integrations.services.github import _defaults_differ, sync_content_source

# ---------------------------------------------------------------------------
# Helper for writing markdown frontmatter files in tests.
# ---------------------------------------------------------------------------


def _write_md(filepath, frontmatter_dict, body=''):
    """Write a markdown file with frontmatter; same shape used elsewhere."""
    if 'content_id' not in frontmatter_dict:
        frontmatter_dict['content_id'] = str(uuid.uuid4())
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = ['---']
    for key, value in frontmatter_dict.items():
        if isinstance(value, list):
            lines.append(f'{key}:')
            for item in value:
                lines.append(f'  - "{item}"')
        elif isinstance(value, bool):
            lines.append(f'{key}: {str(value).lower()}')
        elif isinstance(value, int):
            lines.append(f'{key}: {value}')
        else:
            lines.append(f'{key}: "{value}"')
    lines.append('---')
    lines.append(body)
    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))


def _write_yaml(filepath, data):
    """Write a YAML file (manually so we don't require pyyaml dump style)."""
    if 'content_id' not in data:
        data['content_id'] = str(uuid.uuid4())
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    lines = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f'{key}:')
            for item in value:
                lines.append(f'  - "{item}"')
        elif isinstance(value, bool):
            lines.append(f'{key}: {str(value).lower()}')
        elif isinstance(value, int):
            lines.append(f'{key}: {value}')
        else:
            lines.append(f'{key}: "{value}"')
    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))


# ---------------------------------------------------------------------------
# _defaults_differ helper
# ---------------------------------------------------------------------------


class DefaultsDifferTest(TestCase):
    """Direct tests for the comparison helper.

    The helper drives the no-change-skip path for every content type, so
    edge cases here directly translate into spurious 'updated' counts in
    the dashboard if they regress.
    """

    def test_returns_false_when_all_fields_equal(self):
        a = Article(title='X', slug='x', date=date(2026, 1, 1))
        defaults = {'title': 'X', 'date': date(2026, 1, 1)}
        self.assertFalse(_defaults_differ(a, defaults))

    def test_returns_true_when_field_differs(self):
        a = Article(title='X', slug='x', date=date(2026, 1, 1))
        defaults = {'title': 'Y'}
        self.assertTrue(_defaults_differ(a, defaults))

    def test_ignores_source_commit(self):
        """``source_commit`` bumps every sync; comparing it would defeat
        the whole point of the skip path."""
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            source_commit='old-sha',
        )
        defaults = {'title': 'X', 'source_commit': 'new-sha'}
        self.assertFalse(_defaults_differ(a, defaults))

    def test_ignores_source_repo_and_path(self):
        """Scope keys can't differ for a row we just looked up by them."""
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            source_repo='org/repo', source_path='x.md',
        )
        defaults = {
            'title': 'X',
            'source_repo': 'org/repo',
            'source_path': 'x.md',
        }
        self.assertFalse(_defaults_differ(a, defaults))

    def test_normalizes_tags_before_comparing(self):
        """Models normalize tags on save (lowercase + hyphens). Without
        equivalent normalization here every YAML tag like ``Python`` would
        always look different from the stored ``python``."""
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            tags=['python', 'machine-learning'],
        )
        # Incoming tags are author-cased and contain duplicates.
        defaults = {'tags': ['Python', 'Machine Learning', 'python']}
        self.assertFalse(_defaults_differ(a, defaults))

    def test_normalized_tags_diff_still_detected(self):
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            tags=['python'],
        )
        defaults = {'tags': ['Python', 'Data']}
        self.assertTrue(_defaults_differ(a, defaults))

    def test_uuid_string_equivalent_to_uuid_object(self):
        """``content_id`` is a UUIDField on the model but YAML parses to
        a string. Without coercion every re-sync would look like a diff."""
        cid = uuid.uuid4()
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            content_id=cid,
        )
        defaults = {'content_id': str(cid)}
        self.assertFalse(_defaults_differ(a, defaults))

    def test_different_content_id_still_differs(self):
        a = Article(
            title='X', slug='x', date=date(2026, 1, 1),
            content_id=uuid.uuid4(),
        )
        defaults = {'content_id': str(uuid.uuid4())}
        self.assertTrue(_defaults_differ(a, defaults))


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


class SyncArticlesUnchangedTest(TestCase):
    """Issue #225: re-syncing identical articles is a no-op."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog-225',
            content_type='article',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_articles(self, n=3):
        for i in range(n):
            _write_md(
                os.path.join(self.temp_dir, f'article-{i}.md'),
                {
                    'title': f'Article {i}',
                    'slug': f'article-{i}',
                    'date': '2026-01-15',
                    'description': f'Description for article {i}',
                    'content_id': f'aaaaaaaa-0000-0000-0000-{i:012d}',
                },
                f'Body of article {i}.',
            )

    def test_first_sync_creates_then_second_sync_unchanged(self):
        self._seed_articles(n=3)
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 3)
        self.assertEqual(log1.items_updated, 0)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 3)
        self.assertEqual(log2.items_detail, [])

    def test_changing_one_body_only_marks_that_one_updated(self):
        self._seed_articles(n=3)
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Touch only article-1's body. Other files identical.
        _write_md(
            os.path.join(self.temp_dir, 'article-1.md'),
            {
                'title': 'Article 1',
                'slug': 'article-1',
                'date': '2026-01-15',
                'description': 'Description for article 1',
                'content_id': 'aaaaaaaa-0000-0000-0000-000000000001',
            },
            'Brand new body for article 1.',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 1)
        self.assertEqual(log.items_unchanged, 2)
        self.assertEqual(len(log.items_detail), 1)
        self.assertEqual(log.items_detail[0]['slug'], 'article-1')
        self.assertEqual(log.items_detail[0]['action'], 'updated')

        # Persisted state reflects the new body.
        article = Article.objects.get(slug='article-1')
        self.assertIn('Brand new body', article.content_markdown)

    def test_resync_does_not_bump_last_synced_at_only(self):
        """``last_synced_at`` should still bump even when nothing changed.

        Issue #225 acceptance criterion - the dashboard wants to see
        "checked recently" even when the result is "no changes".
        """
        self._seed_articles(n=2)
        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.source.refresh_from_db()
        first_ts = self.source.last_synced_at

        # Re-sync identical content
        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.source.refresh_from_db()
        self.assertIsNotNone(self.source.last_synced_at)
        self.assertGreaterEqual(self.source.last_synced_at, first_ts)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class SyncProjectsUnchangedTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/projects-225',
            content_type='project',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resync_unchanged_projects_is_a_noop(self):
        for i in range(2):
            _write_md(
                os.path.join(self.temp_dir, f'project-{i}.md'),
                {
                    'title': f'Project {i}',
                    'slug': f'project-{i}',
                    'description': f'Test {i}',
                    'date': '2026-01-15',
                    'difficulty': 'beginner',
                    'content_id': f'bbbbbbbb-0000-0000-0000-{i:012d}',
                },
                f'Body for project {i}.',
            )
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 2)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 2)
        self.assertEqual(log2.items_detail, [])


# ---------------------------------------------------------------------------
# Interview questions
# ---------------------------------------------------------------------------


class SyncInterviewQuestionsUnchangedTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/interview-225',
            content_type='interview_question',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resync_unchanged_interview_categories(self):
        _write_md(
            os.path.join(self.temp_dir, 'theory.md'),
            {'title': 'Theory Questions'},
            'Body content here.',
        )
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 1)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 1)
        self.assertEqual(log2.items_detail, [])


# ---------------------------------------------------------------------------
# Courses (single-course mode + modules + units)
# ---------------------------------------------------------------------------


class SyncCoursesUnchangedTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/course-225',
            content_type='course',
            content_path='',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_course(self, n_modules=2, units_per_module=2):
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "C"\n')
            f.write('slug: "c-225"\n')
            f.write('description: "D"\n')
            f.write('instructor_name: "I"\n')
            f.write('required_level: 0\n')
            f.write('content_id: "cccccccc-0000-0000-0000-000000000000"\n')

        for m in range(1, n_modules + 1):
            mdir = os.path.join(self.temp_dir, f'module-{m:02d}')
            os.makedirs(mdir, exist_ok=True)
            with open(os.path.join(mdir, 'module.yaml'), 'w') as f:
                f.write(f'title: "Module {m}"\n')
                f.write(f'sort_order: {m}\n')
            for u in range(1, units_per_module + 1):
                content_id = (
                    f'dddddddd-0000-0000-{m:04d}-{u:012d}'
                )
                upath = os.path.join(mdir, f'unit-{u:02d}.md')
                _write_md(
                    upath,
                    {
                        'title': f'Unit {m}.{u}',
                        'sort_order': u,
                        'content_id': content_id,
                    },
                    f'Body for unit {m}.{u}.',
                )

    def test_resync_unchanged_course_tree_is_a_noop(self):
        self._build_course(n_modules=2, units_per_module=3)
        # 1 course + 2 modules + 6 units = 9
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 9)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 9)
        self.assertEqual(log2.items_detail, [])

    def test_changing_one_unit_body_only_updates_that_unit(self):
        self._build_course(n_modules=2, units_per_module=3)
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Modify just one unit's body.
        _write_md(
            os.path.join(self.temp_dir, 'module-01', 'unit-02.md'),
            {
                'title': 'Unit 1.2',
                'sort_order': 2,
                'content_id': 'dddddddd-0000-0000-0001-000000000002',
            },
            'A completely new body for this unit only.',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 1)
        # 1 course + 2 modules + 5 untouched units = 8 unchanged
        self.assertEqual(log.items_unchanged, 8)

        # Only the changed unit appears in items_detail and it's flagged updated.
        self.assertEqual(len(log.items_detail), 1)
        only_item = log.items_detail[0]
        self.assertEqual(only_item['content_type'], 'unit')
        self.assertEqual(only_item['title'], 'Unit 1.2')
        self.assertEqual(only_item['action'], 'updated')

        # Persisted state.
        unit = Unit.objects.get(slug='unit-02', module__sort_order=1)
        self.assertIn('completely new body', unit.body)

    def test_changing_module_title_only_updates_that_module(self):
        self._build_course(n_modules=2, units_per_module=2)
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Change only module-02's title via its YAML.
        with open(
            os.path.join(self.temp_dir, 'module-02', 'module.yaml'),
            'w',
        ) as f:
            f.write('title: "Module 2 - Renamed"\n')
            f.write('sort_order: 2\n')

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 1)

        details = log.items_detail
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]['content_type'], 'module')
        self.assertEqual(details[0]['title'], 'Module 2 - Renamed')


# ---------------------------------------------------------------------------
# Curated links + Downloads (resource sync)
# ---------------------------------------------------------------------------


class SyncResourcesUnchangedTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/resources-225',
            content_type='resource',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_resources(self):
        # Curated link
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir, exist_ok=True)
        _write_md(
            os.path.join(links_dir, 'link-1.md'),
            {
                'title': 'A Useful Link',
                'url': 'https://example.com/post',
                'item_id': 'eeeeeeee-0000-0000-0000-000000000001',
                'category': 'article',
            },
            'Some short description.',
        )
        # Download
        downloads_dir = os.path.join(self.temp_dir, 'downloads')
        os.makedirs(downloads_dir, exist_ok=True)
        _write_yaml(
            os.path.join(downloads_dir, 'cheatsheet.yaml'),
            {
                'title': 'Cheatsheet',
                'slug': 'cheatsheet',
                'file_url': 'https://example.com/cheatsheet.pdf',
                'file_type': 'pdf',
                'content_id': 'ffffffff-0000-0000-0000-000000000001',
            },
        )

    def test_resync_unchanged_resources_is_a_noop(self):
        self._seed_resources()
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 2)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 2)
        self.assertEqual(log2.items_detail, [])

    def test_changing_curated_link_only_marks_that_one_updated(self):
        self._seed_resources()
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Touch only the curated link.
        _write_md(
            os.path.join(self.temp_dir, 'curated-links', 'link-1.md'),
            {
                'title': 'A Useful Link (renamed)',
                'url': 'https://example.com/post',
                'item_id': 'eeeeeeee-0000-0000-0000-000000000001',
                'category': 'article',
            },
            'Some short description.',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 1)
        self.assertEqual(log.items_unchanged, 1)
        self.assertEqual(len(log.items_detail), 1)
        self.assertEqual(
            log.items_detail[0]['title'], 'A Useful Link (renamed)',
        )

        # Persisted state.
        link = CuratedLink.objects.get(
            item_id='eeeeeeee-0000-0000-0000-000000000001',
        )
        self.assertEqual(link.title, 'A Useful Link (renamed)')


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class SyncEventsUnchangedTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/events-225',
            content_type='event',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_event(self, title='Workshop', slug='workshop-1'):
        _write_yaml(
            os.path.join(self.temp_dir, f'{slug}.yaml'),
            {
                'title': title,
                'slug': slug,
                'description': 'A great workshop.',
                'speaker_name': 'Alice',
                'recording_url': 'https://example.com/recording',
                'content_id': '99999999-0000-0000-0000-000000000001',
                'published_at': '2026-01-10',
            },
        )

    def test_resync_unchanged_event_is_a_noop(self):
        self._seed_event()
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.items_created, 1)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 1)
        self.assertEqual(log2.items_detail, [])

    def test_changing_event_description_marks_event_updated(self):
        self._seed_event()
        sync_content_source(self.source, repo_dir=self.temp_dir)

        _write_yaml(
            os.path.join(self.temp_dir, 'workshop-1.yaml'),
            {
                'title': 'Workshop',
                'slug': 'workshop-1',
                'description': 'An updated description.',
                'speaker_name': 'Alice',
                'recording_url': 'https://example.com/recording',
                'content_id': '99999999-0000-0000-0000-000000000001',
                'published_at': '2026-01-10',
            },
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.items_updated, 1)
        self.assertEqual(log.items_unchanged, 0)
        self.assertEqual(len(log.items_detail), 1)
        self.assertEqual(log.items_detail[0]['action'], 'updated')

    def test_resyncing_event_does_not_overwrite_operational_fields(self):
        """Only content fields should sync; operational fields stay put.

        This is the existing event-sync invariant (Event sync only touches
        ``content_defaults`` keys). We re-assert it here so a regression in
        the new no-change path can't accidentally touch operational fields
        like ``status`` or ``zoom_join_url``.
        """
        self._seed_event()
        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Manually set an operational field on the event row.
        ev = Event.objects.get(slug='workshop-1')
        ev.zoom_join_url = 'https://zoom.us/j/123'
        ev.save()

        # Re-sync with identical content.
        sync_content_source(self.source, repo_dir=self.temp_dir)

        ev.refresh_from_db()
        self.assertEqual(ev.zoom_join_url, 'https://zoom.us/j/123')


# ---------------------------------------------------------------------------
# Module README-as-unit
# ---------------------------------------------------------------------------


class SyncCourseReadmeUnitUnchangedTest(TestCase):
    """README.md at the module root is promoted to a Unit; verify the same
    no-change behaviour applies to that code path (it has its own upsert
    block separate from the regular unit loop)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/course-readme-225',
            content_type='course',
            content_path='',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build(self):
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "C"\n')
            f.write('slug: "rc-225"\n')
            f.write('description: "D"\n')
            f.write('instructor_name: "I"\n')
            f.write('required_level: 0\n')
            f.write('content_id: "12121212-0000-0000-0000-000000000000"\n')

        mdir = os.path.join(self.temp_dir, 'module-01')
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, 'module.yaml'), 'w') as f:
            f.write('title: "M1"\n')
            f.write('sort_order: 1\n')
        # README acts as the first unit.
        with open(os.path.join(mdir, 'README.md'), 'w') as f:
            f.write('# Module 1 intro\n\nReadme body here.\n')

    def test_resync_unchanged_readme_unit(self):
        self._build()
        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        # 1 course + 1 module + 1 README-unit = 3
        self.assertEqual(log1.items_created, 3)

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_updated, 0)
        self.assertEqual(log2.items_unchanged, 3)
        self.assertEqual(log2.items_detail, [])
