"""Tests for stripping the leading H1 from synced markdown — issue #227.

The sync pipeline renders the frontmatter title as the page heading. When
authors also start the body with ``# Title``, the reader sees the title
twice. ``strip_leading_title_h1`` removes that leading H1 — but only when
its text matches the title under whitespace-/case-tolerant comparison.

Acceptance-criteria coverage (from #227):
1. Exact match           -> stripped
2. Match + trailing punctuation/whitespace -> stripped
3. Different first H1    -> preserved
4. No H1 at all          -> no-op

Plus an integration test that round-trips the rule through the actual
``Article``/``Course``/``Unit`` save() path (the markdown -> HTML pipeline
the GitHub sync calls into) and a sync-level integration test that runs
``sync_content_source`` against a synthetic course on disk.
"""
import os
import tempfile
import uuid
from datetime import date

from django.test import TestCase

from content.models import Article, Course, Module, Unit
from content.utils.h1 import strip_leading_title_h1
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class StripLeadingTitleH1Test(TestCase):
    """Pure-function tests for the H1-stripping utility."""

    # ---- Acceptance criterion 1: exact match -> stripped ------------------

    def test_exact_match_is_stripped(self):
        body = '# Running examples\n\nThis course uses two running examples.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(
            result, 'This course uses two running examples.\n',
        )

    def test_exact_match_without_blank_line_after_h1(self):
        body = '# Title\nFirst paragraph.\n'
        result = strip_leading_title_h1(body, 'Title')
        self.assertEqual(result, 'First paragraph.\n')

    # ---- Acceptance criterion 2: match modulo punctuation -----------------

    def test_trailing_period_in_h1_still_matches_title(self):
        body = '# Running examples.\n\nBody content.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, 'Body content.\n')

    def test_trailing_period_in_title_still_matches_h1(self):
        body = '# Running examples\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running examples.')
        self.assertEqual(result, 'Body.\n')

    def test_case_insensitive_match_is_stripped(self):
        body = '# RUNNING EXAMPLES\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running Examples')
        self.assertEqual(result, 'Body.\n')

    def test_extra_whitespace_in_h1_is_normalised(self):
        body = '#   Running    examples   \n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, 'Body.\n')

    def test_leading_blank_lines_then_matching_h1_is_stripped(self):
        body = '\n\n# Title\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Title')
        self.assertEqual(result, 'Body.\n')

    def test_h1_with_optional_closing_hashes_is_stripped(self):
        # ATX headings allow optional trailing ``#`` characters — make sure
        # we don't misread the heading text because of them.
        body = '# Title #\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Title')
        self.assertEqual(result, 'Body.\n')

    # ---- Acceptance criterion 3: different first H1 -> preserved ----------

    def test_different_h1_is_preserved(self):
        body = '# Introduction\n\nThis is a different first heading.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, body)

    def test_h2_with_matching_text_is_preserved(self):
        # We only strip H1s. A matching H2 is real section structure.
        body = '## Running examples\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, body)

    def test_setext_h1_is_preserved(self):
        # Setext (``Title\n=====``) is rare; we don't try to detect it to
        # keep the rule narrow. Should be left untouched.
        body = 'Running examples\n================\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, body)

    def test_paragraph_before_h1_is_preserved(self):
        # If anything precedes the H1, the H1 is not "leading".
        body = 'A note from the author.\n\n# Running examples\n\nBody.\n'
        result = strip_leading_title_h1(body, 'Running examples')
        self.assertEqual(result, body)

    # ---- Acceptance criterion 4: no H1 at all -> no-op --------------------

    def test_body_without_any_heading_is_returned_unchanged(self):
        body = 'Just a paragraph, no heading at all.\n'
        result = strip_leading_title_h1(body, 'Whatever')
        self.assertEqual(result, body)

    def test_empty_body_is_returned_unchanged(self):
        self.assertEqual(strip_leading_title_h1('', 'Title'), '')

    def test_blank_body_is_returned_unchanged(self):
        self.assertEqual(strip_leading_title_h1('\n\n\n', 'Title'), '\n\n\n')

    # ---- Defensive cases --------------------------------------------------

    def test_missing_title_is_a_noop(self):
        body = '# Title\n\nBody.\n'
        self.assertEqual(strip_leading_title_h1(body, ''), body)
        self.assertEqual(strip_leading_title_h1(body, None), body)

    def test_only_a_matching_h1_no_body_strips_to_empty(self):
        result = strip_leading_title_h1('# Title\n', 'Title')
        self.assertEqual(result, '')

    def test_later_h1_is_not_stripped(self):
        # Only the very first heading is considered. A matching H1 deeper
        # in the body must survive.
        body = '# Intro\n\nBody.\n\n# Title\n\nMore body.\n'
        result = strip_leading_title_h1(body, 'Title')
        self.assertEqual(result, body)


