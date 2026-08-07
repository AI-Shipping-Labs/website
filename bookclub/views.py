"""Public Book Club views (issues #1362, #1363, #1364).

Foundation scope (#1362): a single model-driven ``/books/<slug>`` detail
view, wired just enough to prove tier gating end to end. #1364 adds per-member
reading state: the ``/books/<slug>`` roadmap gains a mark-read control and a
derived progress bar for members with access, and a server-rendered
POST/redirect/GET toggle endpoint.

#1363 adds the public reading surfaces on top of that foundation:

- ``/books`` — a public discovery hub (never gated, like ``/sprints``) that
  groups books by the operator-set ``Book.status``: the single ``current``
  book is featured, ``upcoming`` and ``finished`` books list, ``draft`` books
  are staff-only previews and ``cancelled`` books are hidden from everyone.
- ``/books/<slug>`` branches on status: ``current`` renders the full detail,
  ``upcoming`` / ``finished`` render a lighter lifecycle page, ``cancelled``
  is a 404, and ``draft`` stays 404-public / 200-staff.

Access reuses ``content.access`` — a viewer sees the participation body iff
``get_user_level(user) >= book.required_level``; otherwise the one canonical
gated-access card renders. Draft books 404 for the public.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.db.models import Count
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.utils.activation import mark_activated
from bookclub.config import get_book_club_slack_url
from bookclub.models import (
    BOOK_STATUS_CANCELLED,
    BOOK_STATUS_CURRENT,
    BOOK_STATUS_DRAFT,
    BOOK_STATUS_FINISHED,
    BOOK_STATUS_UPCOMING,
    READER_VISIBILITY_CHOICES,
    Book,
    ChapterRead,
    Note,
    ReaderProfile,
)
from bookclub.profiles import is_reader_public, public_reader_ids
from bookclub.reading import (
    build_reader_rows,
    viewer_read_numbers,
    viewer_reading_progress,
)
from bookclub.summaries import summary_excerpt, summary_paragraphs
from content.access import build_gated_access_copy, get_user_level
from events.models.event import PUBLIC_EVENT_STATUSES

_VALID_VISIBILITIES = {value for value, _ in READER_VISIBILITY_CHOICES}


def _gate_context(request, book):
    """Context for the canonical ``content/_gated_access_card.html`` partial.

    Delegates all copy to ``content.access.build_gated_access_copy`` (the
    single source of gated copy, issue #1335); we only add the per-surface
    testid, icon, and value manifest. Every Books gate is tier-based.
    """
    copy = build_gated_access_copy(
        gated_reason='insufficient_tier',
        verb='read this book with the community',
        noun='book club',
        required_level=book.required_level,
        user=getattr(request, 'user', None),
        resource_url=request.get_full_path(),
        upgrade_description=(
            'Follow the chapter roadmap, mark chapters read, share notes, and '
            'join the discussions as the group reads this book together.'
        ),
    )
    copy.update({
        'gated_card_testid': 'book-guest-gate',
        'gated_icon': 'book-marked',
        'gated_cta_testid': 'book-guest-gate-cta',
        'gated_value_items': [
            {'icon': 'check-check', 'label': 'Mark chapters read'},
            {'icon': 'pen-line', 'label': 'Share notes'},
            {'icon': 'users', 'label': 'Group discussions'},
        ],
    })
    return copy


def _derive_current_chapter(chapters):
    """Derive the "This week" chapter from a book's ordered chapters.

    Book-level derived state (not per-user): the earliest chapter whose
    ``deadline`` is today or later. If every deadline is past — or no chapter
    has a deadline — fall back to the last chapter so the callout still points
    somewhere useful. With no chapters at all, return ``None`` and the callout
    is omitted. ``chapters`` is assumed to be in ``Chapter.Meta`` order (by
    ``number``), so ``chapters[-1]`` is the final chapter.
    """
    if not chapters:
        return None
    today = timezone.localdate()
    upcoming = [c for c in chapters if c.deadline and c.deadline >= today]
    if upcoming:
        return min(upcoming, key=lambda c: c.deadline)
    return chapters[-1]


def index(request):
    """``/books`` — public Book Club hub (never gated, like ``/sprints``).

    Books are grouped by the operator-set ``Book.status`` (not date-derived):
    the single ``current`` book is featured, ``upcoming`` and ``finished``
    books list in compact grids, ``draft`` books are staff-only previews, and
    ``cancelled`` books are hidden from everyone. If two books are ``current``
    (soft-allowed by the model), the most recent by ``start_date`` is featured.
    """
    is_staff = bool(getattr(request.user, 'is_staff', False))
    statuses = [
        BOOK_STATUS_CURRENT,
        BOOK_STATUS_UPCOMING,
        BOOK_STATUS_FINISHED,
    ]
    if is_staff:
        statuses.append(BOOK_STATUS_DRAFT)

    # One query, ordered so the first ``current`` book is the most recent by
    # kickoff (``order_by`` is explicit because ``annotate`` + GROUP BY does not
    # reliably preserve ``Book.Meta.ordering``). ``num_chapters`` feeds the card
    # bodies without an N+1.
    books = list(
        Book.objects.filter(status__in=statuses)
        .annotate(num_chapters=Count('chapters'))
        .order_by('-start_date', '-created_at')
    )
    current_books = [b for b in books if b.status == BOOK_STATUS_CURRENT]

    context = {
        'featured_book': current_books[0] if current_books else None,
        'upcoming_books': [b for b in books if b.status == BOOK_STATUS_UPCOMING],
        'past_books': [b for b in books if b.status == BOOK_STATUS_FINISHED],
        'draft_books': [b for b in books if b.status == BOOK_STATUS_DRAFT],
    }
    return render(request, 'bookclub/index.html', context)


def book_detail(request, slug):
    """``/books/<slug>`` — model-driven book detail, branched on status.

    ``current`` renders the full participation detail; ``upcoming`` /
    ``finished`` render a lighter lifecycle page; ``cancelled`` is a 404 for
    everyone; ``draft`` stays hidden from the public (404) but staff may still
    preview the full detail. Members with access see the chapter roadmap with
    per-chapter mark-read controls, a derived progress bar (#1364), and a
    derived "This week" callout.
    """
    book = Book.objects.filter(slug=slug).select_related('event_series').first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_CANCELLED:
        raise Http404('This book is not available.')
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    if book.status in (BOOK_STATUS_UPCOMING, BOOK_STATUS_FINISHED):
        return _book_secondary(request, book)

    is_member = get_user_level(request.user) >= book.required_level
    chapters = list(book.chapters.all())

    context = {
        'book': book,
        'chapters': chapters,
        'is_member': is_member,
        'book_club_slack_url': get_book_club_slack_url(),
    }
    if is_member:
        progress = viewer_reading_progress(request.user, book, chapters)
        read_numbers = progress['read_numbers']
        for chapter in chapters:
            chapter.viewer_read = chapter.number in read_numbers
        context.update({
            'viewer_total': progress['total'],
            'viewer_done': progress['done'],
            'viewer_pct': progress['pct'],
            'current_chapter': _derive_current_chapter(chapters),
            # "When we meet" rows (#1369): the linked series' public
            # occurrences, ordered by start. Members only — computed inside the
            # is_member branch so meetings never reach the gated context and
            # cannot leak below the tier gate. Empty/absent series -> [] -> the
            # template hides the section entirely.
            'meetings': _series_meetings(book),
        })
    else:
        context.update(_gate_context(request, book))
    return render(request, 'bookclub/book_detail.html', context)


def _series_meetings(book):
    """Public occurrences of a book's linked series, ordered by start (#1369).

    Returns the same visible-occurrence set (``upcoming`` / ``completed``) the
    public series page lists. Empty list when the book has no series or the
    series has zero public occurrences.
    """
    if not book.event_series_id:
        return []
    return list(
        book.event_series.events
        .filter(status__in=PUBLIC_EVENT_STATUSES)
        .order_by('start_datetime')
    )


def _book_secondary(request, book):
    """Render the lighter lifecycle page for an ``upcoming`` / ``finished`` book.

    This page carries only public lifecycle info (no participation body, no
    gate): the group is not reading this book right now. The full-book summary
    route (``bookclub_book_summary``) now exists (#1368), so a finished book
    links to it; the standings CTA (#1367) is still gated on its own flag. The
    upcoming "join to read along" CTA points to ``/pricing`` only when the
    viewer does not already meet the tier.
    """
    viewer_has_access = get_user_level(request.user) >= book.required_level
    context = {
        'book': book,
        'num_chapters': book.chapters.count(),
        'show_join_cta': not viewer_has_access,
        # #1368 registered the full-book summary route, so the finished recap
        # now links to it (no longer a dead link). ``book_progress_available``
        # stays #1367's coordination flag.
        'book_summary_available': True,
        'book_progress_available': False,
    }
    return render(request, 'bookclub/book_secondary.html', context)


def book_progress(request, slug):
    """``/books/<slug>/progress`` — the calm, you-first Progress board (#1367).

    A read-only roster of the members reading a book together, ordered by
    chapters read (then notes shared, then ``display_name``). No ranks, no
    points, no streaks, no medals, no leaderboard vocabulary — the committed
    ``books.md`` direction retired the prototype's gamification.

    Resolution and gating mirror ``book_detail`` exactly: unknown slug -> 404,
    ``cancelled`` -> 404, ``draft`` -> 404 for non-staff / rendered for staff.
    A viewer sees the board iff ``get_user_level(user) >= book.required_level``
    (``get_user_level`` already elevates staff/superusers to premium, so this
    single check also grants staff their support/preview view — no separate
    staff branch is needed). Anyone below the tier — including anonymous
    visitors — sees the single canonical gated-access card and NO roster, so
    reader identities never leak below the gate. An authenticated viewer with
    access is always pinned first (you-first), even at ``0 of N``; the calm
    empty card is reached only when the board has no rows at all — e.g. an
    open-tier book an anonymous visitor may view but has no readers yet.
    """
    book = Book.objects.filter(slug=slug).first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_CANCELLED:
        raise Http404('This book is not available.')
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    can_view = get_user_level(request.user) >= book.required_level

    context = {'book': book, 'can_view': can_view}
    if not can_view:
        # Below-tier / anonymous: gated card only, never a roster row.
        context.update(_gate_context(request, book))
        return render(request, 'bookclub/progress.html', context)

    reader_rows, distinct_readers = build_reader_rows(
        request.user, book, include_self=True,
    )
    # First-mover nudge: the viewer is pinned but is the only row and has read
    # nothing — welcome them instead of a dead-end empty page.
    first_mover = (
        len(reader_rows) == 1
        and reader_rows[0]['is_self']
        and reader_rows[0]['chapters_read'] == 0
    )
    context.update({
        'reader_rows': reader_rows,
        'distinct_readers': distinct_readers,
        'first_mover': first_mover,
    })
    return render(request, 'bookclub/progress.html', context)


def book_summary(request, slug):
    """``/books/<slug>/summary`` — the compiled full-book summary page (#1368).

    Resolution and gating mirror ``book_detail`` exactly: unknown slug -> 404,
    ``cancelled`` -> 404, ``draft`` -> 404 for non-staff (staff preview). A
    viewer sees the compiled body iff
    ``get_user_level(user) >= book.required_level``; guests / below-tier get
    exactly one ``content/_gated_access_card.html`` and no summary body.

    Three member states:

    - Full-book summary published (or a staff preview of an unpublished draft):
      an "Overall" section plus a "By chapter" list built from every chapter
      that has a published summary, each row linking to that chapter ``#summary``
      anchor.
    - Full-book summary not published but one or more chapter summaries are:
      the "compiled after we finish the book" line plus a "Published so far"
      index of the published chapter summaries.
    - Nothing published yet: the same line with a calm empty state.
    """
    book = Book.objects.filter(slug=slug).first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_CANCELLED:
        raise Http404('This book is not available.')
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    is_member = get_user_level(request.user) >= book.required_level
    context = {'book': book, 'is_member': is_member}
    if not is_member:
        context.update(_gate_context(request, book))
        return render(request, 'bookclub/book_summary.html', context)

    chapters = list(book.chapters.all())
    published_chapters = [c for c in chapters if c.is_summary_published]
    by_chapter_rows = [
        {
            'number': chapter.number,
            'title': chapter.title,
            'label': f'Ch. {chapter.number} — {chapter.title}',
            'url': f'{_chapter_url(book, chapter.number)}#summary',
            'excerpt': summary_excerpt(chapter.summary),
            'published_at': chapter.summary_published_at,
        }
        for chapter in published_chapters
    ]

    overall_published = book.is_summary_published
    # Staff may preview an unpublished full-book draft (body written, not yet
    # published) with a "Draft preview" chip; members never see it.
    overall_draft_preview = (
        is_staff and not overall_published and bool(book.summary.strip())
    )
    show_overall = overall_published or overall_draft_preview

    context.update({
        'show_overall': show_overall,
        'overall_draft_preview': overall_draft_preview,
        'overall_paragraphs': summary_paragraphs(book.summary),
        'by_chapter_rows': by_chapter_rows,
        'has_published_chapters': bool(published_chapters),
    })
    return render(request, 'bookclub/book_summary.html', context)


@require_POST
def chapter_read(request, slug, number):
    """``POST /books/<slug>/chapters/<int:number>/read`` — toggle read state.

    Idempotent set-state driven by the hidden ``read`` field: ``1`` marks
    read (``get_or_create``), ``0`` marks unread (delete if present). A
    repeated submit is a no-op, not a flip. On success, redirect (P/R/G) back
    to the roadmap with a ``#chapter-<number>`` fragment so a browser refresh
    re-issues a GET, never re-posts.

    Access rules mirror the detail-view gate exactly: draft -> 404 for
    non-staff, anonymous -> login redirect (no row written), below-tier ->
    403 (no row written), unknown slug / chapter -> 404.
    """
    book = Book.objects.filter(slug=slug).first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    if not request.user.is_authenticated:
        return redirect_to_login(book.get_absolute_url())

    if get_user_level(request.user) < book.required_level:
        return HttpResponseForbidden('You do not have access to this book.')

    chapter = book.chapters.filter(number=number).first()
    if chapter is None:
        raise Http404('Unknown chapter.')

    if request.POST.get('read') == '1':
        ChapterRead.objects.get_or_create(user=request.user, chapter=chapter)
        # Marking a chapter read is a real platform action (issue #768):
        # flip ``account_activated`` on first mark. Idempotent.
        mark_activated(request.user)
    else:
        ChapterRead.objects.filter(
            user=request.user, chapter=chapter,
        ).delete()

    next_url = request.POST.get('next')
    if not next_url or not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()},
    ):
        next_url = f'{book.get_absolute_url()}#chapter-{number}'
    return redirect(next_url)


def _chapter_url(book, number):
    """Canonical ``/books/<slug>/chapters/<number>`` reader URL."""
    return reverse(
        'bookclub_chapter_detail',
        kwargs={'slug': book.slug, 'number': number},
    )


def chapter_detail(request, slug, number):
    """``/books/<slug>/chapters/<int:number>`` — the per-chapter reader page.

    The route #1362/#1363 deliberately left non-linked; #1365 wires it. Access
    mirrors ``book_detail`` exactly: ``cancelled`` is a 404 for everyone,
    ``draft`` is 404-public / 200-staff, and an unknown chapter number is a
    404. A public chapter header always renders (for discovery/SEO); the
    participation body (own-note composer, group feed, comment threads) is
    gated behind ``get_user_level(user) >= book.required_level`` — guests /
    below-tier members see exactly one ``content/_gated_access_card.html``.
    """
    book = Book.objects.filter(slug=slug).select_related('event_series').first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_CANCELLED:
        raise Http404('This book is not available.')
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    chapters = list(book.chapters.all())
    chapter = next((c for c in chapters if c.number == number), None)
    if chapter is None:
        raise Http404('Unknown chapter.')

    index = chapters.index(chapter)
    prev_chapter = chapters[index - 1] if index > 0 else None
    next_chapter = chapters[index + 1] if index < len(chapters) - 1 else None

    is_member = get_user_level(request.user) >= book.required_level
    context = {
        'book': book,
        'chapter': chapter,
        'is_member': is_member,
        'prev_chapter': prev_chapter,
        'next_chapter': next_chapter,
    }

    if not is_member:
        context.update(_gate_context(request, book))
        return render(request, 'bookclub/book_chapter.html', context)

    # Chapter summary (#1368). A published summary (timestamp set + non-empty
    # body) renders to every member; an unpublished draft renders only to staff
    # with a "Draft" chip so the organizer can preview before publishing. When
    # nothing is published, a member sees the calm placeholder only once the
    # chapter deadline has passed — before that, nothing (no premature promise).
    summary_paras = summary_paragraphs(chapter.summary)
    summary_draft_preview = (
        is_staff
        and not chapter.is_summary_published
        and bool(chapter.summary.strip())
    )
    deadline_passed = (
        chapter.deadline is not None
        and chapter.deadline < timezone.localdate()
    )
    context.update({
        'summary_paragraphs': summary_paras,
        'summary_published': chapter.is_summary_published,
        'summary_draft_preview': summary_draft_preview,
        'summary_deadline_passed': deadline_passed,
    })

    read_numbers = viewer_read_numbers(request.user, book)
    own_note = (
        Note.objects.filter(user=request.user, chapter=chapter).first()
    )

    # Group notes feed: members' notes for this chapter, newest first. #1366
    # flips the private-profile exclusion on: a note whose author is a private,
    # non-self reader is not shown to other members. The viewer's OWN note is
    # always visible to them regardless of their own visibility. One
    # ``public_reader_ids`` query partitions the authors (no N+1).
    all_notes = list(
        Note.objects.filter(chapter=chapter)
        .select_related('user')
        .order_by('-created_at')
    )
    public_author_ids = public_reader_ids({n.user_id for n in all_notes})
    group_notes = [
        note for note in all_notes
        if note.user_id == request.user.id or note.user_id in public_author_ids
    ]
    for note in group_notes:
        note.is_own = note.user_id == request.user.id

    context.update({
        'viewer_read': chapter.number in read_numbers,
        'own_note': own_note,
        'editing': request.GET.get('edit') == '1',
        'group_notes': group_notes,
        'notes_count': len(group_notes),
        'read_count': chapter.reads.count(),
    })
    return render(request, 'bookclub/book_chapter.html', context)


@require_POST
def chapter_note(request, slug, number):
    """``POST /books/<slug>/chapters/<int:number>/note`` — upsert own note.

    Mirrors ``chapter_read``'s gate exactly (draft -> 404 for non-staff,
    anonymous -> login redirect, below-tier -> 403, unknown slug / chapter ->
    404). Upserts the viewer's single ``Note`` for the chapter from POST
    ``body`` (+ optional mermaid ``diagram``); an empty ``body`` deletes the
    note (idempotent clear). The first real save flips ``account_activated``
    via ``mark_activated`` (issue #768). Redirect (P/R/G) back to the chapter
    reader page so a browser refresh re-issues a GET.
    """
    book = Book.objects.filter(slug=slug).first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

    if not request.user.is_authenticated:
        return redirect_to_login(_chapter_url(book, number))

    if get_user_level(request.user) < book.required_level:
        return HttpResponseForbidden('You do not have access to this book.')

    chapter = book.chapters.filter(number=number).first()
    if chapter is None:
        raise Http404('Unknown chapter.')

    body = (request.POST.get('body') or '').strip()
    diagram = (request.POST.get('diagram') or '').strip()

    if not body:
        # Empty submit clears the note (idempotent).
        Note.objects.filter(user=request.user, chapter=chapter).delete()
    else:
        Note.objects.update_or_create(
            user=request.user,
            chapter=chapter,
            defaults={'body': body, 'diagram': diagram},
        )
        # Writing a note is a real platform action (issue #768). Idempotent.
        mark_activated(request.user)

    return redirect(_chapter_url(book, number))


def _resolve_book_or_404(request, slug):
    """Resolve a non-cancelled/non-hidden ``Book`` by slug, mirroring detail.

    Returns ``(book, is_staff)``. ``cancelled`` -> 404 for everyone; ``draft``
    -> 404 for non-staff (staff preview allowed); unknown slug -> 404.
    """
    book = Book.objects.filter(slug=slug).first()
    if book is None:
        raise Http404('Unknown book.')
    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_CANCELLED:
        raise Http404('This book is not available.')
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')
    return book, is_staff


def _reader_profile_url(book, user_id):
    return reverse(
        'bookclub_reader_profile',
        kwargs={'slug': book.slug, 'user_id': user_id},
    )


def reader_profile(request, slug, user_id):
    """``/books/<slug>/readers/<user_id>`` — a member's public reading profile.

    Access mirrors ``book_detail`` exactly, then layers a visibility gate:

    - Draft -> 404 non-staff; cancelled / unknown slug -> 404.
    - The target must be a participant (>= 1 ``ChapterRead`` OR >= 1 ``Note``
      on the book's chapters); a non-participant -> 404.
    - Visibility: a private profile is a 404 to everyone except the owner and
      staff. The message never distinguishes "private" from "does not exist" —
      a private profile's existence is never revealed.
    - Tier gate (mirrors ``book_detail`` / the board): a guest / below-tier
      viewer who passes the visibility gate sees the public header plus exactly
      one gated-access card and NO notes feed — participation content (other
      members' notes) never leaks below the gate. Members with access get the
      chapters-read progress strip, the two stats, and the target's notes.
    - The owner always sees their own profile (public or private) with a
      visibility toggle; staff can view any profile (support/preview).
    """
    book, is_staff = _resolve_book_or_404(request, slug)

    target = get_user_model().objects.filter(pk=user_id).first()
    private_or_missing = Http404(
        "This reader's profile is private or does not exist.",
    )
    if target is None:
        raise private_or_missing

    is_owner = (
        request.user.is_authenticated and request.user.id == target.id
    )

    # A non-participant has no profile to others (404). The owner always
    # reaches their own profile — even at zero reads/notes — so the calm
    # "Your reading profile" link on book detail never dead-ends.
    is_participant = (
        ChapterRead.objects.filter(user=target, chapter__book=book).exists()
        or Note.objects.filter(user=target, chapter__book=book).exists()
    )
    if not is_participant and not is_owner:
        raise private_or_missing

    target_public = is_reader_public(target)
    if not target_public and not is_owner and not is_staff:
        # Never reveal that a private profile exists.
        raise private_or_missing

    is_member = get_user_level(request.user) >= book.required_level
    chapters = list(book.chapters.all())
    read_numbers = set(
        ChapterRead.objects.filter(user=target, chapter__book=book)
        .values_list('chapter__number', flat=True)
    )
    for chapter in chapters:
        chapter.target_read = chapter.number in read_numbers

    context = {
        'book': book,
        'target': target,
        'chapters': chapters,
        'is_member': is_member,
        'is_owner': is_owner,
        'target_public': target_public,
        'chapters_read': len(read_numbers),
        'notes_shared': Note.objects.filter(
            user=target, chapter__book=book,
        ).count(),
    }

    if not is_member:
        # Below-tier / anonymous viewer of a public profile: header + gate,
        # never other members' notes.
        context.update(_gate_context(request, book))
        return render(request, 'bookclub/reader_profile.html', context)

    notes = list(
        Note.objects.filter(user=target, chapter__book=book)
        .select_related('chapter')
        .order_by('-created_at')
    )
    for note in notes:
        note.is_own = note.user_id == request.user.id
    context['notes'] = notes
    return render(request, 'bookclub/reader_profile.html', context)


@require_POST
def reader_visibility(request, slug, user_id):
    """``POST /books/<slug>/readers/<user_id>/visibility`` — owner toggle.

    A preference change (like the newsletter toggle), not a platform action —
    it never calls ``mark_activated``. Gate mirrors ``chapter_read`` (draft ->
    404, anonymous -> login redirect, below-tier -> 403). Only the profile
    owner may flip their own visibility: any attempt to toggle another user is
    a 403. ``get_or_create``s the caller's ``ReaderProfile`` and sets
    ``visibility`` from POST; any value other than the choices is a 400.
    Redirect (P/R/G) back to the owner's profile so a refresh re-issues GET.
    """
    book, _ = _resolve_book_or_404(request, slug)

    if not request.user.is_authenticated:
        return redirect_to_login(_reader_profile_url(book, user_id))

    if get_user_level(request.user) < book.required_level:
        return HttpResponseForbidden('You do not have access to this book.')

    if request.user.id != user_id:
        return HttpResponseForbidden(
            'You can only change your own reading profile.',
        )

    visibility = (request.POST.get('visibility') or '').strip()
    if visibility not in _VALID_VISIBILITIES:
        return HttpResponseBadRequest('Invalid visibility value.')

    ReaderProfile.objects.update_or_create(
        user=request.user,
        defaults={'visibility': visibility},
    )
    return redirect(_reader_profile_url(book, request.user.id))
