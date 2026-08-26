"""Chapter page: own note renders once, and page hierarchy (issue #1461).

The viewer's own note used to render twice, ~1000px apart — once in the
own-note card and again in the group feed. The card is now the single
canonical self surface, which pulls the note's comment thread and the
member-API hint into it, and leaves the chapter-level notes count in one
place (the ``Notes`` section header).
"""

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


@tag('core')
class ChapterHierarchyFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )
        cls.ch0 = Chapter.objects.create(
            book=cls.book, number=0, title='Inference',
        )
        cls.ch1 = Chapter.objects.create(
            book=cls.book, number=1, title='Prerequisites',
        )
        cls.member = User.objects.create_user(email='me@test.com', password='pw')
        cls.member.tier = Tier.objects.get(slug='main')
        cls.member.save()
        cls.other = User.objects.create_user(email='them@test.com', password='pw')
        cls.other.tier = Tier.objects.get(slug='main')
        cls.other.save()
        ReaderProfile.objects.create(
            user=cls.other, visibility=READER_VISIBILITY_PUBLIC,
        )

    def _url(self, number=0):
        return f'/books/inference-engineering/chapters/{number}'


class OwnNoteRendersOnceTest(ChapterHierarchyFixture):
    def setUp(self):
        self.own = Note.objects.create(
            chapter=self.ch0, user=self.member, body='My own takeaway.',
        )
        self.theirs = Note.objects.create(
            chapter=self.ch0, user=self.other, body='Their takeaway.',
        )
        self.client.force_login(self.member)

    def test_own_note_body_appears_exactly_once(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'My own takeaway.', count=1)
        self.assertContains(
            response, 'data-testid="own-note-written"', count=1,
        )

    def test_group_feed_holds_the_other_member_only(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'Their takeaway.')
        self.assertEqual(
            [n.pk for n in response.context['group_notes']], [self.theirs.pk],
        )
        self.assertContains(response, 'data-testid="note-body"', count=1)

    def test_notes_count_includes_the_viewers_own_note_and_prints_once(self):
        response = self.client.get(self._url())
        self.assertEqual(response.context['notes_count'], 2)
        # One home for the count: the "Notes" section header, not the header
        # meta strip that used to print the same number a viewport earlier.
        self.assertContains(response, '2 notes', count=1)
        body = response.content.decode()
        self.assertLess(
            body.index('>Notes<'), body.index('2 notes'),
        )

    def test_private_author_is_still_excluded_from_the_count_and_feed(self):
        ghost = User.objects.create_user(email='ghost@test.com', password='pw')
        ghost.tier = Tier.objects.get(slug='main')
        ghost.save()
        ReaderProfile.objects.create(
            user=ghost, visibility=READER_VISIBILITY_PRIVATE,
        )
        Note.objects.create(chapter=self.ch0, user=ghost, body='Hidden take.')
        response = self.client.get(self._url())
        self.assertNotContains(response, 'Hidden take.')
        self.assertEqual(response.context['notes_count'], 2)

    def test_own_note_card_hosts_its_comment_thread(self):
        response = self.client.get(self._url())
        self.assertContains(
            response, f'qa-section-{self.own.pk}',
        )
        self.assertContains(response, str(self.own.comment_content_id))

    def test_api_hint_sits_inside_the_own_note_card(self):
        response = self.client.get(self._url())
        body = response.content.decode()
        card_start = body.index('data-testid="own-note-written"')
        hint = body.index('data-testid="own-note-api-hint"')
        feed = body.index('data-testid="note-body"')
        self.assertLess(card_start, hint)
        self.assertLess(hint, feed)


class ComposerStateTest(ChapterHierarchyFixture):
    def test_composer_has_no_comment_thread_but_keeps_the_api_hint(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(1))
        self.assertContains(response, 'data-testid="own-note-composer"')
        self.assertContains(response, 'data-testid="own-note-api-hint"', count=1)
        self.assertNotContains(response, 'class="qa-thread"')
        self.assertNotContains(response, 'qa-thread" id="qa-section')

    def test_posting_the_first_note_moves_it_into_the_written_card_only(self):
        self.client.force_login(self.member)
        self.client.post(self._url(1) + '/note', {'body': 'First takeaway.'})
        response = self.client.get(self._url(1))
        self.assertContains(response, 'First takeaway.', count=1)
        self.assertContains(response, 'data-testid="own-note-written"', count=1)
        self.assertEqual(response.context['group_notes'], [])
        self.assertContains(response, 'data-testid="own-note-api-hint"', count=1)


