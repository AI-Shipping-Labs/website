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

    def test_bio_html_linkifies_bare_url(self):
        instructor = Instructor.objects.create(
            instructor_id='linked-bio',
            name='Linked Bio',
            bio='Profile: https://example.com/instructor',
        )
        self.assertIn(
            '<a href="https://example.com/instructor" target="_blank" '
            'rel="noopener noreferrer">https://example.com/instructor</a>',
            instructor.bio_html,
        )

    def test_markdown_link_is_not_double_linked(self):
        instructor = Instructor.objects.create(
            instructor_id='markdown-link-bio',
            name='Markdown Link Bio',
            bio='Profile: [site](https://example.com/instructor)',
        )
        self.assertEqual(instructor.bio_html.count('<a '), 1)
        self.assertIn('href="https://example.com/instructor"', instructor.bio_html)

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

    def test_save_update_fields_bio_keeps_bio_html_fresh(self):
        instructor = Instructor.objects.create(
            instructor_id='update-fields-bio',
            name='Update Fields Bio',
            bio='Original.',
        )
        instructor.bio = 'Updated: https://example.com/new-profile'
        instructor.save(update_fields=['bio'])
        instructor.refresh_from_db()
        self.assertIn('href="https://example.com/new-profile"', instructor.bio_html)
        self.assertNotIn('Original', instructor.bio_html)


# ``InstructorOrderingTest::test_default_queryset_ordered_by_name``
# previously asserted on ``Meta.ordering`` — Django framework
# behaviour, removed per ``_docs/testing-guidelines.md`` Rule 3.


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
