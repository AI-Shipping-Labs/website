"""Tests for course sync: ignore patterns + README handling (issue #200).

Covers:
- ``ignore:`` in course.yaml is honored across modules (course-level)
- ``ignore:`` in module.yaml is honored within that module (merges on top)
- Recursive ``**`` globs (``**/*.template.md``, ``**/plan.md``, ``docs/**``)
- Module-root README.md becomes the first unit (sort_order = -1)
- Course-root README.md populates Course.description when no explicit
  ``description:`` is set
- Explicit ``description:`` in course.yaml overrides the README
- README listed in ``ignore:`` is suppressed
- Re-running the sync is idempotent (stable content_id, no churn)
- Helper functions (_matches_ignore_patterns, _extract_readme_title,
  _derive_readme_content_id)
"""

import os
import shutil
import tempfile
import uuid

from django.test import TestCase

from content.models import Course, Module, Unit
from integrations.models import ContentSource
from integrations.services.github import (
    _derive_readme_content_id,
    _extract_readme_title,
    _matches_ignore_patterns,
    sync_content_source,
)


class MatchesIgnorePatternsTest(TestCase):
    """Unit tests for the glob matcher used by course sync."""

    def test_empty_patterns_never_match(self):
        self.assertFalse(_matches_ignore_patterns('AGENTS.md', []))
        self.assertFalse(_matches_ignore_patterns('AGENTS.md', None))

    def test_literal_filename(self):
        self.assertTrue(_matches_ignore_patterns('AGENTS.md', ['AGENTS.md']))
        self.assertFalse(_matches_ignore_patterns('AGENT.md', ['AGENTS.md']))

    def test_directory_recursive_glob(self):
        self.assertTrue(_matches_ignore_patterns('docs/writing.md', ['docs/**']))
        self.assertTrue(_matches_ignore_patterns('docs/sub/x.md', ['docs/**']))
        self.assertFalse(_matches_ignore_patterns('other/writing.md', ['docs/**']))

    def test_template_files_recursive(self):
        self.assertTrue(_matches_ignore_patterns(
            '01-intro/example.template.md', ['**/*.template.md'],
        ))
        self.assertTrue(_matches_ignore_patterns(
            '02-basics/deep/x.template.md', ['**/*.template.md'],
        ))
        self.assertFalse(_matches_ignore_patterns(
            '01-intro/example.md', ['**/*.template.md'],
        ))

    def test_plan_files_recursive(self):
        self.assertTrue(_matches_ignore_patterns(
            '01-intro/plan.md', ['**/plan.md'],
        ))
        self.assertFalse(_matches_ignore_patterns(
            '01-intro/planning.md', ['**/plan.md'],
        ))

    def test_multiple_patterns_any_matches(self):
        patterns = ['AGENTS.md', 'docs/**', '**/*.template.md']
        self.assertTrue(_matches_ignore_patterns('AGENTS.md', patterns))
        self.assertTrue(_matches_ignore_patterns('docs/x.md', patterns))
        self.assertTrue(_matches_ignore_patterns('01/x.template.md', patterns))
        self.assertFalse(_matches_ignore_patterns('01/real.md', patterns))

    def test_malformed_glob_does_not_crash(self):
        # Unsupported patterns just don't match; sync must keep going.
        # (PurePath.full_match raises ValueError on e.g. empty patterns.)
        self.assertFalse(_matches_ignore_patterns('x.md', ['']))


class ExtractReadmeTitleTest(TestCase):
    """Unit tests for pulling the H1 out of a README body."""

    def test_first_h1_is_used(self):
        body = '# Welcome to Python\n\nSome intro.\n## Subheading\n'
        self.assertEqual(
            _extract_readme_title(body, fallback='Fallback'),
            'Welcome to Python',
        )

    def test_first_h1_wins_over_later_h1(self):
        body = '# First\n\n# Second\n'
        self.assertEqual(
            _extract_readme_title(body, fallback='F'), 'First',
        )

    def test_no_h1_returns_fallback(self):
        body = '## Only H2\n\nPlain text.\n'
        self.assertEqual(
            _extract_readme_title(body, fallback='Module Title'),
            'Module Title',
        )

    def test_empty_body_returns_fallback(self):
        self.assertEqual(
            _extract_readme_title('', fallback='Module Title'),
            'Module Title',
        )


class DeriveReadmeContentIdTest(TestCase):
    """The derived UUID must be stable and distinct per module."""

    def test_stable_across_calls(self):
        a = _derive_readme_content_id('org/repo', '01-intro')
        b = _derive_readme_content_id('org/repo', '01-intro')
        self.assertEqual(a, b)
        # Valid UUID string.
        uuid.UUID(a)

    def test_different_modules_get_different_ids(self):
        a = _derive_readme_content_id('org/repo', '01-intro')
        b = _derive_readme_content_id('org/repo', '02-basics')
        self.assertNotEqual(a, b)

    def test_different_repos_get_different_ids(self):
        a = _derive_readme_content_id('org/repo', '01-intro')
        b = _derive_readme_content_id('other/repo', '01-intro')
        self.assertNotEqual(a, b)


