"""Studio views for managing Book Club books and chapters (issue #1362).

Mirrors the Community Sprints Studio surface (``studio/views/sprints.py``):
staff-only list / create / detail / edit / delete for :class:`bookclub.Book`,
plus inline add / edit / delete / reorder of :class:`bookclub.Chapter` rows
from the book detail page.

All views are staff-only (``studio.decorators.staff_required``).
"""

import json
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from bookclub.models import (
    BOOK_STATUS_CHOICES,
    BOOK_STATUS_CURRENT,
    Book,
    Chapter,
)
from content.access import LEVEL_MAIN
from content.access import VISIBILITY_CHOICES as TIER_LEVEL_CHOICES
from events.models import Event, EventSeries
from events.models.event import PUBLIC_EVENT_STATUSES
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

# Valid tier level integers accepted by the form, mirroring
# ``content.access.VISIBILITY_CHOICES`` so the dropdown stays consistent.
_VALID_TIER_LEVELS = {value for value, _label in TIER_LEVEL_CHOICES}


def _parse_required_level(raw):
    """Parse the ``required_level`` form field. Returns ``(value, error)``."""
    if raw in (None, ''):
        return LEVEL_MAIN, ''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Required tier level must be a whole number.'
    if value not in _VALID_TIER_LEVELS:
        return None, 'Required tier level must be one of 0, 10, 20, 30.'
    return value, ''


def _parse_event_series(raw):
    """Parse the ``event_series`` field. Returns ``(EventSeries|None, error)``.

    Empty -> unlinked. A numeric value resolves by pk; a non-numeric string by
    slug (the API shares this behaviour). Unknown id/slug is an error so the
    caller re-renders with 400 and no write happens.
    """
    if raw in (None, ''):
        return None, ''
    if isinstance(raw, bool):
        return None, 'Selected event series does not exist.'
    if isinstance(raw, int):
        series = EventSeries.objects.filter(pk=raw).first()
    elif isinstance(raw, str) and raw.lstrip('-').isdigit():
        series = EventSeries.objects.filter(pk=int(raw)).first()
    elif isinstance(raw, str):
        series = EventSeries.objects.filter(slug=raw).first()
    else:
        return None, 'Selected event series does not exist.'
    if series is None:
        return None, 'Selected event series does not exist.'
    return series, ''


def _parse_chapter_event(raw):
    """Parse the chapter ``event`` form field. Returns ``(Event|None, error)``.

    Empty -> detach (``None``). A numeric value resolves by pk; a non-numeric
    string by slug (the admin API shares this behaviour). Unknown id/slug is an
    error so the caller re-renders without a write. The series-only rule is
    enforced separately by ``Chapter.clean()``.
    """
    if raw in (None, ''):
        return None, ''
    if isinstance(raw, int) and not isinstance(raw, bool):
        event = Event.objects.filter(pk=raw).first()
    elif isinstance(raw, str) and raw.lstrip('-').isdigit():
        event = Event.objects.filter(pk=int(raw)).first()
    elif isinstance(raw, str):
        event = Event.objects.filter(slug=raw).first()
    else:
        return None, 'Selected event does not exist.'
    if event is None:
        return None, 'Selected event does not exist.'
    return event, ''


def _parse_optional_date(raw, *, field_label):
    """Parse an optional ``YYYY-MM-DD`` date. Returns ``(date|None, error)``."""
    if not raw:
        return None, ''
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date(), ''
    except ValueError:
        return None, f'{field_label} must be in YYYY-MM-DD format.'


def _normalize_status(raw):
    valid = {choice[0] for choice in BOOK_STATUS_CHOICES}
    return raw if raw in valid else 'draft'


def _form_data_from_post(request):
    return {
        'title': (request.POST.get('title') or '').strip(),
        'slug': (request.POST.get('slug') or '').strip(),
        'subtitle': (request.POST.get('subtitle') or '').strip(),
        'author': (request.POST.get('author') or '').strip(),
        'description': (request.POST.get('description') or '').strip(),
        'cover_image_url': (request.POST.get('cover_image_url') or '').strip(),
        'cover_accent': (request.POST.get('cover_accent') or '').strip(),
        'required_level': (request.POST.get('required_level') or '').strip(),
        'status': (request.POST.get('status') or '').strip(),
        'start_date': (request.POST.get('start_date') or '').strip(),
        'meeting_cadence': (request.POST.get('meeting_cadence') or '').strip(),
        'buy_url': (request.POST.get('buy_url') or '').strip(),
        'event_series': (request.POST.get('event_series') or '').strip(),
    }


