"""Books hub, detail status-branching, and secondary pages (issue #1363).

Covers the view-layer logic that #1363 adds on top of the #1362/#1364
foundation: the ``/books`` hub grouping by ``Book.status``, the
``/books/<slug>`` status branch (current -> full detail, upcoming/finished ->
secondary, cancelled -> 404, draft -> 404 public / 200 staff), the derived
"This week" current-chapter selection, the Community-nav placement, and the
absence of any dead link to an unbuilt Book Club route.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookclub.models import Book, Chapter
from bookclub.views import _derive_current_chapter
from content.access import LEVEL_MAIN
from payments.models import Tier
from website.context_processors import _build_primary_nav

User = get_user_model()


class BooksHubGroupingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.current = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )
        cls.upcoming = Book.objects.create(
            title='Designing ML Systems', slug='designing-ml-systems',
            author='Chip Huyen', required_level=LEVEL_MAIN,
            status='upcoming', start_date=date(2026, 10, 1),
        )
        cls.finished = Book.objects.create(
            title='The Pragmatic Programmer', slug='pragmatic-programmer',
            author='Hunt & Thomas', required_level=LEVEL_MAIN,
            status='finished', start_date=date(2026, 3, 1),
        )
        cls.draft = Book.objects.create(
            title='Secret Draft', slug='secret-draft', author='Nobody',
            required_level=LEVEL_MAIN, status='draft',
        )
        cls.cancelled = Book.objects.create(
            title='Cancelled Read', slug='cancelled-read', author='Nobody',
            required_level=LEVEL_MAIN, status='cancelled',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def test_hub_renders_header_and_how_it_works(self):
        response = self.client.get('/books')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/index.html')
        self.assertContains(response, 'Read together, ship together')
        self.assertContains(response, 'Community &middot; Books')
        self.assertContains(response, 'How it works')

    def test_current_book_is_featured(self):
        response = self.client.get('/books')
        self.assertEqual(response.context['featured_book'], self.current)
        self.assertContains(response, 'featured-book-card')
        self.assertContains(response, '/books/inference-engineering')

    def test_upcoming_and_finished_are_grouped(self):
        response = self.client.get('/books')
        self.assertEqual(list(response.context['upcoming_books']), [self.upcoming])
        self.assertEqual(list(response.context['past_books']), [self.finished])
        self.assertContains(response, 'upcoming-book-card')
        self.assertContains(response, 'past-book-card')

    def test_draft_and_cancelled_hidden_from_public(self):
        response = self.client.get('/books')
        self.assertEqual(list(response.context['draft_books']), [])
        self.assertNotContains(response, 'Secret Draft')
        self.assertNotContains(response, 'Cancelled Read')

    def test_staff_see_draft_but_not_cancelled(self):
        self.client.force_login(self.staff)
        response = self.client.get('/books')
        self.assertEqual(list(response.context['draft_books']), [self.draft])
        self.assertContains(response, 'Secret Draft')
        self.assertContains(response, 'draft-book-card')
        self.assertNotContains(response, 'Cancelled Read')

    def test_draft_card_shows_draft_badge_not_up_next(self):
        # Isolate a single draft book (title without "Draft") so the badge — not
        # the title — is what proves the draft branch renders.
        Book.objects.all().delete()
        Book.objects.create(
            title='Hidden Book', slug='hidden-book', author='Nobody',
            required_level=LEVEL_MAIN, status='draft',
        )
        self.client.force_login(self.staff)
        response = self.client.get('/books')
        self.assertContains(response, 'draft-book-card')
        # "Draft" appears only in the badge (the title is "Hidden Book"), and the
        # blue "Up next" upcoming badge must not render for a draft.
        self.assertContains(response, 'Draft')
        self.assertNotContains(response, 'Up next')

    def test_featured_picks_most_recent_current_by_start_date(self):
        newer = Book.objects.create(
            title='Newer Current', slug='newer-current', author='Someone',
            required_level=LEVEL_MAIN, status='current',
            start_date=date(2026, 9, 1),
        )
        response = self.client.get('/books')
        self.assertEqual(response.context['featured_book'], newer)


class BooksHubEmptyStateTest(TestCase):
    def test_hub_with_no_books_renders_empty_state(self):
        response = self.client.get('/books')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['featured_book'])
        self.assertContains(response, 'No book in progress right now')
        # The rest of the page still renders.
        self.assertContains(response, 'How it works')

    def test_upcoming_and_past_sections_omitted_when_empty(self):
        response = self.client.get('/books')
        self.assertNotContains(response, 'upcoming-book-card')
        self.assertNotContains(response, 'past-book-card')
        self.assertNotContains(response, '>Coming up<')
        self.assertNotContains(response, '>Past reads<')


class BookDetailBranchingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.main_user = User.objects.create_user(email='main@test.com', password='pw')
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()
        cls.staff = User.objects.create_user(
            email='staff2@test.com', password='pw', is_staff=True,
        )

    def _book(self, slug, status):
        return Book.objects.create(
            title=f'Book {slug}', slug=slug, author='Author',
            required_level=LEVEL_MAIN, status=status,
            start_date=date(2026, 8, 10),
        )

    def test_current_renders_full_detail_with_gate_for_guest(self):
        self._book('cur-guest', 'current')
        response = self.client.get('/books/cur-guest')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/book_detail.html')
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'book-participation-body')

    def test_current_renders_participation_body_for_member(self):
        self._book('cur-member', 'current')
        self.client.force_login(self.main_user)
        response = self.client.get('/books/cur-member')
        self.assertTemplateUsed(response, 'bookclub/book_detail.html')
        self.assertContains(response, 'book-participation-body')
        self.assertNotContains(response, 'book-guest-gate')

    def test_upcoming_renders_secondary(self):
        self._book('up-book', 'upcoming')
        response = self.client.get('/books/up-book')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/book_secondary.html')
        self.assertContains(response, 'Up next')

    def test_finished_renders_secondary(self):
        self._book('fin-book', 'finished')
        response = self.client.get('/books/fin-book')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/book_secondary.html')
        self.assertContains(response, 'This book is finished')

    def test_cancelled_404s_for_everyone(self):
        self._book('cancelled-book', 'cancelled')
        self.assertEqual(self.client.get('/books/cancelled-book').status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/books/cancelled-book').status_code, 404)

    def test_draft_404_public_200_staff(self):
        self._book('draft-book', 'draft')
        self.assertEqual(self.client.get('/books/draft-book').status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get('/books/draft-book')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/book_detail.html')

    def test_unknown_slug_404s(self):
        self.assertEqual(self.client.get('/books/nope').status_code, 404)


class SecondaryPageContentTest(TestCase):
    def test_finished_recap_shows_chapter_count_and_no_dead_links(self):
        book = Book.objects.create(
            title='Done Book', slug='done-book', author='Author',
            required_level=LEVEL_MAIN, status='finished',
            start_date=date(2026, 3, 1),
        )
        Chapter.objects.create(book=book, number=0, title='One')
        Chapter.objects.create(book=book, number=1, title='Two')
        response = self.client.get('/books/done-book')
        self.assertContains(response, 'book-finished-recap')
        self.assertContains(response, '2 chapters')
        # #1368 registered the full-book summary route, so the finished recap
        # now links to it (no longer a dead link).
        self.assertContains(response, '/books/done-book/summary')
        self.assertContains(response, 'book-finished-summary-cta')
        # The standings route stays behind #1367's own flag — no dead link.
        self.assertNotContains(response, '/progress')
        # Multi-line template comments must not leak as visible page text.
        self.assertNotContains(response, '{#')

    def test_upcoming_shows_kickoff_and_no_notify_control(self):
        Book.objects.create(
            title='Soon Book', slug='soon-book', author='Author',
            required_level=LEVEL_MAIN, status='upcoming',
            start_date=date(2026, 10, 1),
        )
        response = self.client.get('/books/soon-book')
        self.assertContains(response, 'book-upcoming-callout')
        self.assertContains(response, 'Starts')
        # The prototype's fake GET-state notify control must not ship.
        self.assertNotContains(response, 'name="notify"')
        # Multi-line template comments must not leak as visible page text.
        self.assertNotContains(response, '{#')

    def test_upcoming_join_cta_hidden_when_viewer_meets_tier(self):
        Book.objects.create(
            title='Soon2 Book', slug='soon2-book', author='Author',
            required_level=LEVEL_MAIN, status='upcoming',
            start_date=date(2026, 10, 1),
        )
        main_user = User.objects.create_user(email='m2@test.com', password='pw')
        main_user.tier = Tier.objects.get(slug='main')
        main_user.save()
        self.client.force_login(main_user)
        response = self.client.get('/books/soon2-book')
        self.assertNotContains(response, 'book-upcoming-join-cta')


class ThisWeekCalloutTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_user = User.objects.create_user(email='reader@test.com', password='pw')
        cls.main_user.tier = Tier.objects.get(slug='main')
        cls.main_user.save()

    def test_callout_names_current_chapter_for_member(self):
        book = Book.objects.create(
            title='Reading', slug='reading', author='A',
            required_level=LEVEL_MAIN, status='current',
        )
        today = timezone.localdate()
        Chapter.objects.create(
            book=book, number=0, title='Past', week_label='Week 1',
            deadline=today - timedelta(days=7),
        )
        Chapter.objects.create(
            book=book, number=1, title='This one', week_label='Week 2',
            deadline=today + timedelta(days=3),
        )
        self.client.force_login(self.main_user)
        response = self.client.get('/books/reading')
        self.assertContains(response, 'book-this-week')
        self.assertContains(response, 'Ch. 1 — This one')
        self.assertContains(response, 'read by')
        # Multi-line template comments must not leak as visible page text.
        self.assertNotContains(response, '{#')


class DeriveCurrentChapterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='B', slug='derive-b', author='A',
            required_level=LEVEL_MAIN, status='current',
        )

    def _chapter(self, number, deadline=None):
        return Chapter.objects.create(
            book=self.book, number=number, title=f'Ch {number}', deadline=deadline,
        )

    def test_picks_earliest_deadline_today_or_later(self):
        today = timezone.localdate()
        self._chapter(0, today - timedelta(days=10))
        c1 = self._chapter(1, today + timedelta(days=2))
        self._chapter(2, today + timedelta(days=9))
        chapters = list(self.book.chapters.all())
        self.assertEqual(_derive_current_chapter(chapters), c1)

    def test_today_deadline_counts_as_current(self):
        today = timezone.localdate()
        c0 = self._chapter(0, today)
        chapters = list(self.book.chapters.all())
        self.assertEqual(_derive_current_chapter(chapters), c0)

    def test_falls_back_to_last_chapter_when_all_past(self):
        today = timezone.localdate()
        self._chapter(0, today - timedelta(days=20))
        last = self._chapter(1, today - timedelta(days=5))
        chapters = list(self.book.chapters.all())
        self.assertEqual(_derive_current_chapter(chapters), last)

    def test_falls_back_to_last_chapter_when_no_deadlines(self):
        self._chapter(0)
        last = self._chapter(1)
        chapters = list(self.book.chapters.all())
        self.assertEqual(_derive_current_chapter(chapters), last)

    def test_none_when_no_chapters(self):
        self.assertIsNone(_derive_current_chapter([]))


class BooksNavPlacementTest(TestCase):
    def test_books_item_follows_community_sprints(self):
        nav = _build_primary_nav({}, has_published_downloads=False)
        community = next(g for g in nav if g.get('key') == 'community')
        slugs = [item['slug'] for item in community['items']]
        self.assertIn('books', slugs)
        self.assertEqual(slugs.index('books'), slugs.index('sprints') + 1)
        books_item = community['items'][slugs.index('books')]
        self.assertEqual(books_item['href'], '/books')
        self.assertEqual(books_item['label'], 'Books')
