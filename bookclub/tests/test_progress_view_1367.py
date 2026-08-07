"""Progress board /books/<slug>/progress (issue #1367).

The calm, you-first roster of readers ordered by chapters read. Covers
routing, tier gating (no roster leak below the gate), you-first ordering,
count correctness, a fixed query budget (no N+1), the absence of every
gamification element, the header stat, and dead-link safety (reader-profile
route #1366 is not built, so names stay plain text).
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from bookclub.models import (
    READER_VISIBILITY_PRIVATE,
    READER_VISIBILITY_PUBLIC,
    Book,
    Chapter,
    ChapterRead,
    Note,
    ReaderProfile,
)
from bookclub.reading import build_reader_rows
from content.access import LEVEL_MAIN, LEVEL_OPEN
from payments.models import Tier

User = get_user_model()


def _mark_read(user, chapters, count):
    """Mark the first ``count`` of ``chapters`` read for ``user``."""
    for chapter in chapters[:count]:
        ChapterRead.objects.create(user=user, chapter=chapter)


def _set_visibility(user, visibility):
    """Set ``user``'s reader-profile visibility (issue #1366)."""
    ReaderProfile.objects.update_or_create(
        user=user, defaults={'visibility': visibility},
    )


class ProgressBoardTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )
        cls.chapters = [
            Chapter.objects.create(book=cls.book, number=n, title=f'Ch {n}')
            for n in range(5)
        ]
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')

        cls.viewer = cls._member('main@test.com')
        cls.free_user = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

    @classmethod
    def _member(cls, email, first_name='', visibility=READER_VISIBILITY_PUBLIC):
        # Board rows are opt-in (#1366): a reader must be public to be listed
        # by name to others, so the ordering/count fixtures default to public.
        user = User.objects.create_user(
            email=email, password='pw', first_name=first_name,
        )
        user.tier = cls.main_tier
        user.save()
        if visibility is not None:
            ReaderProfile.objects.create(user=user, visibility=visibility)
        return user

    @property
    def url(self):
        return reverse(
            'bookclub_book_progress', kwargs={'slug': self.book.slug},
        )


class ProgressRoutingTest(ProgressBoardTestMixin, TestCase):
    def test_route_resolves_and_renders_template_for_member(self):
        self.assertEqual(self.url, '/books/inference-engineering/progress')
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/progress.html')
        self.assertContains(response, 'progress-board')

    def test_unknown_slug_404s(self):
        self.client.force_login(self.viewer)
        response = self.client.get('/books/no-such-book/progress')
        self.assertEqual(response.status_code, 404)

    def test_draft_book_404s_for_non_staff_and_renders_for_staff(self):
        draft = Book.objects.create(
            title='Draft', slug='draft-book', author='Nobody',
            required_level=LEVEL_MAIN, status='draft',
        )
        Chapter.objects.create(book=draft, number=0, title='Ch 0')
        draft_url = f'/books/{draft.slug}/progress'

        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(draft_url).status_code, 404)

        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        self.assertEqual(self.client.get(draft_url).status_code, 200)

    def test_no_comment_syntax_leaks_into_page(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')


class ProgressGatingTest(ProgressBoardTestMixin, TestCase):
    def test_anonymous_sees_gate_and_no_roster(self):
        _mark_read(self.viewer, self.chapters, 3)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'book-guest-gate')
        # Reader identities never leak below the gate.
        self.assertNotContains(response, 'progress-board')
        self.assertNotContains(response, 'progress-row-')

    def test_below_tier_member_sees_gate_and_no_roster(self):
        _mark_read(self.viewer, self.chapters, 3)
        self.client.force_login(self.free_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'progress-board')
        self.assertNotContains(response, 'progress-row-')

    def test_member_sees_roster_rows(self):
        _mark_read(self.viewer, self.chapters, 3)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, 'progress-board')
        self.assertContains(response, f'progress-row-{self.viewer.pk}')
        self.assertNotContains(response, 'book-guest-gate')

    def test_staff_sees_board_and_is_pinned(self):
        # ``get_user_level`` elevates staff to premium, so staff view the board
        # (support/preview) and are pinned first like any member.
        staff = User.objects.create_user(
            email='staff-free@test.com', password='pw', is_staff=True,
        )
        staff.tier = self.free_tier
        staff.save()
        _mark_read(self.viewer, self.chapters, 2)
        self.client.force_login(staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'progress-board')
        self.assertContains(response, f'progress-row-{staff.pk}')
        content = response.content.decode()
        self.assertLess(
            content.index(f'progress-row-{staff.pk}'),
            content.index(f'progress-row-{self.viewer.pk}'),
        )


