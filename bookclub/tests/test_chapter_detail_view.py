"""Per-chapter reader page + own-note write endpoint (issue #1365)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from bookclub.models import (
    READER_VISIBILITY_PRIVATE,
    READER_VISIBILITY_PUBLIC,
    Book,
    Chapter,
    Note,
    ReaderProfile,
)
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


@tag("core")
class ChapterDetailFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )
        cls.ch0 = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
            deadline=date(2026, 8, 17), week_label='Week 1',
        )
        cls.ch1 = Chapter.objects.create(
            book=cls.book, number=1, title='Prerequisites',
        )
        cls.ch2 = Chapter.objects.create(
            book=cls.book, number=2, title='Architecture',
        )

        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_tier = Tier.objects.get(slug='main')

        cls.free_user = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

        cls.main_user = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

        cls.other_main = User.objects.create_user(
            email='main2@test.com', password='pw',
        )
        cls.other_main.tier = cls.main_tier
        cls.other_main.save()

    def _url(self, number):
        return f'/books/inference-engineering/chapters/{number}'


class ChapterDetailAccessTest(ChapterDetailFixture):
    def test_member_sees_participation_body(self):
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'chapter-participation-body')
        self.assertContains(response, 'own-note-composer')
        # Notes can be edited via the member API: hint + docs + settings links.
        self.assertContains(response, 'own-note-api-hint')
        self.assertContains(response, '/member-api/docs')
        self.assertContains(response, '/account/#api-keys')
        # No leaked multi-line Django comment markers on the new page.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')

    def test_guest_sees_header_and_single_gate_only(self):
        response = self.client.get(self._url(0))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ch. 0 — Inference')
        self.assertContains(response, 'chapter-access-badge')
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'chapter-participation-body')
        self.assertNotContains(response, 'own-note-composer')

    def test_free_member_below_tier_is_gated(self):
        self.client.force_login(self.free_user)
        response = self.client.get(self._url(0))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'chapter-participation-body')

    def test_unknown_chapter_number_404s(self):
        self.client.force_login(self.main_user)
        self.assertEqual(self.client.get(self._url(99)).status_code, 404)

    def test_draft_book_404s_for_non_staff(self):
        self.book.status = 'draft'
        self.book.save()
        self.client.force_login(self.main_user)
        self.assertEqual(self.client.get(self._url(0)).status_code, 404)


class ChapterPrevNextNavTest(ChapterDetailFixture):
    def test_first_chapter_has_no_prev_and_has_next(self):
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertNotContains(response, 'chapter-prev')
        self.assertContains(response, 'chapter-next')

    def test_middle_chapter_has_both(self):
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(1))
        self.assertContains(response, 'chapter-prev')
        self.assertContains(response, 'chapter-next')

    def test_last_chapter_links_to_full_book_summary_and_no_next(self):
        # #1368 flipped the deferred handoff copy into a real link to the
        # full-book summary route (no more dead link).
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(2))
        self.assertNotContains(response, 'chapter-next')
        self.assertContains(response, 'chapter-end-of-book')
        self.assertContains(response, 'Read the full book summary')
        self.assertContains(
            response, 'href="/books/inference-engineering/summary"',
        )


class ChapterNoteWriteTest(ChapterDetailFixture):
    def test_member_writes_one_note_and_updates_in_place(self):
        self.client.force_login(self.main_user)
        # First write.
        self.client.post(self._url(0) + '/note', {'body': 'First take'})
        notes = Note.objects.filter(user=self.main_user, chapter=self.ch0)
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.first().body, 'First take')

        # Re-submit updates the same row (no second note).
        self.client.post(self._url(0) + '/note', {'body': 'Better take'})
        notes = Note.objects.filter(user=self.main_user, chapter=self.ch0)
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.first().body, 'Better take')

    def test_empty_body_clears_the_note(self):
        self.client.force_login(self.main_user)
        self.client.post(self._url(0) + '/note', {'body': 'Something'})
        self.assertEqual(
            Note.objects.filter(user=self.main_user, chapter=self.ch0).count(), 1,
        )
        self.client.post(self._url(0) + '/note', {'body': '   '})
        self.assertEqual(
            Note.objects.filter(user=self.main_user, chapter=self.ch0).count(), 0,
        )

    def test_note_write_redirects_back_to_chapter(self):
        self.client.force_login(self.main_user)
        response = self.client.post(self._url(0) + '/note', {'body': 'x'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self._url(0))

    def test_first_save_marks_activated(self):
        self.client.force_login(self.main_user)
        self.assertFalse(self.main_user.account_activated)
        self.client.post(self._url(0) + '/note', {'body': 'x'})
        self.main_user.refresh_from_db()
        self.assertTrue(self.main_user.account_activated)

    def test_mermaid_fence_in_body_renders_to_diagram_div(self):
        # A ```mermaid fence in the note body renders to a client-side
        # ``div.mermaid`` in the sanitised body_html (no separate field).
        self.client.force_login(self.main_user)
        self.client.post(
            self._url(0) + '/note',
            {'body': 'My take.\n\n```mermaid\ngraph TD; A-->B\n```'},
        )
        note = Note.objects.get(user=self.main_user, chapter=self.ch0)
        self.assertIn('class="mermaid"', note.body_html)
        self.assertIn('graph TD; A--&gt;B', note.body_html)

    def test_edit_recomputes_body_html(self):
        # The edit path saves with update_fields={'body'}; body_html is derived
        # and must be recomputed, not left stale from the first save.
        self.client.force_login(self.main_user)
        self.client.post(self._url(0) + '/note', {'body': 'first'})
        self.client.post(self._url(0) + '/note', {'body': 'second take'})
        note = Note.objects.get(user=self.main_user, chapter=self.ch0)
        self.assertEqual(note.body, 'second take')
        self.assertIn('second take', note.body_html)
        self.assertNotIn('first', note.body_html)

    def test_body_html_strips_script(self):
        # Notes are member-authored and shown to every reader, so raw HTML is
        # sanitised out of the stored body_html.
        self.client.force_login(self.main_user)
        self.client.post(
            self._url(0) + '/note',
            {'body': 'hi <script>alert(1)</script> there'},
        )
        note = Note.objects.get(user=self.main_user, chapter=self.ch0)
        self.assertNotIn('<script>', note.body_html)

    def test_anonymous_write_redirects_to_login(self):
        response = self.client.post(self._url(0) + '/note', {'body': 'x'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response['Location'])
        self.assertEqual(Note.objects.count(), 0)

    def test_below_tier_write_forbidden(self):
        self.client.force_login(self.free_user)
        response = self.client.post(self._url(0) + '/note', {'body': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Note.objects.count(), 0)


class ChapterGroupFeedTest(ChapterDetailFixture):
    def test_feed_shows_public_notes_and_excludes_the_viewers_own(self):
        # #1366: another member's note appears only if their profile is public.
        # #1461: the viewer's own note is NOT repeated in the feed — the
        # own-note card above is the single canonical self surface.
        ReaderProfile.objects.create(
            user=self.other_main, visibility=READER_VISIBILITY_PUBLIC,
        )
        Note.objects.create(chapter=self.ch0, user=self.other_main, body='Theirs')
        Note.objects.create(chapter=self.ch0, user=self.main_user, body='Mine')
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertContains(response, 'Theirs')
        # "Mine" renders exactly once — in the own-note card, not the feed.
        self.assertContains(response, 'Mine', count=1)
        self.assertContains(response, 'data-testid="own-note-body-rendered"', count=1)
        self.assertContains(response, 'data-testid="note-body"', count=1)
        self.assertNotContains(response, 'note-you-chip')
        # The public author's name links to their reader profile (#1366).
        self.assertContains(response, 'note-author-link')
        # No leaked Django comment markers from the rendered note cards.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')

    def test_private_authors_note_hidden_from_others_own_note_shown(self):
        # A private, non-self author's note is excluded from the feed shown to
        # others; the viewer's own note is always shown to the viewer.
        ReaderProfile.objects.create(
            user=self.other_main, visibility=READER_VISIBILITY_PRIVATE,
        )
        Note.objects.create(chapter=self.ch0, user=self.other_main, body='Hidden')
        Note.objects.create(chapter=self.ch0, user=self.main_user, body='Mine')
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertNotContains(response, 'Hidden')
        self.assertContains(response, 'Mine')

    def test_reader_with_no_profile_is_public_by_default_in_feed(self):
        # No ReaderProfile row -> public default -> visible to other members.
        Note.objects.create(chapter=self.ch0, user=self.other_main, body='Ghost')
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertContains(response, 'Ghost')

    def test_empty_feed_renders_member_empty_state(self):
        self.client.force_login(self.main_user)
        response = self.client.get(self._url(0))
        self.assertContains(response, 'No notes yet')


class BookDetailChapterLinksTest(ChapterDetailFixture):
    def test_chapter_rows_link_to_chapter_page(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, 'book-chapter-link')
        self.assertContains(
            response, 'href="/books/inference-engineering/chapters/0"',
        )
        # The mark-read form on the row is still present.
        self.assertContains(response, 'book-chapter-mark-read')
