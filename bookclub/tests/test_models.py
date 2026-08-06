"""Model behaviour for Book Club (issue #1362)."""

from datetime import date

from django.test import TestCase

from bookclub.models import Book
from content.access import LEVEL_MAIN


class BookModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )

    def test_get_absolute_url_points_at_books_slug(self):
        self.assertEqual(
            self.book.get_absolute_url(), '/books/inference-engineering',
        )

    def test_str_is_title(self):
        self.assertEqual(str(self.book), 'Inference Engineering')

    def test_required_level_display_uses_content_access_labels(self):
        # required_level draws its choices from content.access.VISIBILITY_CHOICES.
        self.assertEqual(self.book.get_required_level_display(), 'Main and above')