class ProgressOrderingTest(ProgressBoardTestMixin, TestCase):
    def test_you_first_then_chapters_desc(self):
        reader4 = self._member('r4@test.com')
        reader2 = self._member('r2@test.com')
        reader1 = self._member('r1@test.com')
        _mark_read(reader4, self.chapters, 4)
        _mark_read(reader2, self.chapters, 2)
        _mark_read(reader1, self.chapters, 1)
        _mark_read(self.viewer, self.chapters, 3)

        rows, _ = build_reader_rows(self.viewer, self.book, include_self=True)
        self.assertEqual(
            [r['member'].pk for r in rows],
            [self.viewer.pk, reader4.pk, reader2.pk, reader1.pk],
        )
        self.assertTrue(rows[0]['is_self'])

    def test_you_first_even_at_zero_read(self):
        reader = self._member('reader@test.com')
        _mark_read(reader, self.chapters, 4)
        # Viewer has read nothing but is still pinned first + highlighted.
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        content = response.content.decode()
        self_idx = content.index(f'progress-row-{self.viewer.pk}')
        other_idx = content.index(f'progress-row-{reader.pk}')
        self.assertLess(self_idx, other_idx)
        self.assertContains(response, 'aria-current="true"')
        self.assertContains(response, '(you)')
        self.assertContains(response, 'bg-accent/10')

    def test_display_name_tiebreak_ascending(self):
        # Two readers at the same chapter count sort by display_name asc.
        reader_b = self._member('bbb@test.com')
        reader_a = self._member('aaa@test.com')
        _mark_read(reader_a, self.chapters, 2)
        _mark_read(reader_b, self.chapters, 2)

        rows, _ = build_reader_rows(reader_a, self.book, include_self=False)
        self.assertEqual(
            [r['member'].pk for r in rows],
            [reader_a.pk, reader_b.pk],
        )

    def test_notes_shared_tiebreak_before_name(self):
        # Same chapters read; more notes ranks higher than name order.
        reader_early = self._member('aaa2@test.com')  # name sorts first
        reader_notes = self._member('zzz2@test.com')  # name sorts last
        _mark_read(reader_early, self.chapters, 2)
        _mark_read(reader_notes, self.chapters, 2)
        Note.objects.create(
            user=reader_notes, chapter=self.chapters[0], body='hi',
        )

        rows, _ = build_reader_rows(reader_early, self.book, include_self=False)
        self.assertEqual(
            [r['member'].pk for r in rows],
            [reader_notes.pk, reader_early.pk],
        )

    def test_no_rank_number_rendered(self):
        reader = self._member('reader@test.com')
        _mark_read(reader, self.chapters, 4)
        _mark_read(self.viewer, self.chapters, 2)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        # De-gamified: no rank / points / streak column headers.
        for banned in ('>#<', 'Points', 'Streak', 'Rank'):
            self.assertNotContains(response, banned)