def _form_data_from_book(book):
    return {
        'title': book.title,
        'slug': book.slug,
        'subtitle': book.subtitle,
        'author': book.author,
        'description': book.description,
        'cover_image_url': book.cover_image_url,
        'cover_accent': book.cover_accent,
        'required_level': str(book.required_level),
        'status': book.status,
        'start_date': book.start_date.isoformat() if book.start_date else '',
        'meeting_cadence': book.meeting_cadence,
        'buy_url': book.buy_url,
        'event_series': (
            str(book.event_series_id) if book.event_series_id else ''
        ),
    }


def _render_form(request, *, book, form_action, form_data, error='', status=200):
    context = {
        'book': book,
        'form_action': form_action,
        'form_data': form_data,
        'status_choices': BOOK_STATUS_CHOICES,
        'tier_level_choices': TIER_LEVEL_CHOICES,
        'event_series_list': EventSeries.objects.all().order_by('name'),
        'error': error,
        'primary_label': 'Save changes' if form_action == 'edit' else 'Create book',
    }
    return render(request, 'studio/books/form.html', context, status=status)


@staff_required
def book_list(request):
    """Table of books with author, slug, status, tier, chapter count, start."""
    search = (request.GET.get('q') or '').strip()
    books = (
        Book.objects
        .annotate(chapter_count=Count('chapters'))
        .order_by('-start_date', '-created_at')
    )
    if search:
        books = books.filter(
            Q(title__icontains=search)
            | Q(author__icontains=search)
            | Q(slug__icontains=search)
            | Q(status__icontains=search)
        )
    pager = studio_pagination_context(request, books)
    return render(request, 'studio/books/list.html', {
        'books': list(pager['page'].object_list),
        'search': search,
        'tier_level_labels': dict(TIER_LEVEL_CHOICES),
        **pager,
    })


def _apply_book_fields(book, *, form_data, required_level, status_value,
                       start_date, event_series):
    book.title = form_data['title']
    book.subtitle = form_data['subtitle']
    book.author = form_data['author']
    book.description = form_data['description']
    book.cover_image_url = form_data['cover_image_url']
    book.cover_accent = form_data['cover_accent'] or 'from-accent/30'
    book.required_level = required_level
    book.status = status_value
    book.start_date = start_date
    book.meeting_cadence = form_data['meeting_cadence']
    book.buy_url = form_data['buy_url']
    book.event_series = event_series


def _warn_second_current(request, book):
    """Non-blocking warning if another book is already ``current``."""
    if book.status != BOOK_STATUS_CURRENT:
        return
    others = (
        Book.objects
        .filter(status=BOOK_STATUS_CURRENT)
        .exclude(pk=book.pk)
    )
    other = others.first()
    if other is not None:
        messages.warning(
            request,
            f'"{other.title}" is also set to Current. Only one book should be '
            'the current read at a time.',
        )


@staff_required
def book_create(request):
    """Form to create a book."""
    if request.method != 'POST':
        return _render_form(
            request,
            book=None,
            form_action='create',
            form_data={
                'title': '', 'slug': '', 'subtitle': '', 'author': '',
                'description': '', 'cover_image_url': '',
                'cover_accent': 'from-accent/30',
                'required_level': str(LEVEL_MAIN), 'status': 'draft',
                'start_date': '', 'meeting_cadence': '', 'buy_url': '',
                'event_series': '',
            },
        )

    form_data = _form_data_from_post(request)
    title = form_data['title']
    required_level, tier_error = _parse_required_level(form_data['required_level'])
    status_value = _normalize_status(form_data['status'])
    start_date, date_error = _parse_optional_date(
        form_data['start_date'], field_label='Kickoff date',
    )
    event_series, series_error = _parse_event_series(form_data['event_series'])

    def _reject(error):
        return _render_form(
            request, book=None, form_action='create',
            form_data=form_data, error=error, status=400,
        )

    if not title:
        return _reject('Title is required.')
    if not form_data['author']:
        return _reject('Author is required.')
    slug = form_data['slug'] or slugify(title)
    if not slug:
        return _reject('Slug could not be derived from the title.')
    if tier_error:
        return _reject(tier_error)
    if date_error:
        return _reject(date_error)
    if series_error:
        return _reject(series_error)
    if Book.objects.filter(slug=slug).exists():
        return _reject(
            f'A book with slug "{slug}" already exists. Pick a different slug.'
        )

    book = Book(slug=slug)
    _apply_book_fields(
        book, form_data=form_data, required_level=required_level,
        status_value=status_value, start_date=start_date,
        event_series=event_series,
    )
    book.save()
    _warn_second_current(request, book)
    messages.success(request, f'Book "{book.title}" created.')
    return redirect('studio_book_detail', book_id=book.pk)


