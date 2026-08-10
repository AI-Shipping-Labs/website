"""Studio admin views for Book Club books and chapters (issue #1362)."""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN

User = get_user_model()


class BookStudioAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_book_list_requires_staff(self):
        response = self.client.get('/studio/books/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/books/')
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/books/')
        self.assertEqual(response.status_code, 200)

    def test_dashboard_nav_links_to_book_club(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/studio/books/')
        self.assertContains(response, 'Book club')


class BookStudioCrudTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_book_derives_slug_and_lands_on_detail(self):
        response = self.client.post('/studio/books/new', {
            'title': 'Inference Engineering',
            'author': 'Philip Kiely',
            'required_level': str(LEVEL_MAIN),
            'status': 'current',
            'start_date': '2026-08-10',
        })
        book = Book.objects.get(title='Inference Engineering')
        self.assertEqual(book.slug, 'inference-engineering')
        self.assertEqual(book.required_level, LEVEL_MAIN)
        self.assertEqual(book.status, 'current')
        self.assertEqual(book.start_date, date(2026, 8, 10))
        self.assertRedirects(response, f'/studio/books/{book.pk}/')

    def test_create_rejects_duplicate_slug_with_friendly_error(self):
        Book.objects.create(
            title='First', slug='inference-engineering', author='A',
            required_level=LEVEL_MAIN,
        )
        response = self.client.post('/studio/books/new', {
            'title': 'Inference Engineering',
            'slug': 'inference-engineering',
            'author': 'Philip Kiely',
            'required_level': str(LEVEL_MAIN),
            'status': 'draft',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(Book.objects.filter(slug='inference-engineering').count(), 1)

    def test_edit_book_updates_fields(self):
        book = Book.objects.create(
            title='Old', slug='the-book', author='A', required_level=LEVEL_MAIN,
            status='draft',
        )
        response = self.client.post(f'/studio/books/{book.pk}/edit', {
            'title': 'New Title',
            'slug': 'the-book',
            'author': 'B',
            'required_level': str(LEVEL_MAIN),
            'status': 'current',
        })
        self.assertRedirects(response, f'/studio/books/{book.pk}/')
        book.refresh_from_db()
        self.assertEqual(book.title, 'New Title')
        self.assertEqual(book.status, 'current')

    def test_delete_book_removes_book_and_chapters(self):
        book = Book.objects.create(
            title='B', slug='b', author='A', required_level=LEVEL_MAIN,
        )
        Chapter.objects.create(book=book, number=0, title='Ch0')
        response = self.client.post(f'/studio/books/{book.pk}/delete')
        self.assertRedirects(response, '/studio/books/')
        self.assertFalse(Book.objects.filter(pk=book.pk).exists())
        self.assertFalse(Chapter.objects.filter(book_id=book.pk).exists())


class BookStudioChapterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_add_chapter_with_deadline(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/add',
            {'number': '0', 'title': 'Inference', 'deadline': '2026-08-17',
             'week_label': 'Week 1'},
        )
        self.assertRedirects(response, f'/studio/books/{self.book.pk}/')
        chapter = Chapter.objects.get(book=self.book, number=0)
        self.assertEqual(chapter.title, 'Inference')
        self.assertEqual(chapter.deadline, date(2026, 8, 17))
        self.assertEqual(chapter.week_label, 'Week 1')

    def test_add_chapter_with_week_number(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/add',
            {'number': '0', 'title': 'Inference', 'week_number': '1'},
        )
        self.assertRedirects(response, f'/studio/books/{self.book.pk}/')
        chapter = Chapter.objects.get(book=self.book, number=0)
        self.assertEqual(chapter.week_number, 1)

    def test_edit_chapter_week_number_and_clear(self):
        chapter = Chapter.objects.create(
            book=self.book, number=0, title='Inference', week_number=1,
        )
        # Set to week 2.
        self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{chapter.pk}/edit',
            {'number': '0', 'title': 'Inference', 'week_number': '2'},
        )
        chapter.refresh_from_db()
        self.assertEqual(chapter.week_number, 2)
        # Blank clears the grouping.
        self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{chapter.pk}/edit',
            {'number': '0', 'title': 'Inference', 'week_number': ''},
        )
        chapter.refresh_from_db()
        self.assertIsNone(chapter.week_number)

    def test_invalid_week_number_shows_friendly_error(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/add',
            {'number': '0', 'title': 'Inference', 'week_number': 'x'}, follow=True,
        )
        self.assertContains(response, 'Week number must be a whole number.')
        self.assertEqual(Chapter.objects.filter(book=self.book).count(), 0)

    def test_duplicate_chapter_number_shows_friendly_error(self):
        Chapter.objects.create(book=self.book, number=0, title='Inference')
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/add',
            {'number': '0', 'title': 'Duplicate'}, follow=True,
        )
        # Redirects back to the detail page where the friendly error surfaces.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has a chapter numbered 0')
        # No second chapter 0, no 500.
        self.assertEqual(Chapter.objects.filter(book=self.book, number=0).count(), 1)

    def test_edit_chapter_deadline(self):
        chapter = Chapter.objects.create(
            book=self.book, number=0, title='Inference', deadline=date(2026, 8, 17),
        )
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{chapter.pk}/edit',
            {'number': '0', 'title': 'Inference', 'deadline': '2026-08-20'},
        )
        self.assertRedirects(response, f'/studio/books/{self.book.pk}/')
        chapter.refresh_from_db()
        self.assertEqual(chapter.deadline, date(2026, 8, 20))

    def test_edit_rejects_number_collision(self):
        Chapter.objects.create(book=self.book, number=0, title='Zero')
        ch1 = Chapter.objects.create(book=self.book, number=1, title='One')
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{ch1.pk}/edit',
            {'number': '0', 'title': 'One'},
        )
        self.assertRedirects(response, f'/studio/books/{self.book.pk}/')
        ch1.refresh_from_db()
        self.assertEqual(ch1.number, 1)  # unchanged

    def test_delete_chapter(self):
        chapter = Chapter.objects.create(book=self.book, number=0, title='Inference')
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{chapter.pk}/delete',
        )
        self.assertRedirects(response, f'/studio/books/{self.book.pk}/')
        self.assertFalse(Chapter.objects.filter(pk=chapter.pk).exists())

    def test_reorder_renumbers_chapters(self):
        c0 = Chapter.objects.create(book=self.book, number=0, title='A')
        c1 = Chapter.objects.create(book=self.book, number=1, title='B')
        c2 = Chapter.objects.create(book=self.book, number=2, title='C')
        # New order: C, A, B
        payload = [
            {'id': c2.pk, 'number': 0},
            {'id': c0.pk, 'number': 1},
            {'id': c1.pk, 'number': 2},
        ]
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/reorder',
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        c0.refresh_from_db(); c1.refresh_from_db(); c2.refresh_from_db()
        self.assertEqual(c2.number, 0)
        self.assertEqual(c0.number, 1)
        self.assertEqual(c1.number, 2)