class ProgressCountTest(ProgressBoardTestMixin, TestCase):
    def test_counts_and_pct_are_correct(self):
        _mark_read(self.viewer, self.chapters, 3)
        rows, _ = build_reader_rows(self.viewer, self.book, include_self=True)
        row = rows[0]
        self.assertEqual(row['chapters_read'], 3)
        self.assertEqual(row['total'], 5)
        self.assertEqual(row['pct'], 60)

    def test_count_renders_in_template(self):
        _mark_read(self.viewer, self.chapters, 3)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(
            response, f'data-testid="progress-count-{self.viewer.pk}"',
        )
        self.assertContains(response, '3 of 5')

    def test_notes_count_column(self):
        _mark_read(self.viewer, self.chapters, 2)
        Note.objects.create(
            user=self.viewer, chapter=self.chapters[0], body='a',
        )
        Note.objects.create(
            user=self.viewer, chapter=self.chapters[1], body='b',
        )
        rows, _ = build_reader_rows(self.viewer, self.book, include_self=True)
        self.assertEqual(rows[0]['notes_shared'], 2)


class ProgressQueryBudgetTest(ProgressBoardTestMixin, TestCase):
    def _seed_board(self, slug, reader_count):
        book = Book.objects.create(
            title=f'Book {slug}', slug=slug, author='A',
            required_level=LEVEL_MAIN, status='current',
        )
        chapters = [
            Chapter.objects.create(book=book, number=n, title=f'Ch {n}')
            for n in range(5)
        ]
        for i in range(reader_count):
            reader = self._member(f'{slug}-reader-{i}@test.com')
            _mark_read(reader, chapters, (i % 5) + 1)
        return book

    def test_query_count_is_fixed_regardless_of_roster_size(self):
        small = self._seed_board('small-book', 2)
        large = self._seed_board('large-book', 20)
        self.client.force_login(self.viewer)

        with CaptureQueriesContext(connection) as small_ctx:
            self.client.get(f'/books/{small.slug}/progress')
        with CaptureQueriesContext(connection) as large_ctx:
            self.client.get(f'/books/{large.slug}/progress')

        self.assertEqual(len(small_ctx), len(large_ctx))


class ProgressNoGamificationTest(ProgressBoardTestMixin, TestCase):
    def test_no_leaderboard_vocabulary_or_chrome(self):
        reader = self._member('reader@test.com')
        _mark_read(reader, self.chapters, 4)
        _mark_read(self.viewer, self.chapters, 2)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        lowered = response.content.decode().lower()
        for banned in ('leaderboard', 'ranked', 'climb', 'points', 'streak'):
            self.assertNotIn(banned, lowered)
        # No medal / flame iconography.
        self.assertNotContains(response, 'data-lucide="flame"')
        self.assertNotContains(response, 'data-lucide="medal"')


class ProgressHeaderStatTest(ProgressBoardTestMixin, TestCase):
    def test_header_counts_distinct_readers(self):
        reader = self._member('reader@test.com')
        _mark_read(reader, self.chapters, 4)
        _mark_read(self.viewer, self.chapters, 2)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, '2 readers reading along')

    def test_private_reader_counts_but_is_not_listed(self):
        # #1366 live: a private, non-self reader is excluded from named rows
        # but still counts toward the "reading along" header stat.
        private = self._member(
            'private@test.com', visibility=READER_VISIBILITY_PRIVATE,
        )
        _mark_read(private, self.chapters, 3)
        _mark_read(self.viewer, self.chapters, 1)

        rows, distinct = build_reader_rows(
            self.viewer, self.book, include_self=True,
        )
        row_pks = [r['member'].pk for r in rows]
        self.assertNotIn(private.pk, row_pks)  # not listed
        self.assertIn(self.viewer.pk, row_pks)
        self.assertEqual(distinct, 2)  # still counted

    def test_reader_with_no_profile_row_is_private_by_default(self):
        # Absence of a ReaderProfile row means private (the default) -> a
        # non-self reader without a row is not listed by name to others.
        no_profile = self._member('no-profile@test.com', visibility=None)
        _mark_read(no_profile, self.chapters, 2)
        _mark_read(self.viewer, self.chapters, 1)
        rows, distinct = build_reader_rows(
            self.viewer, self.book, include_self=True,
        )
        self.assertNotIn(no_profile.pk, [r['member'].pk for r in rows])
        self.assertEqual(distinct, 2)  # still counted

    def test_public_reader_is_listed(self):
        # A public, non-self reader is listed by name.
        reader = self._member(
            'reader@test.com', visibility=READER_VISIBILITY_PUBLIC,
        )
        _mark_read(reader, self.chapters, 2)
        _mark_read(self.viewer, self.chapters, 1)
        rows, _ = build_reader_rows(self.viewer, self.book, include_self=True)
        self.assertIn(reader.pk, [r['member'].pk for r in rows])

    def test_own_private_row_is_always_shown_to_self(self):
        # The viewer's OWN row is shown even when their profile is private.
        _set_visibility(self.viewer, READER_VISIBILITY_PRIVATE)
        _mark_read(self.viewer, self.chapters, 2)
        rows, _ = build_reader_rows(self.viewer, self.book, include_self=True)
        self.assertIn(self.viewer.pk, [r['member'].pk for r in rows])