class _CourseSyncFixtureBase(TestCase):
    """Helpers to write a realistic single-course repo on disk."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            content_path='',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, text):
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(text)
        return full

    def _write_root_course_yaml(self, extras=''):
        body = (
            'title: "Python Course"\n'
            'slug: "python-course"\n'
            'instructor_name: "Alexey Grigorev"\n'
            'required_level: 20\n'
            'is_free: false\n'
            'content_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"\n'
        ) + extras
        self._write('course.yaml', body)

    def _write_module_yaml(self, dirname, title, content_id, extras=''):
        body = (
            f'title: "{title}"\n'
            f'content_id: "{content_id}"\n'
        ) + extras
        self._write(f'{dirname}/module.yaml', body)

    def _write_unit(self, dirname, filename, title, content_id,
                    body='Unit body.\n'):
        text = (
            '---\n'
            f'title: "{title}"\n'
            f'content_id: "{content_id}"\n'
            '---\n'
            f'{body}'
        )
        self._write(f'{dirname}/{filename}', text)


class CourseRootReadmeAsDescriptionTest(_CourseSyncFixtureBase):
    """Course-root README.md populates Course.description by default."""

    def test_readme_becomes_description_when_no_explicit_description(self):
        self._write_root_course_yaml()  # no description: key
        self._write('README.md', '# Python Course\n\nWelcome to the course.\n')
        self._write_module_yaml(
            '01-intro', 'Intro', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why', 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug='python-course')
        self.assertIn('Welcome to the course.', course.description)
        # Rendered HTML picks up the README heading.
        self.assertIn('Python Course', course.description_html)

    def test_explicit_description_overrides_readme(self):
        self._write_root_course_yaml(
            extras='description: "Explicit description from YAML."\n',
        )
        self._write('README.md', '# Ignored README\n\nThis should not win.\n')
        self._write_module_yaml(
            '01-intro', 'Intro', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        course = Course.objects.get(slug='python-course')
        self.assertEqual(course.description, 'Explicit description from YAML.')
        self.assertNotIn('Ignored README', course.description)

    def test_readme_in_ignore_is_suppressed(self):
        self._write_root_course_yaml(
            extras='ignore:\n  - README.md\n',
        )
        self._write('README.md', '# Suppressed\n\nShould not appear.\n')
        self._write_module_yaml(
            '01-intro', 'Intro', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        course = Course.objects.get(slug='python-course')
        self.assertEqual(course.description, '')

    def test_missing_readme_leaves_description_empty(self):
        self._write_root_course_yaml()
        self._write_module_yaml(
            '01-intro', 'Intro', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        course = Course.objects.get(slug='python-course')
        self.assertEqual(course.description, '')


class ModuleReadmeAsFirstUnitTest(_CourseSyncFixtureBase):
    """Module-root README.md becomes the first unit of the module."""

    def test_module_readme_becomes_first_unit(self):
        self._write_root_course_yaml()
        self._write_module_yaml(
            '01-intro', 'Introduction',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        # Module README with an H1 distinct from the module title.
        self._write(
            '01-intro/README.md',
            '# Getting Started With Python\n\nOverview of this module.\n',
        )
        # Two numbered units after it.
        self._write_unit(
            '01-intro', '01-why.md', 'Why Python',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )
        self._write_unit(
            '01-intro', '02-setup.md', 'Setup',
            'dddddddd-dddd-dddd-dddd-dddddddddddd',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        module = Module.objects.get(course__slug='python-course')
        units = list(Unit.objects.filter(module=module).order_by('sort_order'))
        titles = [u.title for u in units]
        self.assertEqual(
            titles,
            ['Getting Started With Python', 'Why Python', 'Setup'],
        )
        # First unit is the README and comes before the numbered units.
        self.assertEqual(units[0].sort_order, -1)
        self.assertEqual(units[0].slug, 'readme')
        self.assertIn('Overview of this module.', units[0].body)

    def test_module_readme_title_falls_back_to_module_title(self):
        self._write_root_course_yaml()
        self._write_module_yaml(
            '01-intro', 'Introduction',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        # README without H1.
        self._write('01-intro/README.md', 'No heading, just text.\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        unit = Unit.objects.get(
            module__course__slug='python-course', slug='readme',
        )
        self.assertEqual(unit.title, 'Introduction')

    def test_module_readme_in_module_ignore_is_suppressed(self):
        self._write_root_course_yaml()
        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            extras='ignore:\n  - README.md\n',
        )
        self._write('01-intro/README.md', '# Should Be Skipped\n\nNope.\n')
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        module = Module.objects.get(course__slug='python-course')
        slugs = set(Unit.objects.filter(module=module).values_list('slug', flat=True))
        self.assertNotIn('readme', slugs)
        self.assertEqual(slugs, {'why'})

    def test_module_readme_content_id_is_stable_across_syncs(self):
        self._write_root_course_yaml()
        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write('01-intro/README.md', '# Intro\n\nFirst.\n')
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        first_unit = Unit.objects.get(slug='readme')
        first_id = first_unit.content_id

        # Run sync again - no churn.
        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(Unit.objects.filter(slug='readme').count(), 1)
        self.assertEqual(
            Unit.objects.get(slug='readme').content_id, first_id,
        )


class CourseIgnorePatternsTest(_CourseSyncFixtureBase):
    """Course-level ``ignore:`` is honored across all modules."""

    def test_course_level_ignores_skip_matching_files(self):
        self._write_root_course_yaml(
            extras=(
                'ignore:\n'
                '  - AGENTS.md\n'
                '  - docs/**\n'
                '  - "**/*.template.md"\n'
                '  - "**/plan.md"\n'
            ),
        )
        # Ignored files at the course root
        self._write('AGENTS.md', '# Agents\n\nInstructions for AI agents.\n')
        self._write('docs/writing.md', '# Writing guide\n')
        self._write('docs/style.md', '# Style guide\n')

        # Module with legitimate content + ignored files.
        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )
        # Template scaffolding - must not become a unit.
        self._write(
            '01-intro/01-why.template.md',
            '---\ntitle: "Template"\n---\nTEMPLATE body\n',
        )
        # Author's plan file - must not become a unit.
        self._write(
            '01-intro/plan.md',
            '---\ntitle: "Plan"\n---\nAuthor notes.\n',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        # No errors about missing content_id on ignored files.
        self.assertEqual(log.errors, [])

        # Only the one real unit exists.
        course = Course.objects.get(slug='python-course')
        module = Module.objects.get(course=course)
        unit_slugs = set(
            Unit.objects.filter(module=module).values_list('slug', flat=True),
        )
        # Must not include template/plan files.
        self.assertNotIn('why.template', unit_slugs)
        self.assertNotIn('plan', unit_slugs)
        # Only 'why' is present.
        self.assertEqual(unit_slugs, {'why'})

    def test_ignored_docs_dir_is_not_synced_as_module(self):
        """Directory matched by ``docs/**`` is skipped even if it has files."""
        self._write_root_course_yaml(extras='ignore:\n  - docs/**\n')
        # docs/ would never be a module (no module.yaml) but verify nothing
        # gets walked.
        self._write('docs/writing.md', '# Writing\n')
        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])
        # Exactly one module was synced.
        course = Course.objects.get(slug='python-course')
        self.assertEqual(Module.objects.filter(course=course).count(), 1)


class ModuleIgnoreMergesWithCourseTest(_CourseSyncFixtureBase):
    """Module-level ignore patterns are additive to course-level."""

    def test_module_ignore_suppresses_only_its_module(self):
        self._write_root_course_yaml()
        # Module 1: suppresses README via module.yaml ignore.
        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            extras='ignore:\n  - README.md\n',
        )
        self._write('01-intro/README.md', '# Not synced\n')
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        # Module 2: keeps README.
        self._write_module_yaml(
            '02-basics', 'Basics',
            'dddddddd-dddd-dddd-dddd-dddddddddddd',
        )
        self._write('02-basics/README.md', '# Basics README\n\nKeep me.\n')
        self._write_unit(
            '02-basics', '01-vars.md', 'Vars',
            'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
        )

        sync_content_source(self.source, repo_dir=self.temp_dir)
        m1 = Module.objects.get(slug='intro')
        m2 = Module.objects.get(slug='basics')
        m1_slugs = set(Unit.objects.filter(module=m1).values_list('slug', flat=True))
        m2_slugs = set(Unit.objects.filter(module=m2).values_list('slug', flat=True))
        self.assertNotIn('readme', m1_slugs)
        self.assertIn('readme', m2_slugs)


class CourseSyncIdempotencyTest(_CourseSyncFixtureBase):
    """A second identical sync must not create or delete anything."""

    def test_double_sync_is_idempotent(self):
        self._write_root_course_yaml(
            extras=(
                'ignore:\n'
                '  - AGENTS.md\n'
                '  - "**/*.template.md"\n'
            ),
        )
        self._write('README.md', '# Python Course\n\nOverview.\n')
        self._write('AGENTS.md', '# Agents\nIgnored.\n')

        self._write_module_yaml(
            '01-intro', 'Intro',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write('01-intro/README.md', '# Intro\n\nFirst unit.\n')
        self._write_unit(
            '01-intro', '01-why.md', 'Why',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )
        self._write(
            '01-intro/01-why.template.md',
            '---\ntitle: "Template"\n---\nT\n',
        )

        log1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log1.errors, [])
        created_first = log1.items_created

        log2 = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log2.errors, [])
        # Second sync creates nothing new and deletes nothing.
        self.assertEqual(log2.items_created, 0)
        self.assertEqual(log2.items_deleted, 0)

        # Sanity: the first sync created course (1) + module (1) + readme unit
        # (1) + why unit (1) = 4.
        self.assertEqual(created_first, 4)
