"""Tests for sort_order derivation and slug-based URLs in course sync - issue #143.

Covers:
- extract_sort_order: numeric prefix extraction from filenames/directory names
- derive_slug: slug derivation from filenames/directory names
- sort_order removed from REQUIRED_FIELDS for module and unit
- Module and Unit slug fields
- Slug-based unit URLs
- Old numeric URLs return 404
"""

from django.test import Client, TestCase

from content.models import Course, Module, Unit
from integrations.services.github import (
    REQUIRED_FIELDS,
    derive_slug,
    extract_sort_order,
)


class ExtractSortOrderTest(TestCase):
    """Test extract_sort_order helper function."""

    def test_numeric_prefix(self):
        self.assertEqual(extract_sort_order('01-day-1'), 1)

    def test_two_digit_prefix(self):
        self.assertEqual(extract_sort_order('02-setup.md'), 2)

    def test_large_prefix(self):
        self.assertEqual(extract_sort_order('99-final.md'), 99)

    def test_no_prefix_returns_zero(self):
        self.assertEqual(extract_sort_order('intro.md'), 0)

    def test_no_prefix_directory(self):
        self.assertEqual(extract_sort_order('appendix'), 0)

    def test_leading_zeros(self):
        self.assertEqual(extract_sort_order('001-advanced'), 1)

    def test_zero_prefix(self):
        self.assertEqual(extract_sort_order('00-intro'), 0)


class DeriveSlugTest(TestCase):
    """Test derive_slug helper function.

    ``derive_slug`` strips an optional ``NN-`` numeric prefix and the
    optional ``.md`` extension. The behaviour is a single regex match,
    not branching code, so the per-shape tests collapse into one
    ``subTest``-parameterized table.
    """

    def test_derive_slug_table(self):
        cases = [
            # (filename_or_dir, expected_slug, why)
            ('01-day-1', 'day-1', 'numeric prefix stripped from directory'),
            ('02-environment.md', 'environment',
             'numeric prefix and .md extension stripped from filename'),
            ('lesson.md', 'lesson',
             'no numeric prefix on filename — only .md stripped'),
            ('appendix', 'appendix',
             'no numeric prefix on directory — name kept verbatim'),
            ('123-advanced-topics', 'advanced-topics',
             'multi-digit prefix stripped'),
            ('01-introduction', 'introduction',
             'numeric prefix stripped even without extension'),
        ]
        for name, expected, label in cases:
            with self.subTest(name=name, why=label):
                self.assertEqual(derive_slug(name), expected)


class RequiredFieldsTest(TestCase):
    """Test that sort_order is no longer required for module and unit."""

    def test_module_required_fields_no_sort_order(self):
        self.assertNotIn('sort_order', REQUIRED_FIELDS['module'])
        self.assertIn('title', REQUIRED_FIELDS['module'])

    def test_unit_required_fields_no_sort_order(self):
        self.assertNotIn('sort_order', REQUIRED_FIELDS['unit'])
        self.assertIn('title', REQUIRED_FIELDS['unit'])


class ModuleSlugFieldTest(TestCase):
    """Test Module slug field."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Test Course', slug='test-course')

    def test_module_has_slug(self):
        module = Module.objects.create(
            course=self.course, title='Intro', slug='intro', sort_order=0,
        )
        self.assertEqual(module.slug, 'intro')

    def test_module_slug_unique_per_course(self):
        from django.db import IntegrityError
        Module.objects.create(
            course=self.course, title='M1', slug='same-slug', sort_order=0,
        )
        with self.assertRaises(IntegrityError):
            Module.objects.create(
                course=self.course, title='M2', slug='same-slug', sort_order=1,
            )


class UnitSlugFieldTest(TestCase):
    """Test Unit slug field."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Test Course', slug='test-slug-course')
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=0,
        )

    def test_unit_has_slug(self):
        unit = Unit.objects.create(
            module=self.module, title='Lesson', slug='lesson', sort_order=0,
        )
        self.assertEqual(unit.slug, 'lesson')

    def test_unit_slug_unique_per_module(self):
        from django.db import IntegrityError
        Unit.objects.create(
            module=self.module, title='U1', slug='same-slug', sort_order=0,
        )
        with self.assertRaises(IntegrityError):
            Unit.objects.create(
                module=self.module, title='U2', slug='same-slug', sort_order=1,
            )


class SlugBasedURLTest(TestCase):
    """Test slug-based unit URLs."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='URL Course', slug='url-course', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Day 1', slug='day-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Intro', slug='intro', sort_order=1,
            is_preview=True, body='Content here.',
        )

    def test_get_absolute_url_uses_slugs(self):
        self.assertEqual(
            self.unit.get_absolute_url(),
            '/courses/url-course/day-1/intro',
        )

    def test_slug_url_returns_200(self):
        client = Client()
        response = client.get('/courses/url-course/day-1/intro')
        self.assertEqual(response.status_code, 200)

    def test_old_numeric_url_returns_404(self):
        client = Client()
        response = client.get('/courses/url-course/1/1')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_module_slug_returns_404(self):
        client = Client()
        response = client.get('/courses/url-course/nonexistent/intro')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_unit_slug_returns_404(self):
        client = Client()
        response = client.get('/courses/url-course/day-1/nonexistent')
        self.assertEqual(response.status_code, 404)
