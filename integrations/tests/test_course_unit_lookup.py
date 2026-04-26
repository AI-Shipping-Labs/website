"""Tests for ``_build_course_unit_lookup`` (issue #233).

The lookup feeds the markdown link rewriter (issue #226). It must agree
exactly with what :func:`_sync_module_units` actually persists, otherwise
the rewriter produces "working-looking" URLs to units that were never
created — silent 404s instead of the standard unresolvable-link warning.

Each test below pins one of the divergence cases the PM caught in #233:

- Course-level ``ignore:`` globs are honored.
- Module-level ``ignore:`` globs are honored.
- Files missing ``content_id`` in frontmatter are skipped (README excepted).
- README is always registered under the ``__module_overview__`` sentinel.
- The unit slug fallback uses ``metadata.get('slug', derive_slug(filename))``
  (key-absent default), mirroring ``_sync_module_units``.

The companion sync-level test
(:class:`UnitLookupRespectsIgnoresEndToEndTest` below) exercises the same
shape end-to-end and confirms that a link to an ignored file emits the
standard unresolvable-link warning rather than a 404 URL.
"""

import os
import shutil
import tempfile

from django.test import TestCase

from content.models import Course, Unit
from integrations.models import ContentSource
from integrations.services.github import (
    _build_course_unit_lookup,
    sync_content_source,
)


