"""Merged week blocks on ``/books/<slug>`` (issue #1461).

The roadmap and the old separate "When we meet" list are one week-by-week
block now: each week carries its chapters and, last, the meeting those
chapters link to through ``Chapter.event``. Covered here:

- the join itself (week block -> meeting row), and its public-status filter,
- the placement contract for occurrences no chapter links to,
- the "every occurrence renders exactly once" invariant,
- row titles that lead with the differentiating token,
- the recap affordance gate,
- the composed week label and the separable theme.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.services.timezones import format_user_datetime
from bookclub.models import Book, Chapter
from bookclub.views import _meeting_display_label
from content.access import LEVEL_MAIN
from events.models import Event, EventSeries
from payments.models import Tier

User = get_user_model()


def _event(series, *, title, slug, start, status='upcoming', **kwargs):
    return Event.objects.create(
        title=title, slug=slug, start_datetime=start,
        end_datetime=start + timedelta(hours=1), status=status,
        origin='studio', event_series=series, **kwargs,
    )


@tag('core')
class WeekBlockFixture(TestCase):
    """A book whose week 1 and week 2 each link one public occurrence.

    The series also carries a kickoff before week 1 and a wrap-up after week 2
    that no chapter links to, plus a draft occurrence linked to a chapter.
    """

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='wk@test.com', password='pw')
        cls.member.tier = Tier.objects.get(slug='main')
        cls.member.save()

        cls.series = EventSeries.objects.create(
            name='Inference Engineering Book Club',
            slug='inference-engineering-book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        now = timezone.now()
        cls.kickoff = _event(
            cls.series, title='Inference Engineering Book Club — Kickoff',
            slug='ie-kickoff', start=now + timedelta(days=1),
        )
        cls.week1_meeting = _event(
            cls.series, title='Inference Engineering Book Club week 1',
            slug='ie-week-1', start=now + timedelta(days=8),
        )
        cls.week2_meeting = _event(
            cls.series, title='Inference Engineering Book Club week 2',
            slug='ie-week-2', start=now + timedelta(days=15),
        )
        cls.wrap_up = _event(
            cls.series, title='Inference Engineering Book Club wrap-up',
            slug='ie-wrap-up', start=now + timedelta(days=30),
        )
        cls.draft = _event(
            cls.series, title='Draft session', slug='ie-draft',
            start=now + timedelta(days=22), status='draft',
        )

        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', event_series=cls.series,
        )
        today = timezone.localdate()
        # Week 1 = chapters 0,1 (both link the same occurrence).
        for number in (0, 1):
            Chapter.objects.create(
                book=cls.book, number=number, title=f'Ch{number}',
                week_number=1, deadline=today + timedelta(days=7),
                event=cls.week1_meeting,
            )
        # Week 2 = chapters 2,3 — chapter 3 links the DRAFT occurrence.
        Chapter.objects.create(
            book=cls.book, number=2, title='Ch2', week_number=2,
            deadline=today + timedelta(days=14), event=cls.week2_meeting,
        )
        Chapter.objects.create(
            book=cls.book, number=3, title='Ch3', week_number=2,
            deadline=today + timedelta(days=14), event=cls.draft,
        )

    def _get(self, slug='inference-engineering'):
        self.client.force_login(self.member)
        return self.client.get(f'/books/{slug}')


class WeekBlockJoinTest(WeekBlockFixture):
    def test_week_block_holds_its_own_meeting(self):
        weeks = self._get().context['chapter_weeks']
        self.assertEqual([w['week_number'] for w in weeks], [1, 2])
        self.assertEqual(weeks[0]['events'], [self.week1_meeting])
        self.assertEqual(weeks[1]['events'], [self.week2_meeting])

    def test_two_chapters_sharing_one_meeting_render_one_row(self):
        # Week 1's chapters 0 and 1 both point at the same occurrence.
        response = self._get()
        self.assertEqual(len(response.context['chapter_weeks'][0]['events']), 1)
        self.assertContains(response, 'book-meeting-row', count=4)

    def test_draft_occurrence_linked_to_a_chapter_never_renders(self):
        response = self._get()
        self.assertNotContains(response, 'Draft session')
        self.assertNotContains(response, self.draft.get_absolute_url())
        self.assertNotIn(
            self.draft, response.context['chapter_weeks'][1]['events'],
        )

    def test_unlinked_occurrences_are_placed_around_the_week_blocks(self):
        response = self._get()
        # The kickoff starts before week 1's meeting -> above the first block.
        self.assertEqual(response.context['meetings_before'], [self.kickoff])
        # The wrap-up is later -> below the last block.
        self.assertEqual(response.context['meetings_after'], [self.wrap_up])

    def test_every_public_occurrence_renders_exactly_once(self):
        response = self._get()
        body = response.content.decode()
        for meeting in (
            self.kickoff, self.week1_meeting, self.week2_meeting, self.wrap_up,
        ):
            self.assertEqual(
                body.count(f'href="{meeting.get_absolute_url()}"'), 1,
                f'{meeting.slug} did not render exactly once',
            )

    def test_when_we_meet_section_no_longer_renders(self):
        response = self._get()
        self.assertNotContains(response, 'book-when-we-meet')
        self.assertNotContains(response, 'When we meet')

    def test_meeting_rows_use_the_shared_list_row_partial(self):
        response = self._get()
        self.assertTemplateUsed(response, 'bookclub/_meeting_row.html')
        self.assertTemplateUsed(response, 'includes/_list_row.html')

    def test_guest_sees_no_meeting_data(self):
        self.client.logout()
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, 'data-testid="book-guest-gate"', count=1)
        self.assertNotContains(response, 'book-meeting-row')
        self.assertNotContains(response, self.kickoff.get_absolute_url())
        self.assertNotContains(response, 'book-week-group')
        self.assertNotContains(response, 'book-chapter-link')


class MeetingRowTitleTest(WeekBlockFixture):
    def test_week_linked_row_leads_with_meeting_and_the_datetime(self):
        response = self._get()
        when = format_user_datetime(
            self.week1_meeting.start_datetime, self.member,
            fmt='%a, %b %d, %H:%M',
        )
        # Weekday-bearing short datetime, e.g. "Meeting · Mon, Aug 17, 19:00".
        self.assertContains(response, f'Meeting · {when}')
        # Neither the book nor the series title is repeated on the row.
        self.assertNotContains(
            response, 'Inference Engineering Book Club week 1',
        )
        self.assertNotContains(response, 'Inference Engineering · ')

    def test_standalone_row_strips_the_series_title_prefix(self):
        response = self._get()
        self.assertContains(response, 'Kickoff · ')
        self.assertNotContains(
            response, 'Inference Engineering Book Club — Kickoff',
        )

    def test_display_label_strips_case_insensitively_with_separators(self):
        series = 'Inference Engineering Book Club'
        cases = {
            'Inference Engineering Book Club — Kickoff': 'Kickoff',
            'inference engineering book club: wrap-up': 'Wrap-up',
            'Inference Engineering Book Club · Week 4': 'Week 4',
            'Inference Engineering book club kickoff': 'Kickoff',
            # Nothing left after stripping -> keep the full title.
            'Inference Engineering Book Club': 'Inference Engineering Book Club',
            # No prefix match -> untouched.
            'Office hours': 'Office hours',
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                event = Event(title=title)
                self.assertEqual(_meeting_display_label(event, series), expected)

    def test_display_label_without_a_series_keeps_the_title(self):
        self.assertEqual(
            _meeting_display_label(Event(title='Office hours'), ''),
            'Office hours',
        )


class MeetingRecapAffordanceTest(WeekBlockFixture):
    def test_past_meeting_with_a_recap_offers_the_notes_link(self):
        past = timezone.now() - timedelta(days=3)
        Event.objects.filter(pk=self.week1_meeting.pk).update(
            start_datetime=past, end_datetime=past + timedelta(hours=1),
            status='completed', recap_html='<p>What we discussed.</p>',
        )
        self.week1_meeting.refresh_from_db()
        response = self._get()
        self.assertContains(response, 'book-meeting-recap', count=1)
        self.assertContains(
            response, f'href="{self.week1_meeting.get_recap_url()}"', count=1,
        )
        # The row remains the event-detail navigation target; only the notes
        # affordance goes directly to the occurrence recap.
        self.assertContains(
            response, f'href="{self.week1_meeting.get_absolute_url()}"', count=1,
        )

    def test_upcoming_meeting_shows_no_recap_placeholder(self):
        response = self._get()
        self.assertNotContains(response, 'book-meeting-recap')
        self.assertNotContains(response, 'recap coming')

    def test_past_meeting_without_a_recap_shows_nothing_extra(self):
        past = timezone.now() - timedelta(days=3)
        Event.objects.filter(pk=self.week1_meeting.pk).update(
            start_datetime=past, end_datetime=past + timedelta(hours=1),
            status='completed',
        )
        response = self._get()
        self.assertNotContains(response, 'book-meeting-recap')


class WeekLabelCompositionTest(WeekBlockFixture):
    def test_theme_composes_with_the_number_and_stays_separable(self):
        Chapter.objects.filter(book=self.book, number=2).update(
            week_label='Batching',
        )
        weeks = self._get().context['chapter_weeks']
        self.assertEqual(weeks[1]['label'], 'Week 2 · Batching')
        self.assertEqual(weeks[1]['theme'], 'Batching')
        self.assertEqual(weeks[0]['label'], 'Week 1')
        self.assertEqual(weeks[0]['theme'], '')


class WeeklessBookTest(TestCase):
    """A book with no week numbers and no chapter-linked events (#1461)."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='flat@test.com', password='pw')
        cls.member.tier = Tier.objects.get(slug='main')
        cls.member.save()
        cls.series = EventSeries.objects.create(
            name='Flat Book Club', slug='flat-book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        cls.only_meeting = _event(
            cls.series, title='Flat Book Club opening call', slug='flat-open',
            start=timezone.now() + timedelta(days=2),
        )
        cls.book = Book.objects.create(
            title='Flat Book', slug='flat-book', author='Author',
            required_level=LEVEL_MAIN, status='current',
            event_series=cls.series,
        )
        Chapter.objects.create(book=cls.book, number=0, title='A')
        Chapter.objects.create(book=cls.book, number=1, title='B')

    def test_unheaded_group_keeps_its_chapters_and_one_meeting_row(self):
        self.client.force_login(self.member)
        response = self.client.get('/books/flat-book')
        self.assertNotContains(response, 'book-week-heading')
        self.assertContains(response, 'book-chapter-mark-read', count=2)
        self.assertContains(response, 'book-meeting-row', count=1)
        self.assertEqual(response.context['meetings_after'], [self.only_meeting])
        self.assertEqual(response.context['meetings_before'], [])
        # Stripping the series prefix leaves the session's own words.
        self.assertContains(response, 'Opening call · ')


class RoadmapMobileLayoutTest(WeekBlockFixture):
    """Section H: the two mobile defects inside the rewritten block (#1461)."""

    def test_read_and_unread_rows_align_their_controls_the_same_way(self):
        from bookclub.models import ChapterRead

        ChapterRead.objects.create(
            user=self.member, chapter=self.book.chapters.get(number=0),
        )
        response = self._get()
        body = response.content.decode()
        # The row no longer uses justify-between (which left-aligned the
        # wrapped control cluster on read rows); the cluster is pushed with
        # ml-auto in both states instead.
        self.assertNotIn(
            'flex min-h-[44px] flex-wrap items-center justify-between', body,
        )
        self.assertContains(
            response,
            'class="ml-auto flex items-center gap-3 text-xs '
            'text-muted-foreground"',
            count=4,
        )

    def test_progress_bar_has_clearance_from_the_header_links(self):
        response = self._get()
        self.assertContains(
            response,
            'class="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted"',
        )


class NoChapterBookTest(TestCase):
    """A book with a series but no chapters still lists its occurrences."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='nc@test.com', password='pw')
        cls.member.tier = Tier.objects.get(slug='main')
        cls.member.save()
        cls.series = EventSeries.objects.create(
            name='Empty Roadmap Club', slug='empty-roadmap-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        cls.meeting = _event(
            cls.series, title='Empty Roadmap Club kickoff', slug='erc-kickoff',
            start=timezone.now() + timedelta(days=3),
        )
        cls.book = Book.objects.create(
            title='No Chapters', slug='no-chapters', author='Author',
            required_level=LEVEL_MAIN, status='current',
            event_series=cls.series,
        )

    def test_roadmap_placeholder_and_the_meeting_both_render(self):
        self.client.force_login(self.member)
        response = self.client.get('/books/no-chapters')
        self.assertContains(response, 'The chapter roadmap is being prepared.')
        self.assertContains(response, 'book-meeting-row', count=1)