class ArticleSaveStripsLeadingH1Test(TestCase):
    """End-to-end through the Article model render pipeline."""

    def test_matching_h1_does_not_appear_in_rendered_html(self):
        article = Article.objects.create(
            title='Running examples', slug='running-examples',
            date=date(2025, 1, 1),
            content_markdown='# Running examples\n\nThe body content.\n',
            published=True,
        )
        self.assertNotIn('<h1>Running examples</h1>', article.content_html)
        self.assertIn('The body content.', article.content_html)

    def test_different_h1_is_preserved_in_rendered_html(self):
        article = Article.objects.create(
            title='Running examples', slug='running-examples-2',
            date=date(2025, 1, 1),
            content_markdown='# Why this matters\n\nThe body content.\n',
            published=True,
        )
        self.assertIn('<h1>Why this matters</h1>', article.content_html)

    def test_stored_markdown_is_unchanged(self):
        # We strip in the rendering step, not in the source-of-truth field.
        original = '# Running examples\n\nThe body content.\n'
        article = Article.objects.create(
            title='Running examples', slug='running-examples-3',
            date=date(2025, 1, 1),
            content_markdown=original,
            published=True,
        )
        article.refresh_from_db()
        self.assertEqual(article.content_markdown, original)


class CourseSaveStripsLeadingH1Test(TestCase):
    """End-to-end through the Course (description) render pipeline."""

    def test_matching_h1_does_not_appear_in_description_html(self):
        course = Course.objects.create(
            title='Python Course', slug='python-course-h1',
            description='# Python Course\n\nLearn Python from scratch.\n',
        )
        self.assertNotIn('<h1>Python Course</h1>', course.description_html)
        self.assertIn('Learn Python from scratch.', course.description_html)

    def test_different_h1_is_preserved_in_description_html(self):
        course = Course.objects.create(
            title='Python Course', slug='python-course-h1-keep',
            description='# Why Python\n\nLearn Python from scratch.\n',
        )
        self.assertIn('<h1>Why Python</h1>', course.description_html)


class UnitSaveStripsLeadingH1Test(TestCase):
    """End-to-end through the Unit (body) render pipeline."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Python Course', slug='python-course-units-h1',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Fundamentals', slug='fundamentals',
        )

    def test_matching_h1_does_not_appear_in_body_html(self):
        unit = Unit.objects.create(
            module=self.module, title='Running examples', slug='running-examples',
            body='# Running examples\n\nThis course uses two running examples.\n',
        )
        self.assertNotIn('<h1>Running examples</h1>', unit.body_html)
        self.assertIn('This course uses two running examples.', unit.body_html)

    def test_h1_with_trailing_period_does_not_appear_in_body_html(self):
        unit = Unit.objects.create(
            module=self.module, title='Why Python', slug='why-python-1',
            body='# Why Python.\n\nBecause it is great.\n',
        )
        self.assertNotIn('<h1>Why Python', unit.body_html)
        self.assertIn('Because it is great.', unit.body_html)

    def test_different_h1_is_preserved_in_body_html(self):
        unit = Unit.objects.create(
            module=self.module, title='Why Python', slug='why-python-2',
            body='# A short tour\n\nLet us begin.\n',
        )
        self.assertIn('<h1>A short tour</h1>', unit.body_html)


class SyncStripsLeadingH1Test(TestCase):
    """Integration: the whole sync_content_source path strips the H1."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/python-course-h1',
            content_type='course',
            content_path='',
        )
        self.temp_dir = tempfile.mkdtemp()
        # Single-course mode: course.yaml at repo root.
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write(
                'title: Python Course\n'
                'slug: python-course-h1\n'
                f'content_id: "{uuid.uuid4()}"\n'
            )

        module_dir = os.path.join(self.temp_dir, '01-fundamentals')
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: Fundamentals\nslug: fundamentals\n')

        # Unit whose body H1 matches the title — should be stripped.
        with open(os.path.join(module_dir, '01-running-examples.md'), 'w') as f:
            f.write(
                '---\n'
                'title: Running examples\n'
                'slug: running-examples\n'
                f'content_id: "{uuid.uuid4()}"\n'
                '---\n'
                '# Running examples\n\n'
                'This course uses two running examples.\n'
            )

        # Unit whose body H1 differs from the title — should be kept.
        with open(os.path.join(module_dir, '02-why-python.md'), 'w') as f:
            f.write(
                '---\n'
                'title: Why Python\n'
                'slug: why-python\n'
                f'content_id: "{uuid.uuid4()}"\n'
                '---\n'
                '# A short tour of Python\n\n'
                'Python is a popular language.\n'
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_matching_unit_h1_is_stripped_after_sync(self):
        sync_content_source(self.source, repo_dir=self.temp_dir)
        unit = Unit.objects.get(slug='running-examples')
        self.assertNotIn('<h1>Running examples</h1>', unit.body_html)
        self.assertIn(
            'This course uses two running examples.', unit.body_html,
        )

    def test_non_matching_unit_h1_survives_sync(self):
        sync_content_source(self.source, repo_dir=self.temp_dir)
        unit = Unit.objects.get(slug='why-python')
        self.assertIn('<h1>A short tour of Python</h1>', unit.body_html)
