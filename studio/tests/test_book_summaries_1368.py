"""Studio authoring for Book Club summaries + notes-pull helper (issue #1368).

Covers the full-book summary textarea + publish toggle on the book form, the
per-chapter summary + publish toggle on the inline chapter surface, the
empty-publish guard on both, and the "Pull all notes" server-side aggregation
button.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from bookclub.models import Book, Chapter, Note
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class BookSummaryStudioTest(TestCase):
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

    def _base_payload(self, **overrides):
        data = {
            'title': self.book.title, 'slug': self.book.slug,
            'author': self.book.author, 'required_level': str(LEVEL_MAIN),
            'status': 'current',
        }
        data.update(overrides)
        return data

    def _edit(self, **overrides):
        return self.client.post(
            f'/studio/books/{self.book.pk}/edit', self._base_payload(**overrides),
        )

    def test_form_shows_summary_field_and_toggle(self):
        response = self.client.get(f'/studio/books/{self.book.pk}/edit')
        self.assertContains(response, 'book-summary')
        self.assertContains(response, 'book-summary-published')

    def test_publish_sets_timestamp_idempotently(self):
        response = self._edit(summary='Overall body.', summary_published='1')
        self.assertEqual(response.status_code, 302)
        self.book.refresh_from_db()
        self.assertTrue(self.book.is_summary_published)
        first_at = self.book.summary_published_at
        # Re-save while still published: timestamp is not bumped.
        self._edit(summary='Overall body edited.', summary_published='1')
        self.book.refresh_from_db()
        self.assertEqual(self.book.summary_published_at, first_at)

    def test_unpublish_clears_timestamp(self):
        self._edit(summary='Body.', summary_published='1')
        self._edit(summary='Body.')  # checkbox absent -> unpublish
        self.book.refresh_from_db()
        self.assertIsNone(self.book.summary_published_at)

    def test_empty_publish_is_rejected(self):
        response = self._edit(summary='', summary_published='1')
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'Write the summary before publishing', status_code=400,
        )
        self.book.refresh_from_db()
        self.assertIsNone(self.book.summary_published_at)


@tag("core")
class ChapterSummaryStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.main_tier = Tier.objects.get(slug='main')
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
        )
        cls.empty_chapter = Chapter.objects.create(
            book=cls.book, number=1, title='Prerequisites',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _edit_chapter(self, chapter, **overrides):
        data = {'number': str(chapter.number), 'title': chapter.title}
        data.update(overrides)
        return self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{chapter.pk}/edit', data,
        )

    def test_detail_shows_summary_controls_and_pull_button(self):
        response = self.client.get(f'/studio/books/{self.book.pk}/')
        self.assertContains(response, 'book-chapter-summary')
        self.assertContains(response, 'book-chapter-summary-published')
        self.assertContains(response, 'book-chapter-pull-notes')

    def test_publish_chapter_summary(self):
        self._edit_chapter(
            self.chapter, summary='Ch summary.', summary_published='1',
        )
        self.chapter.refresh_from_db()
        self.assertTrue(self.chapter.is_summary_published)

    def test_empty_publish_chapter_is_rejected(self):
        response = self._edit_chapter(
            self.chapter, summary='', summary_published='1',
        )
        # Redirects with a session error (P/R/G), draft untouched.
        self.assertEqual(response.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertIsNone(self.chapter.summary_published_at)
        follow = self.client.get(f'/studio/books/{self.book.pk}/')
        self.assertContains(follow, 'Write the summary before publishing')

    def _make_notes(self):
        for name in ['Ada', 'Lin', 'Sam']:
            user = User.objects.create_user(
                email=f'{name.lower()}@test.com', password='pw', first_name=name,
            )
            user.tier = self.main_tier
            user.save()
            Note.objects.create(
                chapter=self.chapter, user=user, body=f'{name} note body.',
            )

    def test_pull_notes_seeds_draft(self):
        self._make_notes()
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/pull-notes',
        )
        self.assertEqual(response.status_code, 302)
        self.chapter.refresh_from_db()
        self.assertIn('- Ada: Ada note body.', self.chapter.summary)
        self.assertIsNone(self.chapter.summary_published_at)
        follow = self.client.get(f'/studio/books/{self.book.pk}/')
        self.assertContains(follow, 'Pulled 3 notes')

    def test_pull_notes_is_non_destructive(self):
        self.chapter.summary = 'Existing draft.'
        self.chapter.save(update_fields=['summary'])
        self._make_notes()
        self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/pull-notes',
        )
        self.chapter.refresh_from_db()
        self.assertTrue(self.chapter.summary.startswith('Existing draft.'))
        self.assertIn('----', self.chapter.summary)

    def test_pull_notes_no_notes_reports_info(self):
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.empty_chapter.pk}/pull-notes',
        )
        self.assertEqual(response.status_code, 302)
        self.empty_chapter.refresh_from_db()
        self.assertEqual(self.empty_chapter.summary, '')
        follow = self.client.get(f'/studio/books/{self.book.pk}/')
        self.assertContains(follow, 'No notes to pull yet')

    def test_pull_notes_requires_staff(self):
        self.client.logout()
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/pull-notes',
        )
        self.assertIn(response.status_code, (302, 403))
        # An authenticated non-staff member is forbidden.
        member = User.objects.create_user(email='m@test.com', password='pw')
        member.tier = self.main_tier
        member.save()
        self.client.login(email='m@test.com', password='pw')
        response = self.client.post(
            f'/studio/books/{self.book.pk}/chapters/{self.chapter.pk}/pull-notes',
        )
        self.assertEqual(response.status_code, 403)
