"""ChapterRead model + derived progress helper (issue #1364)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from bookclub.models import Book, Chapter, ChapterRead
from bookclub.reading import viewer_reading_progress
from content.access import LEVEL_MAIN

User = get_user_model()


@tag("core")
class ChapterReadModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title="Inference Engineering", slug="inference-engineering",
            author="Philip Kiely", required_level=LEVEL_MAIN,
            status="current", start_date=date(2026, 8, 10),
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=0, title="Inference",
        )
        cls.user = User.objects.create_user(email="reader@test.com")

    def test_get_or_create_is_idempotent_under_unique_constraint(self):
        row, created = ChapterRead.objects.get_or_create(
            user=self.user, chapter=self.chapter,
        )
        self.assertTrue(created)
        again, created_again = ChapterRead.objects.get_or_create(
            user=self.user, chapter=self.chapter,
        )
        self.assertFalse(created_again)
        self.assertEqual(row.pk, again.pk)
        self.assertEqual(
            ChapterRead.objects.filter(
                user=self.user, chapter=self.chapter,
            ).count(),
            1,
        )

    def test_deleting_the_row_is_the_unmark(self):
        ChapterRead.objects.get_or_create(user=self.user, chapter=self.chapter)
        ChapterRead.objects.filter(
            user=self.user, chapter=self.chapter,
        ).delete()
        self.assertFalse(
            ChapterRead.objects.filter(
                user=self.user, chapter=self.chapter,
            ).exists(),
        )


@tag("core")
class ViewerReadingProgressTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title="Inference Engineering", slug="inference-engineering",
            author="Philip Kiely", required_level=LEVEL_MAIN,
            status="current", start_date=date(2026, 8, 10),
        )
        cls.chapters = [
            Chapter.objects.create(book=cls.book, number=i, title=f"Ch {i}")
            for i in range(5)
        ]
        cls.user = User.objects.create_user(email="reader@test.com")

    def test_progress_reflects_read_rows(self):
        ChapterRead.objects.create(user=self.user, chapter=self.chapters[0])
        ChapterRead.objects.create(user=self.user, chapter=self.chapters[2])
        progress = viewer_reading_progress(self.user, self.book)
        self.assertEqual(progress["total"], 5)
        self.assertEqual(progress["done"], 2)
        self.assertEqual(progress["pct"], 40)
        self.assertEqual(progress["read_numbers"], {0, 2})

    def test_anonymous_viewer_gets_empty_progress(self):
        from django.contrib.auth.models import AnonymousUser

        progress = viewer_reading_progress(AnonymousUser(), self.book)
        self.assertEqual(progress["done"], 0)
        self.assertEqual(progress["pct"], 0)
        self.assertEqual(progress["read_numbers"], set())

    def test_read_set_is_a_single_query_no_n_plus_one(self):
        for chapter in self.chapters:
            ChapterRead.objects.create(user=self.user, chapter=chapter)
        chapters = list(self.book.chapters.all())
        # One query for the read set, regardless of chapter count.
        with self.assertNumQueries(1):
            progress = viewer_reading_progress(self.user, self.book, chapters)
        self.assertEqual(progress["done"], 5)
        self.assertEqual(progress["pct"], 100)

    def test_progress_is_private_to_the_viewer(self):
        other = User.objects.create_user(email="other@test.com")
        ChapterRead.objects.create(user=self.user, chapter=self.chapters[0])
        ChapterRead.objects.create(user=self.user, chapter=self.chapters[1])
        ChapterRead.objects.create(user=other, chapter=self.chapters[0])
        self.assertEqual(viewer_reading_progress(self.user, self.book)["done"], 2)
        self.assertEqual(viewer_reading_progress(other, self.book)["done"], 1)
