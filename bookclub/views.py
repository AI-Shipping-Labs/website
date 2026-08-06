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

from django.contrib.auth.views import redirect_to_login
from django.db.models import Count
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
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
    Book,
    ChapterRead,
)
from bookclub.reading import viewer_reading_progress
from content.access import build_gated_access_copy, get_user_level


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
        })
    else:
        context.update(_gate_context(request, book))
    return render(request, 'bookclub/book_detail.html', context)


def _book_secondary(request, book):
    """Render the lighter lifecycle page for an ``upcoming`` / ``finished`` book.

    This page carries only public lifecycle info (no participation body, no
    gate): the group is not reading this book right now. The summary and
    standings CTAs target routes that do not exist yet (#1368 / #1367), so they
    are absent here — ``book_summary_available`` / ``book_progress_available``
    are the coordination flags those issues flip when they add the routes and
    the CTA markup. The upcoming "join to read along" CTA points to ``/pricing``
    only when the viewer does not already meet the tier.
    """
    viewer_has_access = get_user_level(request.user) >= book.required_level
    context = {
        'book': book,
        'num_chapters': book.chapters.count(),
        'show_join_cta': not viewer_has_access,
        # Flipped by #1368 (summary) / #1367 (progress board) when those routes
        # and their CTA markup land. False here keeps the page dead-link free.
        'book_summary_available': False,
        'book_progress_available': False,
    }
    return render(request, 'bookclub/book_secondary.html', context)


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