class ProgressNameLinkTest(ProgressBoardTestMixin, TestCase):
    def test_public_reader_name_links_to_reader_profile(self):
        # #1366 flips names on: a public reader's name links to their profile.
        reader = self._member('reader@test.com', first_name='Reader One')
        _mark_read(reader, self.chapters, 3)
        _mark_read(self.viewer, self.chapters, 1)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reader One')
        self.assertContains(
            response, f'data-testid="progress-name-{reader.pk}"',
        )
        profile_url = reverse(
            'bookclub_reader_profile',
            kwargs={'slug': self.book.slug, 'user_id': reader.pk},
        )
        self.assertContains(response, f'href="{profile_url}"')

    def test_own_row_links_to_own_profile(self):
        _mark_read(self.viewer, self.chapters, 1)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        own_url = reverse(
            'bookclub_reader_profile',
            kwargs={'slug': self.book.slug, 'user_id': self.viewer.pk},
        )
        self.assertContains(response, f'href="{own_url}"')

    def test_only_public_and_self_rows_are_emitted_as_links(self):
        # A private non-self reader is never emitted as a named row/link.
        private = self._member(
            'hidden@test.com', visibility=READER_VISIBILITY_PRIVATE,
        )
        _mark_read(private, self.chapters, 4)
        _mark_read(self.viewer, self.chapters, 1)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertNotContains(
            response, f'progress-name-{private.pk}',
        )
        private_url = reverse(
            'bookclub_reader_profile',
            kwargs={'slug': self.book.slug, 'user_id': private.pk},
        )
        self.assertNotContains(response, f'href="{private_url}"')


class ProgressFirstMoverTest(ProgressBoardTestMixin, TestCase):
    def test_first_mover_sees_nudge_not_dead_end(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'progress-first-mover')
        self.assertContains(response, f'progress-row-{self.viewer.pk}')
        self.assertContains(response, '0 of 5')

    def test_no_nudge_when_others_present(self):
        reader = self._member('reader@test.com')
        _mark_read(reader, self.chapters, 2)
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertNotContains(response, 'progress-first-mover')

    def test_empty_open_board_shows_calm_card(self):
        # Open-tier book an anonymous visitor may view, but no one has read yet:
        # the roster has no rows (anon is never pinned) -> calm empty card.
        open_book = Book.objects.create(
            title='Open Book', slug='open-book', author='A',
            required_level=LEVEL_OPEN, status='current',
        )
        Chapter.objects.create(book=open_book, number=0, title='Ch 0')
        response = self.client.get(f'/books/{open_book.slug}/progress')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'progress-empty')
        self.assertNotContains(response, 'progress-row-')


class BookDetailProgressLinkTest(ProgressBoardTestMixin, TestCase):
    def test_member_sees_progress_link(self):
        self.client.force_login(self.viewer)
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertContains(response, 'book-progress-link')
        self.assertContains(response, self.url)

    def test_non_member_does_not_see_progress_link(self):
        self.client.force_login(self.free_user)
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertNotContains(response, 'book-progress-link')

    def test_anonymous_does_not_see_progress_link(self):
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertNotContains(response, 'book-progress-link')
