"""Public Book Club views (issue #1362).

Foundation scope: a single model-driven ``/books/<slug>`` detail view, wired
just enough to prove tier gating end to end. The full hub/detail/chapter
rebuild is #1363+. Access reuses ``content.access`` — a viewer sees the
participation body iff ``get_user_level(user) >= book.required_level``;
otherwise the one canonical gated-access card renders. Draft books 404 for
the public.
"""

from django.http import Http404
from django.shortcuts import render

from bookclub.config import get_book_club_slack_url
from bookclub.models import BOOK_STATUS_DRAFT, Book
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
    if not is_member:
        context.update(_gate_context(request, book))
    return render(request, 'bookclub/book_detail.html', context)
