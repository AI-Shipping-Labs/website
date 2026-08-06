"""Public Book Club views (issues #1362, #1364).

Foundation scope (#1362): a single model-driven ``/books/<slug>`` detail
view, wired just enough to prove tier gating end to end. #1364 adds per-member
reading state: the ``/books/<slug>`` roadmap gains a mark-read control and a
derived progress bar for members with access, and a server-rendered
POST/redirect/GET toggle endpoint.

Access reuses ``content.access`` — a viewer sees the participation body iff
``get_user_level(user) >= book.required_level``; otherwise the one canonical
gated-access card renders. Draft books 404 for the public.
"""

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.utils.activation import mark_activated
from bookclub.config import get_book_club_slack_url
from bookclub.models import BOOK_STATUS_DRAFT, Book, ChapterRead
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


def book_detail(request, slug):
    """``/books/<slug>`` — model-driven book detail with tier gating.

    Draft books are hidden from the public (404); staff may still preview.
    Members with access see the chapter roadmap with per-chapter mark-read
    controls and a derived progress bar (#1364).
    """
    book = Book.objects.filter(slug=slug).select_related('event_series').first()
    if book is None:
        raise Http404('Unknown book.')

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if book.status == BOOK_STATUS_DRAFT and not is_staff:
        raise Http404('This book is not published.')

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
        })
    else:
        context.update(_gate_context(request, book))
    return render(request, 'bookclub/book_detail.html', context)


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
