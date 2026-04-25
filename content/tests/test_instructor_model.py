"""Tests for the Instructor model (issue #308).

Covers behavior we own — markdown rendering on save, ordering by name,
and slug uniqueness — without re-testing Django's ORM defaults.
"""
from django.db import IntegrityError
from django.test import TestCase

from content.models import Instructor


class InstructorBioRenderingTest(TestCase):
    """``Instructor.save`` must render ``bio`` markdown into ``bio_html``."""

    def test_bio_html_rendered_from_markdown_on_save(self):
        instructor = Instructor.objects.create(
            instructor_id='alexey-grigorev',
            name='Alexey Grigorev',
            bio='# Hello\n\nAI/ML engineer.',
        )
        # The rendered HTML should contain a real ``<h1>`` element, not
        # a string-equal copy of the markdown.
        self.assertIn('<h1>Hello</h1>', instructor.bio_html)
        self.assertIn('AI/ML engineer.', instructor.bio_html)

    def test_empty_bio_yields_empty_bio_html(self):
        instructor = Instructor.objects.create(
            instructor_id='no-bio',
            name='No Bio',
            bio='',
        )
        self.assertEqual(instructor.bio_html, '')

    def test_bio_html_re_renders_when_bio_updated(self):
        instructor = Instructor.objects.create(
            instructor_id='change-me',
            name='Change Me',
            bio='Original.',
        )
        instructor.bio = '## New heading'
        instructor.save()
        self.assertIn('<h2>New heading</h2>', instructor.bio_html)
        self.assertNotIn('Original', instructor.bio_html)


class InstructorOrderingTest(TestCase):
    """Default queryset ordering is alphabetical by ``name``."""

    def test_default_queryset_ordered_by_name(self):
        Instructor.objects.create(instructor_id='zoe', name='Zoe')
        Instructor.objects.create(instructor_id='alice', name='Alice')
        Instructor.objects.create(instructor_id='mark', name='Mark')

        names = list(Instructor.objects.values_list('name', flat=True))
        self.assertEqual(names, ['Alice', 'Mark', 'Zoe'])


class InstructorSlugUniquenessTest(TestCase):
    """``instructor_id`` must be unique across the table."""

    def test_duplicate_instructor_id_raises(self):
        Instructor.objects.create(
            instructor_id='alexey-grigorev', name='Alexey Grigorev',
        )
        with self.assertRaises(IntegrityError):
            Instructor.objects.create(
                instructor_id='alexey-grigorev', name='Someone Else',
            )