@staff_required
def book_edit(request, book_id):
    """Edit a book's fields."""
    book = get_object_or_404(Book, pk=book_id)
    if request.method != 'POST':
        return _render_form(
            request, book=book, form_action='edit',
            form_data=_form_data_from_book(book),
        )

    form_data = _form_data_from_post(request)
    title = form_data['title']
    required_level, tier_error = _parse_required_level(form_data['required_level'])
    status_value = _normalize_status(form_data['status'])
    start_date, date_error = _parse_optional_date(
        form_data['start_date'], field_label='Kickoff date',
    )
    event_series, series_error = _parse_event_series(form_data['event_series'])

    def _reject(error):
        return _render_form(
            request, book=book, form_action='edit',
            form_data=form_data, error=error, status=400,
        )

    if not title:
        return _reject('Title is required.')
    if not form_data['author']:
        return _reject('Author is required.')
    slug = form_data['slug'] or slugify(title)
    if not slug:
        return _reject('Slug could not be derived from the title.')
    if tier_error:
        return _reject(tier_error)
    if date_error:
        return _reject(date_error)
    if series_error:
        return _reject(series_error)
    if Book.objects.filter(slug=slug).exclude(pk=book.pk).exists():
        return _reject(f'A different book already uses slug "{slug}".')

    book.slug = slug
    _apply_book_fields(
        book, form_data=form_data, required_level=required_level,
        status_value=status_value, start_date=start_date,
        event_series=event_series,
    )
    book.save()
    _warn_second_current(request, book)
    messages.success(request, f'Book "{book.title}" updated.')
    return redirect('studio_book_detail', book_id=book.pk)


@staff_required
def book_detail(request, book_id):
    """Book fields + the ordered chapter list with inline management."""
    book = get_object_or_404(
        Book.objects.select_related('event_series'), pk=book_id,
    )
    chapters = list(book.chapters.select_related('event').all())
    # The chapter-event selector lists only the book's own series occurrences
    # (issue #1369). Empty list when no series is linked -> the selector is
    # disabled with a hint so an arbitrary event can never be attached.
    if book.event_series_id:
        series_events = list(
            book.event_series.events
            .filter(status__in=PUBLIC_EVENT_STATUSES)
            .order_by('start_datetime')
        )
    else:
        series_events = []
    return render(request, 'studio/books/detail.html', {
        'book': book,
        'chapters': chapters,
        'series_events': series_events,
        'tier_level_labels': dict(TIER_LEVEL_CHOICES),
        'chapter_error': request.session.pop('book_chapter_error', ''),
    })


@staff_required
@require_POST
def book_delete(request, book_id):
    """Hard-delete a book (cascades to chapters). Confirm-gated in the UI."""
    book = get_object_or_404(Book, pk=book_id)
    title = book.title
    book.delete()
    messages.success(request, f'Book "{title}" and its chapters were deleted.')
    return redirect('studio_book_list')


def _parse_chapter_number(raw):
    """Parse a chapter ``number``. Returns ``(int|None, error)``."""
    if raw in (None, ''):
        return None, 'Chapter number is required.'
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Chapter number must be a whole number.'
    if value < 0:
        return None, 'Chapter number must be 0 or greater.'
    return value, ''