class _LookupFixtureBase(TestCase):
    """Helpers for assembling a minimal course tree on disk."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.course_dir = os.path.join(self.temp_dir, 'python-course')
        os.makedirs(self.course_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def _write_module(self, dirname, extras=''):
        self._write(
            f'{dirname}/module.yaml',
            f'title: "{dirname}"\nsort_order: 1\n' + extras,
        )

    def _write_unit(self, dirname, filename, content_id='aaaa', extras=''):
        self._write(
            f'{dirname}/{filename}',
            (
                '---\n'
                f'title: "{filename}"\n'
                f'content_id: "{content_id}"\n'
            ) + extras + '---\nBody.\n',
        )


class BuildCourseUnitLookupIgnoreGlobsTest(_LookupFixtureBase):
    """Files matched by ignore globs must NOT appear in the lookup."""

    def test_course_level_ignore_glob_excludes_file(self):
        self._write_module('01-fundamentals')
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )
        # File that matches a course-level ignore pattern.
        self._write_unit(
            '01-fundamentals', 'plan.md',
            content_id='22222222-2222-2222-2222-222222222222',
        )

        lookup = _build_course_unit_lookup(
            self.course_dir,
            course_ignore_patterns=['**/plan.md'],
        )

        self.assertIn('fundamentals', lookup)
        self.assertIn('01-intro.md', lookup['fundamentals'])
        # The ignored file must not be a link target.
        self.assertNotIn('plan.md', lookup['fundamentals'])

    def test_course_level_ignore_glob_excludes_files_inside_dir(self):
        """``drafts/**`` filters every file in ``drafts/``, mirroring the sync.

        The sync still creates a Module row for ``drafts/`` (the dir name
        ``drafts`` itself is not matched by the ``drafts/**`` glob — only
        files inside are), but every file in it is dropped. The lookup
        mirrors that: the module key is present with an empty files dict,
        which is enough for the rewriter to skip every link into it.
        """
        # `drafts/` module: every file inside matches the course-level glob.
        self._write_module('drafts')
        self._write_unit(
            'drafts', '01-rough.md',
            content_id='33333333-3333-3333-3333-333333333333',
        )
        # Real module that should still appear with its unit.
        self._write_module('01-fundamentals')
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )

        lookup = _build_course_unit_lookup(
            self.course_dir,
            course_ignore_patterns=['drafts/**'],
        )

        self.assertIn('fundamentals', lookup)
        self.assertIn('01-intro.md', lookup['fundamentals'])
        # The drafts module exists but has no units to link to.
        self.assertEqual(lookup.get('drafts'), {})

    def test_course_level_ignore_glob_skips_dir_named_exactly(self):
        """A glob that matches the directory name itself skips the whole module.

        ``drafts`` (no slash) matches the dir entry by literal name, so the
        dir-level check in _sync_course_modules drops the module entirely.
        The lookup mirrors that.
        """
        self._write_module('drafts')
        self._write_unit(
            'drafts', '01-rough.md',
            content_id='33333333-3333-3333-3333-333333333333',
        )
        self._write_module('01-fundamentals')
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )

        lookup = _build_course_unit_lookup(
            self.course_dir,
            course_ignore_patterns=['drafts'],
        )

        self.assertIn('fundamentals', lookup)
        self.assertNotIn('drafts', lookup)

    def test_module_level_ignore_glob_excludes_file(self):
        """Module-level ``ignore:`` is read from module.yaml."""
        self._write_module(
            '01-fundamentals',
            extras='ignore:\n  - "*.template.md"\n',
        )
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )
        # Template file: matched by module-level ignore.
        self._write_unit(
            '01-fundamentals', 'snippet.template.md',
            content_id='44444444-4444-4444-4444-444444444444',
        )

        lookup = _build_course_unit_lookup(self.course_dir)

        self.assertIn('01-intro.md', lookup['fundamentals'])
        self.assertNotIn('snippet.template.md', lookup['fundamentals'])


class BuildCourseUnitLookupContentIdTest(_LookupFixtureBase):
    """Non-README files missing ``content_id`` must be excluded."""

    def test_unit_without_content_id_is_skipped(self):
        self._write_module('01-fundamentals')
        # Real unit with content_id.
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )
        # Unit missing content_id — _sync_module_units logs a warning and
        # creates no Unit row, so the lookup must skip it too.
        self._write(
            '01-fundamentals/02-orphan.md',
            (
                '---\n'
                'title: "Orphan"\n'
                # No content_id.
                '---\n'
                'Body.\n'
            ),
        )

        lookup = _build_course_unit_lookup(self.course_dir)

        self.assertIn('01-intro.md', lookup['fundamentals'])
        self.assertNotIn('02-orphan.md', lookup['fundamentals'])

    def test_readme_does_not_require_content_id(self):
        """README's content_id is derived; missing frontmatter is fine."""
        self._write_module('01-fundamentals')
        # README has no frontmatter at all — still becomes the overview.
        self._write(
            '01-fundamentals/README.md',
            '# Fundamentals\n\nOverview.\n',
        )
        self._write_unit(
            '01-fundamentals', '01-intro.md',
            content_id='11111111-1111-1111-1111-111111111111',
        )

        lookup = _build_course_unit_lookup(self.course_dir)

        self.assertEqual(
            lookup['fundamentals']['README.md'], '__module_overview__',
        )


class BuildCourseUnitLookupReadmeSlugTest(_LookupFixtureBase):
    """README always registers under the overview sentinel.

    The PM noted that the previous implementation used ``or '__module_overview__'``
    (falsy fallback) where the sync uses ``metadata.get('slug', ...)``
    (key-absent fallback). Today README is never a Unit (issue #222), so
    the README's ``slug:`` in frontmatter is academic — it always maps to
    the module-overview sentinel. This test pins that.
    """

    def test_readme_with_no_slug_key_uses_sentinel(self):
        self._write_module('01-fundamentals')
        self._write('01-fundamentals/README.md', '# Hi\n\nText.\n')

        lookup = _build_course_unit_lookup(self.course_dir)
        self.assertEqual(
            lookup['fundamentals']['README.md'], '__module_overview__',
        )

    def test_unit_slug_uses_key_absent_default(self):
        """A unit with no ``slug:`` key falls back to the filename slug."""
        self._write_module('01-fundamentals')
        self._write_unit(
            '01-fundamentals', '02-setup.md',
            content_id='22222222-2222-2222-2222-222222222222',
        )

        lookup = _build_course_unit_lookup(self.course_dir)
        # ``02-setup.md`` -> slug ``setup`` via derive_slug, matching what
        # _sync_module_units writes to ``Unit.slug``.
        self.assertEqual(lookup['fundamentals']['02-setup.md'], 'setup')

    def test_explicit_slug_in_frontmatter_wins(self):
        self._write_module('01-fundamentals')
        self._write_unit(
            '01-fundamentals', '02-setup.md',
            content_id='22222222-2222-2222-2222-222222222222',
            extras='slug: "custom-setup"\n',
        )

        lookup = _build_course_unit_lookup(self.course_dir)
        self.assertEqual(
            lookup['fundamentals']['02-setup.md'], 'custom-setup',
        )


class UnitLookupRespectsIgnoresEndToEndTest(TestCase):
    """End-to-end: links to ignored or content_id-less files stay raw.

    This is the user-facing behavior issue #233 fixes: a link to a file
    that the sync skips must NOT be rewritten to a platform URL (which
    would 404 silently). It must be left as the original ``.md`` href and
    paired with an unresolvable-link warning on the SyncLog.
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def test_link_to_ignored_file_left_unrewritten_with_warning(self):
        """A sibling link to an ignored file must NOT be rewritten.

        Without #233, the lookup would still know about ``02-draft.md``
        and the rewriter would emit ``/courses/.../fundamentals/draft`` —
        a 404 because the sync never created that Unit. With #233 the
        ignored file is absent from the lookup and the rewriter leaves
        the original ``.md`` href in place plus an unresolvable warning.
        """
        # course.yaml ignores any *.draft.md.
        self._write(
            'python-course/course.yaml',
            (
                'title: "Python Course"\n'
                'slug: "python-course"\n'
                'description: "Learn Python"\n'
                'instructor_name: "Test"\n'
                'required_level: 0\n'
                'content_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"\n'
                'ignore:\n'
                '  - "**/*.draft.md"\n'
            ),
        )
        self._write(
            'python-course/01-fundamentals/module.yaml',
            'title: "Fundamentals"\nsort_order: 1\n',
        )
        # Real unit links to an ignored sibling — sibling shape, so the
        # rewriter would absolutely have rewritten it if the file were in
        # the lookup.
        self._write(
            'python-course/01-fundamentals/01-intro.md',
            (
                '---\n'
                'title: "Intro"\n'
                'sort_order: 1\n'
                'content_id: "11111111-1111-1111-1111-111111111111"\n'
                '---\n'
                'See [Draft](02-setup.draft.md) for the upcoming change.\n'
            ),
        )
        # The ignored draft sibling.
        self._write(
            'python-course/01-fundamentals/02-setup.draft.md',
            (
                '---\n'
                'title: "Setup draft"\n'
                'content_id: "22222222-2222-2222-2222-222222222222"\n'
                '---\n'
                'Work in progress.\n'
            ),
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIn(sync_log.status, ('success', 'partial'))

        course = Course.objects.get(slug='python-course')
        intro = Unit.objects.get(module__course=course, slug='intro')
        # The ignored draft must not have become a Unit.
        self.assertFalse(
            Unit.objects.filter(slug='setup.draft').exists(),
            'Ignored file must not become a Unit',
        )

        # Original .md href must be preserved (not a rewritten platform URL).
        self.assertIn('02-setup.draft.md', intro.body_html)
        self.assertNotIn(
            '/courses/python-course/fundamentals/setup.draft',
            intro.body_html,
        )
        # And the sync logged the unresolvable-link warning rather than
        # silently emitting a 404 URL.
        self.assertTrue(
            any(
                '02-setup.draft.md' in (e.get('error') or '')
                and 'Unresolvable' in (e.get('error') or '')
                for e in (sync_log.errors or [])
            ),
            f'Expected unresolvable-link warning for 02-setup.draft.md; '
            f'got {sync_log.errors!r}',
        )

    def test_link_to_unit_missing_content_id_left_unrewritten(self):
        self._write(
            'python-course/course.yaml',
            (
                'title: "Python Course"\n'
                'slug: "python-course"\n'
                'description: "Learn Python"\n'
                'instructor_name: "Test"\n'
                'required_level: 0\n'
                'content_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"\n'
            ),
        )
        self._write(
            'python-course/01-fundamentals/module.yaml',
            'title: "Fundamentals"\nsort_order: 1\n',
        )
        # Real unit links to a sibling that is missing content_id.
        self._write(
            'python-course/01-fundamentals/01-intro.md',
            (
                '---\n'
                'title: "Intro"\n'
                'sort_order: 1\n'
                'content_id: "11111111-1111-1111-1111-111111111111"\n'
                '---\n'
                'Continue with [Setup](02-setup.md).\n'
            ),
        )
        # Sibling without content_id: sync warns and skips it.
        self._write(
            'python-course/01-fundamentals/02-setup.md',
            (
                '---\n'
                'title: "Setup"\n'
                # Deliberately missing content_id.
                '---\n'
                'Setup body.\n'
            ),
        )

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        course = Course.objects.get(slug='python-course')
        intro = Unit.objects.get(module__course=course, slug='intro')

        # The ghost-unit link must not get rewritten.
        self.assertIn('02-setup.md', intro.body_html)
        self.assertNotIn(
            '/courses/python-course/fundamentals/setup', intro.body_html,
        )
        # The sync produced two complementary warnings: the missing
        # content_id error AND the unresolvable-link warning.
        errors = sync_log.errors or []
        self.assertTrue(
            any('missing content_id' in (e.get('error') or '')
                for e in errors),
            f'Expected missing-content_id warning; got {errors!r}',
        )
        self.assertTrue(
            any(
                '02-setup.md' in (e.get('error') or '')
                and 'Unresolvable' in (e.get('error') or '')
                for e in errors
            ),
            f'Expected unresolvable-link warning for 02-setup.md; '
            f'got {errors!r}',
        )
