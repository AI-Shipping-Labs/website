"""Book Club data models (issue #1362).

A :class:`Book` is a Studio-managed, operator-authored reading cohort,
mirroring the Community Sprints pattern (``plans.Sprint``): a
``TimestampedModelMixin`` row with an operator-entered unique slug that
powers ``/books/<slug>``, a tier level drawn from ``content.access``
choices, and an optional recurring meeting series.

A :class:`Chapter` is one entry in a book's ordered roadmap, with an
optional organizer-set ``deadline`` (a meeting appointment, not homework)
and an optional link to a real event (wired in #1369).

Access reuses ``content.access`` exactly like every other content type:
a viewer sees a book's participation body iff
``content.access.get_user_level(user) >= book.required_level``.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_MAIN
from content.access import VISIBILITY_CHOICES as TIER_VISIBILITY_CHOICES
from content.models.mixins import TimestampedModelMixin

# Operator/admin lifecycle state. Drives the public hub sections (built in
# #1363): ``current`` = reading now, ``upcoming`` = coming up, ``finished`` =
# past reads. ``draft`` hides the book from every public surface;
# ``cancelled`` is a reversible off state. Only one book should be ``current``
# at a time — this is enforced softly in Studio (a non-blocking warning), not
# by a DB constraint, so operators can transition between cohorts freely.
BOOK_STATUS_DRAFT = 'draft'
BOOK_STATUS_UPCOMING = 'upcoming'
BOOK_STATUS_CURRENT = 'current'
BOOK_STATUS_FINISHED = 'finished'
BOOK_STATUS_CANCELLED = 'cancelled'

BOOK_STATUS_CHOICES = [
    (BOOK_STATUS_DRAFT, 'Draft'),
    (BOOK_STATUS_UPCOMING, 'Upcoming'),
    (BOOK_STATUS_CURRENT, 'Current'),
    (BOOK_STATUS_FINISHED, 'Finished'),
    (BOOK_STATUS_CANCELLED, 'Cancelled'),
]


class Book(TimestampedModelMixin, models.Model):
    """A community book-club reading cohort.

    Mirrors ``plans.Sprint``: operator-entered unique slug, tier level via
    ``content.access`` choices, optional ``event_series`` FK with
    ``SET_NULL``. Only one book should be ``current`` at a time; Studio warns
    (non-blocking) if a second is set current — this is deliberately not a DB
    constraint so operators can flip cohorts without a two-step dance.
    """

    title = models.CharField(max_length=200)
    slug = models.SlugField(
        max_length=160,
        unique=True,
        help_text=(
            'URL-safe id powering /books/<slug>. Operator-entered, '
            'content-derivable (e.g. "inference-engineering").'
        ),
    )
    subtitle = models.CharField(max_length=300, blank=True, default='')
    author = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    cover_image_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        help_text='Publisher/CDN cover image URL. Blank shows a tinted spine.',
    )
    cover_accent = models.CharField(
        max_length=60,
        blank=True,
        default='from-accent/30',
        help_text=(
            'Tailwind gradient token for the tinted-spine fallback when no '
            'cover image exists (e.g. from-accent/30, from-blue-500/30).'
        ),
    )
    # Minimum tier level required to participate. Default Main (20) so
    # community members can join by default. Same level integers and choices
    # used elsewhere for content gating (``content.access.VISIBILITY_CHOICES``).
    required_level = models.IntegerField(
        default=LEVEL_MAIN,
        choices=TIER_VISIBILITY_CHOICES,
        help_text=(
            'Minimum tier level required to participate. Default 20 (Main).'
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=BOOK_STATUS_CHOICES,
        default=BOOK_STATUS_DRAFT,
    )
    start_date = models.DateField(
        null=True,
        blank=True,
        help_text='Kickoff date. Drives ordering and the "Kickoff …" label.',
    )
    meeting_cadence = models.CharField(
        max_length=80,
        blank=True,
        default='',
        help_text=(
            'Display label for the meeting cadence, e.g. "Weekly · 17:00 CET". '
            'Interim field; the authoritative source is the event series (#1369).'
        ),
    )
    buy_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        help_text='Publisher/buy page for the book.',
    )
    # Full-book "Overall" summary the organizer writes at the end (issue #1368).
    # Plain text (no markdown engine); blank-line-separated paragraphs render as
    # separate <p>. A ``null`` ``summary_published_at`` means draft; a set
    # timestamp published — presence of the timestamp is the state (mirrors the
    # ``ChapterRead`` presence-of-row precedent). A published-but-empty summary
    # is treated as not published (see ``is_summary_published``).
    summary = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Full-book overall summary (plain text; blank-line-separated '
            'paragraphs). Also holds the working draft before publish.'
        ),
    )
    summary_published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the full-book summary was published. Null = draft.',
    )
    # Optional link to a recurring meeting series (rendered as meeting rows in
    # #1369). ``SET_NULL`` mirrors ``Sprint.event_series``: deleting the series
    # only severs the link; the book survives.
    event_series = models.ForeignKey(
        'events.EventSeries',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='books',
        help_text=(
            'Optional recurring meeting series for this book club. Deleting '
            'the series unlinks the book; the book itself is preserved.'
        ),
    )

    class Meta:
        ordering = ['-start_date', '-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['slug']),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('bookclub_book_detail', kwargs={'slug': self.slug})

    @property
    def is_summary_published(self):
        """Whether the full-book summary is visible to members (issue #1368).

        Published only when the timestamp is set AND the body is non-empty —
        a published-but-empty summary is treated as not published (the guard
        against publishing an empty body).
        """
        return self.summary_published_at is not None and bool(
            self.summary.strip()
        )


class Chapter(TimestampedModelMixin, models.Model):
    """One entry in a book's ordered reading roadmap.

    ``number`` starts at 0 (the epic URL contract:
    ``/books/<slug>/chapters/<int:number>``) and is unique within the book.
    ``deadline`` is an optional meeting appointment ("For Thursday's call ·
    Sep 7"), not homework — there is no recurrence engine. The presentation
    timeline (done/current/upcoming) is derived in the view layer (#1363/#1364),
    not stored here.
    """

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name='chapters',
    )
    number = models.PositiveSmallIntegerField(
        help_text='Chapter order, starting at 0. Unique per book.',
    )
    title = models.CharField(max_length=200)
    deadline = models.DateField(
        null=True,
        blank=True,
        help_text='Optional meeting appointment date. No recurrence engine.',
    )
    week_label = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text='Optional display label, e.g. "Week 1".',
    )
    # Optional link to a real event (rendered / constrained to the book's
    # linked series in #1369). ``SET_NULL``: deleting the event unlinks the
    # chapter; the chapter survives.
    event = models.ForeignKey(
        'events.Event',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='book_chapters',
        help_text=(
            'Optional linked event. Deleting the event unlinks the chapter.'
        ),
    )
    # Organizer-authored per-chapter summary (issue #1368). Plain text; the
    # working draft lives here before publish. ``null`` ``summary_published_at``
    # = draft (staff-only preview); a set timestamp = published to members.
    summary = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Chapter summary (plain text; blank-line-separated paragraphs). '
            'Also holds the working draft before publish.'
        ),
    )
    summary_published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the chapter summary was published. Null = draft.',
    )

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(
                fields=['book', 'number'],
                name='uniq_book_chapter_number',
            ),
        ]

    def __str__(self):
        return f'Ch. {self.number} — {self.title}'

    @property
    def is_summary_published(self):
        """Whether this chapter's summary is visible to members (issue #1368).

        Published only when the timestamp is set AND the body is non-empty,
        mirroring ``Book.is_summary_published``.
        """
        return self.summary_published_at is not None and bool(
            self.summary.strip()
        )

    def clean(self):
        """Enforce the series-only rule for a chapter's linked event (#1369).

        A chapter's ``event`` must be an occurrence of the book's own
        ``event_series``. This is the single source of truth for the
        constraint: the Studio view and the admin API both call it (or
        replicate the identical check) so the rule cannot be bypassed. A
        cross-table check like this is not expressible as a DB
        ``CheckConstraint``, so the app-level ``clean()`` is the contract.

        No-op when ``event`` is unset (detach is always allowed). When
        ``event`` is set:

        - the book must have an ``event_series`` linked, else a friendly
          field error tells the operator to link a series first;
        - the event must belong to that series, else a friendly field error.
        """
        super().clean()
        if self.event_id is None:
            return
        # A chapter always belongs to a book; guard defensively so an
        # in-memory chapter without a book set does not raise here.
        if self.book_id is None:
            return
        if self.book.event_series_id is None:
            raise ValidationError({
                'event': (
                    'Link an event series to the book before attaching '
                    'chapter events.'
                ),
            })
        if self.event.event_series_id != self.book.event_series_id:
            raise ValidationError({
                'event': "This event is not part of the book's event series.",
            })


class ChapterRead(models.Model):
    """One member's "I've read this chapter" mark (issue #1364).

    Presence-of-row semantics, following the
    ``content.UserContentCompletion`` / ``UserCourseProgress`` precedent: a
    row means "read"; to unmark, delete the row — there is no ``is_read``
    boolean to keep in sync. A dedicated ``ForeignKey(Chapter)`` (rather than
    the generic completion discriminator table) keeps queries flat, gives
    cascade-on-chapter-delete for free, and lets the board (#1367) and
    profiles (#1366) aggregate with plain ORM ``Count``/``filter``.
    """

    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name='reads',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='book_chapter_reads',
    )
    # Always set; the insert time is the read signal (mirrors
    # ``UserContentCompletion.completed_at``).
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'chapter'],
                name='uniq_book_chapter_read',
            ),
        ]
        indexes = [
            models.Index(fields=['chapter']),
        ]

    def __str__(self):
        return f'{self.user} read {self.chapter}'


class Note(TimestampedModelMixin, models.Model):
    """One member's per-chapter note (issue #1365).

    A first-class object (not a generic ``comments.Comment`` row): one note
    per member per chapter, an optional mermaid ``diagram``, and it hosts a
    comment thread of its own. The ``comment_content_id`` UUID is the ONLY
    bridge from a note to the shared ``comments`` app — comments key off the
    note, not off the chapter — mirroring ``plans.Plan.comment_content_id``.

    An empty submit clears/deletes the note (the write endpoint enforces
    "required for a saved note").
    """

    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name='notes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='book_notes',
    )
    body = models.TextField(
        help_text='The note text. An empty submit clears/deletes the note.',
    )
    diagram = models.TextField(
        blank=True,
        default='',
        help_text='Optional mermaid diagram source (no image uploads).',
    )
    # The single bridge to the shared comments app. Comments are keyed by this
    # UUID (never by the chapter), mirroring ``Plan.comment_content_id``.
    comment_content_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'chapter'],
                name='uniq_book_chapter_note',
            ),
        ]
        indexes = [
            models.Index(fields=['chapter']),
        ]

    def __str__(self):
        return f'{self.user} on {self.chapter}'

    def get_absolute_url(self):
        """The chapter reader page that hosts this note and its thread.

        Used for the note-comment notification deep link (#1341/#1365). The
        shared notification path appends ``#qa-section`` to land the reader on
        the comments area of the chapter page.
        """
        return reverse(
            'bookclub_chapter_detail',
            kwargs={'slug': self.chapter.book.slug, 'number': self.chapter.number},
        )


# Per-member Book Club reading-profile visibility (issue #1366). A single
# per-user flag — not per-user-per-book — drives whether a member is named on
# the progress board (#1367) and whether their notes appear in other members'
# group feeds (#1365). Default ``private``: a member opts in to public. Absence
# of a ``ReaderProfile`` row is therefore treated as ``private`` everywhere.
READER_VISIBILITY_PUBLIC = 'public'
READER_VISIBILITY_PRIVATE = 'private'

READER_VISIBILITY_CHOICES = [
    (READER_VISIBILITY_PUBLIC, 'Public'),
    (READER_VISIBILITY_PRIVATE, 'Private'),
]


class ReaderProfile(TimestampedModelMixin, models.Model):
    """A member's Book Club reading-profile visibility preference (issue #1366).

    One row per member (``OneToOneField``) carrying a single ``visibility``
    flag. The flag is book-agnostic: it mirrors a single-privacy-setting mental
    model (like the newsletter toggle), and per-book granularity is a
    documented future extension (add a ``book`` FK + a ``UniqueConstraint`` on
    ``(user, book)``) reachable without reshaping any caller because every read
    routes through ``bookclub.profiles.is_reader_public`` /
    ``public_reader_ids``.

    A member with no row is treated as ``private`` — the default. The toggle
    ``get_or_create``s the row on first use, so no data backfill is needed.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='book_reader_profile',
    )
    visibility = models.CharField(
        max_length=10,
        choices=READER_VISIBILITY_CHOICES,
        default=READER_VISIBILITY_PRIVATE,
        help_text=(
            'Public lists the member by name on the progress board and shows '
            "their notes in other members' group feeds. Default private."
        ),
    )

    def __str__(self):
        return f'{self.user} — {self.visibility}'

    @property
    def is_public(self):
        return self.visibility == READER_VISIBILITY_PUBLIC