class ChapterNavigationEndsTest(ChapterHierarchyFixture):
    def test_first_chapter_fills_the_left_nav_slot_with_a_back_link(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(0))
        self.assertContains(response, 'data-testid="chapter-back-to-book"')
        self.assertNotContains(response, 'data-testid="chapter-prev"')

    def test_later_chapter_keeps_the_prev_chapter_link(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(1))
        self.assertContains(response, 'data-testid="chapter-prev"')
        self.assertNotContains(response, 'data-testid="chapter-back-to-book"')

    def _next_anchor(self, response):
        body = response.content.decode()
        next_start = body.index('data-testid="chapter-next"')
        return body[body.rindex('<a href', 0, next_start):next_start]

    def test_member_forward_action_stays_primary(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(0))
        self.assertIn(
            'bg-accent text-accent-foreground', self._next_anchor(response),
        )

    def test_guest_forward_action_is_demoted_to_secondary(self):
        response = self.client.get(self._url(0))
        next_anchor = self._next_anchor(response)
        self.assertNotIn('bg-accent text-accent-foreground', next_anchor)
        self.assertIn('border-border', next_anchor)

    def test_guest_on_the_last_chapter_also_gets_a_secondary_forward(self):
        response = self.client.get(self._url(1))
        body = response.content.decode()
        end_start = body.index('data-testid="chapter-end-of-book"')
        anchor = body[body.rindex('<a href', 0, end_start):end_start]
        self.assertNotIn('bg-accent text-accent-foreground', anchor)

    def test_back_link_carries_the_muted_44px_focus_ring_pattern(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(0))
        self.assertContains(
            response,
            'class="inline-flex min-h-[44px] items-center gap-2 text-sm '
            'text-muted-foreground transition-colors hover:text-foreground '
            'focus-visible:outline-none focus-visible:ring-2 '
            'focus-visible:ring-accent focus-visible:ring-offset-2 '
            'focus-visible:ring-offset-background" '
            'data-testid="chapter-back-link"',
        )


class NoteProseModifierTest(ChapterHierarchyFixture):
    def test_both_note_bodies_opt_into_prose_note(self):
        Note.objects.create(chapter=self.ch0, user=self.member, body='## Mine')
        Note.objects.create(chapter=self.ch0, user=self.other, body='## Theirs')
        self.client.force_login(self.member)
        response = self.client.get(self._url(0))
        self.assertContains(response, 'class="prose prose-note mt-3', count=2)


class ReaderProfileUnaffectedTest(ChapterHierarchyFixture):
    """The self-exclusion is scoped to the chapter feed only (#1461)."""

    def test_owner_still_sees_their_own_note_on_their_reader_profile(self):
        note = Note.objects.create(
            chapter=self.ch0, user=self.member, body='My own takeaway.',
        )
        self.client.force_login(self.member)
        response = self.client.get(
            f'/books/inference-engineering/readers/{self.member.pk}',
        )
        self.assertEqual([n.pk for n in response.context['notes']], [note.pk])
        self.assertContains(response, 'My own takeaway.')

    def test_progress_board_still_counts_the_viewers_note(self):
        Note.objects.create(
            chapter=self.ch0, user=self.member, body='My own takeaway.',
        )
        self.client.force_login(self.member)
        response = self.client.get('/books/inference-engineering/progress')
        self_row = next(
            row for row in response.context['reader_rows'] if row['is_self']
        )
        self.assertEqual(self_row['notes_shared'], 1)


class GuestChapterGateTest(ChapterHierarchyFixture):
    def test_guest_sees_one_gate_and_no_note_bodies(self):
        Note.objects.create(chapter=self.ch0, user=self.other, body='Secret take.')
        response = self.client.get(self._url(0))
        self.assertContains(response, 'data-testid="book-guest-gate"', count=1)
        self.assertNotContains(response, 'Secret take.')
        self.assertNotContains(response, 'data-testid="note-body"')
        self.assertNotContains(response, 'own-note-api-hint')
