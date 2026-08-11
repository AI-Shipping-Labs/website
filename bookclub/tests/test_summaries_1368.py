"""Book Club summaries: models, helpers, and the two render surfaces (#1368).

Covers the summary publish-state model rule, the plain-text paragraph split,
the server-side notes-pull digest, the chapter ``#summary`` render (published /
draft / placeholder), and the full-book ``/summary`` page across every member
state plus tier gating. Dates derive from ``date.today()`` so nothing rots.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from bookclub.models import Book, Chapter, Note
from bookclub.summaries import (
    append_notes_digest,
    build_notes_digest,
    resolve_publish_state,
    summary_excerpt,
    summary_paragraphs,
)
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class SummaryModelStateTest(TestCase):
    def test_published_requires_timestamp_and_non_empty_body(self):
        book = Book.objects.create(
            title='B', slug='b', author='A', required_level=LEVEL_MAIN,
        )
        # No timestamp -> draft.
        book.summary = 'Real body.'
        self.assertFalse(book.is_summary_published)
        # Timestamp + non-empty -> published.
        book.summary_published_at = timezone.now()
        self.assertTrue(book.is_summary_published)
        # Timestamp but empty body -> treated as NOT published (guard).
        book.summary = '   '
        self.assertFalse(book.is_summary_published)

    def test_chapter_summary_state_mirrors_book(self):
        book = Book.objects.create(
            title='B', slug='b', author='A', required_level=LEVEL_MAIN,
        )
        chapter = Chapter.objects.create(book=book, number=0, title='One')
        self.assertFalse(chapter.is_summary_published)
        chapter.summary = 'Body'
        chapter.summary_published_at = timezone.now()
        self.assertTrue(chapter.is_summary_published)


@tag("core")
class SummaryHelperTest(TestCase):
    def test_paragraphs_split_on_blank_lines(self):
        text = 'First para.\n\nSecond para.\n\n\nThird.'
        self.assertEqual(
            summary_paragraphs(text),
            ['First para.', 'Second para.', 'Third.'],
        )

    def test_paragraphs_empty_body_yields_empty_list(self):
        self.assertEqual(summary_paragraphs(''), [])
        self.assertEqual(summary_paragraphs('   \n\n  '), [])

    def test_excerpt_truncates_first_paragraph(self):
        text = 'x' * 300 + '\n\nsecond'
        excerpt = summary_excerpt(text, limit=50)
        self.assertTrue(excerpt.endswith('…'))
        self.assertLessEqual(len(excerpt), 50)

    def test_resolve_publish_sets_timestamp_only_when_unpublished(self):
        # Publish from draft: sets a timestamp.
        new_at, empty = resolve_publish_state(None, 'body', True)
        self.assertIsNotNone(new_at)
        self.assertFalse(empty)
        # Idempotent: an already-set timestamp is preserved (not bumped).
        existing = timezone.now() - timedelta(days=3)
        new_at, empty = resolve_publish_state(existing, 'body', True)
        self.assertEqual(new_at, existing)
        # Unpublish clears it.
        new_at, empty = resolve_publish_state(existing, 'body', False)
        self.assertIsNone(new_at)
        # Empty publish is rejected.
        new_at, empty = resolve_publish_state(None, '   ', True)
        self.assertTrue(empty)


@tag("core")
class NotesDigestTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.book = Book.objects.create(
            title='B', slug='b', author='A', required_level=LEVEL_MAIN,
            status='current',
        )
        cls.chapter = Chapter.objects.create(book=cls.book, number=0, title='One')
        cls.empty_chapter = Chapter.objects.create(
            book=cls.book, number=1, title='Two',
        )
        for i, name in enumerate(['Ada', 'Lin', 'Sam']):
            user = User.objects.create_user(
                email=f'{name.lower()}@test.com', password='pw',
                first_name=name,
            )
            user.tier = cls.main_tier
            user.save()
            Note.objects.create(
                chapter=cls.chapter, user=user, body=f'{name} takeaway body.',
            )

    def test_build_digest_lists_every_member_note(self):
        digest, count = build_notes_digest(self.chapter)
        self.assertEqual(count, 3)
        self.assertIn('3 notes', digest)
        for name in ['Ada', 'Lin', 'Sam']:
            self.assertIn(f'- {name}: {name} takeaway body.', digest)

    def test_build_digest_no_notes_returns_none(self):
        digest, count = build_notes_digest(self.empty_chapter)
        self.assertIsNone(digest)
        self.assertEqual(count, 0)

    def test_append_is_non_destructive_and_leaves_publish_untouched(self):
        self.chapter.summary = 'Organizer draft text.'
        self.chapter.save(update_fields=['summary'])
        count = append_notes_digest(self.chapter)
        self.chapter.refresh_from_db()
        self.assertEqual(count, 3)
        # Existing draft preserved, digest appended under a separator.
        self.assertTrue(self.chapter.summary.startswith('Organizer draft text.'))
        self.assertIn('----', self.chapter.summary)
        self.assertIn('- Ada: Ada takeaway body.', self.chapter.summary)
        # Never publishes.
        self.assertIsNone(self.chapter.summary_published_at)

    def test_append_twice_keeps_both_digests(self):
        append_notes_digest(self.chapter)
        first = Chapter.objects.get(pk=self.chapter.pk).summary
        append_notes_digest(self.chapter)
        second = Chapter.objects.get(pk=self.chapter.pk).summary
        self.assertEqual(second.count('----'), 1)
        self.assertTrue(second.startswith(first))
        self.assertGreater(second.count('- Ada:'), 1)

    def test_append_no_notes_writes_nothing(self):
        count = append_notes_digest(self.empty_chapter)
        self.empty_chapter.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(self.empty_chapter.summary, '')


class SummaryRenderFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )
        # Ch0 deadline in the past so the placeholder is meaningful.
        cls.ch0 = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
            deadline=today - timedelta(days=7),
        )
        cls.ch1 = Chapter.objects.create(
            book=cls.book, number=1, title='Prerequisites',
            deadline=today + timedelta(days=7),
        )
        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_tier = Tier.objects.get(slug='main')
        cls.member = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.member.tier = cls.main_tier
        cls.member.save()
        cls.free_user = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def _chapter_url(self, number):
        return f'/books/inference-engineering/chapters/{number}'

    def _summary_url(self):
        return '/books/inference-engineering/summary'


@tag("core")
class ChapterSummaryRenderTest(SummaryRenderFixture):
    def _publish_ch0(self):
        self.ch0.summary = 'The KV cache framing clicked.\n\nSecond point.'
        self.ch0.summary_published_at = timezone.now()
        self.ch0.save()

    def test_published_summary_shows_to_member(self):
        self._publish_ch0()
        self.client.force_login(self.member)
        response = self.client.get(self._chapter_url(0))
        self.assertContains(response, 'chapter-summary')
        self.assertContains(response, 'The KV cache framing clicked.')
        self.assertContains(response, 'Published')
        self.assertNotContains(response, 'chapter-summary-draft-chip')

    def test_draft_summary_hidden_from_member_shown_to_staff(self):
        self.ch0.summary = 'Secret draft body.'
        self.ch0.summary_published_at = None
        self.ch0.save()
        # Member does not see the draft body.
        self.client.force_login(self.member)
        response = self.client.get(self._chapter_url(0))
        self.assertNotContains(response, 'Secret draft body.')
        self.assertNotContains(response, 'chapter-summary-draft-chip')
        # Staff previews it with a Draft chip.
        self.client.force_login(self.staff)
        response = self.client.get(self._chapter_url(0))
        self.assertContains(response, 'chapter-summary-draft-chip')
        self.assertContains(response, 'Secret draft body.')

    def test_placeholder_only_after_deadline(self):
        self.client.force_login(self.member)
        # Ch0 deadline is in the past -> placeholder shows.
        response = self.client.get(self._chapter_url(0))
        self.assertContains(response, 'chapter-summary-placeholder')
        # Ch1 deadline is in the future -> nothing (no premature promise).
        response = self.client.get(self._chapter_url(1))
        self.assertNotContains(response, 'chapter-summary-placeholder')
        self.assertNotContains(response, 'chapter-summary')

    def test_publish_state_gates_member_visibility(self):
        self.ch0.summary = 'Body text visible only when published.'
        self.ch0.save()
        self.client.force_login(self.member)
        response = self.client.get(self._chapter_url(0))
        self.assertNotContains(
            response, 'Body text visible only when published.',
        )
        self.ch0.summary_published_at = timezone.now()
        self.ch0.save()
        response = self.client.get(self._chapter_url(0))
        self.assertContains(response, 'Body text visible only when published.')

    def test_no_comment_leak(self):
        self._publish_ch0()
        self.client.force_login(self.member)
        response = self.client.get(self._chapter_url(0))
        self.assertNotContains(response, '{#')


@tag("core")
class BookSummaryPageTest(SummaryRenderFixture):
    def _publish_overall(self):
        self.book.summary = 'Overall theme one.\n\nOverall theme two.'
        self.book.summary_published_at = timezone.now()
        self.book.save()

    def _publish_chapter(self, chapter, body):
        chapter.summary = body
        chapter.summary_published_at = timezone.now()
        chapter.save()

    def test_member_sees_overall_and_by_chapter_list(self):
        self._publish_overall()
        self._publish_chapter(self.ch0, 'Ch0 takeaway line.')
        self._publish_chapter(self.ch1, 'Ch1 takeaway line.')
        self.client.force_login(self.member)
        response = self.client.get(self._summary_url())
        self.assertContains(response, 'summary-overall')
        self.assertContains(response, 'Overall theme one.')
        self.assertContains(response, 'summary-by-chapter')
        self.assertContains(response, 'Ch. 0 — Inference')
        self.assertContains(response, 'Ch. 1 — Prerequisites')
        # Rows link to the chapter #summary anchor.
        self.assertContains(response, f'{self._chapter_url(0)}#summary')

    def test_mid_book_shows_published_so_far_index(self):
        # Overall not published, but Ch0 summary published.
        self._publish_chapter(self.ch0, 'Ch0 takeaway line.')
        self.client.force_login(self.member)
        response = self.client.get(self._summary_url())
        self.assertContains(response, 'summary-in-progress')
        self.assertContains(
            response, 'The full summary is compiled after we finish the book.',
        )
        self.assertContains(response, 'summary-published-so-far')
        self.assertContains(response, f'{self._chapter_url(0)}#summary')
        # No broken Overall section.
        self.assertNotContains(response, 'summary-overall')

    def test_nothing_published_shows_empty_state(self):
        self.client.force_login(self.member)
        response = self.client.get(self._summary_url())
        self.assertContains(response, 'summary-in-progress')
        self.assertNotContains(response, 'summary-overall')
        self.assertNotContains(response, 'summary-published-so-far')
        # A calm empty state, not a blank section.
        self.assertContains(response, 'No summary yet')

    def test_free_member_is_gated(self):
        self._publish_overall()
        self.client.force_login(self.free_user)
        response = self.client.get(self._summary_url())
        self.assertContains(response, 'book-guest-gate')
        self.assertContains(response, '/membership')
        self.assertNotContains(response, 'Overall theme one.')
        self.assertNotContains(response, 'summary-overall')

    def test_staff_previews_unpublished_overall_with_chip(self):
        self.book.summary = 'Draft overall body preview.'
        self.book.summary_published_at = None
        self.book.save()
        self.client.force_login(self.staff)
        response = self.client.get(self._summary_url())
        self.assertContains(response, 'summary-draft-chip')
        self.assertContains(response, 'Draft overall body preview.')
        # A member does NOT see the draft overall.
        self.client.force_login(self.member)
        response = self.client.get(self._summary_url())
        self.assertNotContains(response, 'Draft overall body preview.')

    def test_draft_book_summary_404s_for_non_staff(self):
        self.book.status = 'draft'
        self.book.save()
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self._summary_url()).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self._summary_url()).status_code, 200)

    def test_no_comment_leak_member_and_gated(self):
        self._publish_overall()
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(self._summary_url()), '{#')
        self.client.force_login(self.free_user)
        self.assertNotContains(self.client.get(self._summary_url()), '{#')