@staff_required
@require_POST
def book_chapter_create(request, book_id):
    """Add a chapter to a book. Friendly error on duplicate number."""
    book = get_object_or_404(Book, pk=book_id)
    number, number_error = _parse_chapter_number(request.POST.get('number'))
    title = (request.POST.get('title') or '').strip()
    deadline, date_error = _parse_optional_date(
        (request.POST.get('deadline') or '').strip(), field_label='Deadline',
    )
    week_label = (request.POST.get('week_label') or '').strip()
    event, event_error = _parse_chapter_event(
        (request.POST.get('event') or '').strip(),
    )

    error = ''
    if number_error:
        error = number_error
    elif not title:
        error = 'Chapter title is required.'
    elif date_error:
        error = date_error
    elif event_error:
        error = event_error
    elif Chapter.objects.filter(book=book, number=number).exists():
        error = f'This book already has a chapter numbered {number}.'

    chapter = Chapter(
        book=book, number=number, title=title,
        deadline=deadline, week_label=week_label, event=event,
    )
    if not error:
        try:
            chapter.clean()
        except ValidationError as exc:
            error = exc.message_dict.get('event', ['Invalid event.'])[0]

    if error:
        request.session['book_chapter_error'] = error
        return redirect('studio_book_detail', book_id=book.pk)

    chapter.save()
    messages.success(request, f'Chapter {number} — "{title}" added.')
    return redirect('studio_book_detail', book_id=book.pk)


@staff_required
@require_POST
def book_chapter_edit(request, book_id, chapter_id):
    """Edit a chapter's fields. Friendly error on duplicate number."""
    book = get_object_or_404(Book, pk=book_id)
    chapter = get_object_or_404(Chapter, pk=chapter_id, book=book)
    number, number_error = _parse_chapter_number(request.POST.get('number'))
    title = (request.POST.get('title') or '').strip()
    deadline, date_error = _parse_optional_date(
        (request.POST.get('deadline') or '').strip(), field_label='Deadline',
    )
    week_label = (request.POST.get('week_label') or '').strip()
    event, event_error = _parse_chapter_event(
        (request.POST.get('event') or '').strip(),
    )

    error = ''
    if number_error:
        error = number_error
    elif not title:
        error = 'Chapter title is required.'
    elif date_error:
        error = date_error
    elif event_error:
        error = event_error
    elif Chapter.objects.filter(book=book, number=number).exclude(pk=chapter.pk).exists():
        error = f'This book already has a chapter numbered {number}.'

    chapter.number = number
    chapter.title = title
    chapter.deadline = deadline
    chapter.week_label = week_label
    chapter.event = event
    if not error:
        try:
            chapter.clean()
        except ValidationError as exc:
            error = exc.message_dict.get('event', ['Invalid event.'])[0]

    if error:
        request.session['book_chapter_error'] = error
        return redirect('studio_book_detail', book_id=book.pk)

    chapter.save()
    messages.success(request, f'Chapter {number} — "{title}" updated.')
    return redirect('studio_book_detail', book_id=book.pk)


@staff_required
@require_POST
def book_chapter_delete(request, book_id, chapter_id):
    """Delete a single chapter."""
    book = get_object_or_404(Book, pk=book_id)
    chapter = get_object_or_404(Chapter, pk=chapter_id, book=book)
    number = chapter.number
    chapter.delete()
    messages.success(request, f'Chapter {number} deleted.')
    return redirect('studio_book_detail', book_id=book.pk)


@staff_required
def book_chapter_reorder(request, book_id):
    """Reorder chapters (JSON POST of ``[{id, number}, ...]``).

    Mirrors the course module/unit reorder pattern. Because ``number`` is the
    unique ordering key, the two-phase transaction below offsets every row out
    of range first so no intermediate assignment collides with the
    ``(book, number)`` unique constraint before the final numbers land.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    book = get_object_or_404(Book, pk=book_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    chapter_ids = [item['id'] for item in data]
    chapters = {c.pk: c for c in Chapter.objects.filter(book=book, pk__in=chapter_ids)}
    offset = 100000
    with transaction.atomic():
        for chapter in chapters.values():
            Chapter.objects.filter(pk=chapter.pk).update(number=chapter.number + offset)
        for item in data:
            chapter = chapters.get(item['id'])
            if chapter is not None:
                Chapter.objects.filter(pk=chapter.pk).update(number=item['number'])
    return JsonResponse({'status': 'ok'})
