"""Studio views for event-series CRUD.

Issue #564. An ``EventSeries`` lets staff create a weekly recurring
series in one form submission. Once created the series' only role is to
keep the events linked together — every member event is an independent
row.

The create form generates N ``Event`` rows 7 days apart and atomically
commits the series + events in a single transaction. Validation errors
roll back the whole flow so the database never holds a partial series.

Deleting the series leaves the member events alive (FK is
``on_delete=SET_NULL``); staff must delete each event explicitly.

Issue #575 renamed ``EventGroup`` to ``EventSeries`` everywhere; this
file moved from ``studio/views/event_groups.py`` to
``studio/views/event_series.py`` as part of that rename.
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from events.models import Event, EventSeries
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

# Sanity guard so a typo in the form cannot generate 1000 events.
MAX_OCCURRENCES = 26


def _parse_date_str(date_str):
    """Parse dd/mm/yyyy into a ``date``."""
    day, month, year = date_str.split('/')
    return datetime(int(year), int(month), int(day)).date()


def _generate_unique_slug(base, used=None):
    """Return a slug that does not collide with an existing Event slug.

    If ``base`` already exists, append ``-2``, ``-3`` … until a free slug
    is found. ``used`` is an optional set of slugs created in the same
    transaction (so two siblings inside the same form submission do not
    overlap before they hit the database).
    """
    used = used or set()
    candidate = base
    suffix = 2
    while (
        candidate in used
        or Event.objects.filter(slug=candidate).exists()
    ):
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


@staff_required
def event_series_list(request):
    """List all event series."""
    series_list = EventSeries.objects.all().order_by('-created_at')
    return render(request, 'studio/event_series/list.html', {
        'series_list': series_list,
    })


@staff_required
def event_series_create(request):
    """Create an EventSeries + N Event rows in one transaction.

    On validation failure the whole submission is rolled back so the
    database never holds a partial series — staff see the form again
    with field-level errors and no orphan rows.

    A day-of-week conflict between the supplied start date and a
    different weekday in the user's intent is not rejected: the start
    date is authoritative. ``day_of_week`` is derived from the date.
    """
    errors = {}
    form_values = {
        'name': '',
        'slug': '',
        'description': '',
        'start_date': '',
        'start_time': '',
        'duration_hours': '1',
        'occurrences': '6',
        'timezone': 'Europe/Berlin',
        'required_level': '0',
        'kind': 'standard',
        'platform': 'zoom',
    }

    if request.method == 'POST':
        for key in form_values:
            form_values[key] = request.POST.get(key, form_values[key]).strip()

        name = form_values['name']
        slug = form_values['slug'] or slugify(name)
        description = form_values['description']
        start_date_str = form_values['start_date']
        start_time_str = form_values['start_time']
        duration_str = form_values['duration_hours'] or '1'
        occurrences_str = form_values['occurrences'] or '0'
        timezone_value = form_values['timezone'] or 'Europe/Berlin'
        required_level_str = form_values['required_level'] or '0'
        kind = form_values['kind'] or 'standard'
        platform = form_values['platform'] or 'zoom'

        if not name:
            errors['name'] = 'Name is required.'

        try:
            start_date = _parse_date_str(start_date_str)
        except (ValueError, AttributeError):
            errors['start_date'] = 'Start date is required (dd/mm/yyyy).'
            start_date = None

        try:
            hour, minute = start_time_str.split(':')
            start_time = datetime(2000, 1, 1, int(hour), int(minute)).time()
        except (ValueError, AttributeError):
            errors['start_time'] = 'Start time is required (HH:MM, 24h).'
            start_time = None

        try:
            duration_hours = float(duration_str)
            if duration_hours <= 0:
                raise ValueError
        except ValueError:
            errors['duration_hours'] = 'Duration must be a positive number.'
            duration_hours = None

        try:
            occurrences = int(occurrences_str)
        except ValueError:
            errors['occurrences'] = 'Occurrences must be a whole number.'
            occurrences = None
        else:
            if occurrences < 1:
                errors['occurrences'] = (
                    'Occurrences must be at least 1.'
                )
            elif occurrences > MAX_OCCURRENCES:
                errors['occurrences'] = (
                    f'Occurrences cannot exceed {MAX_OCCURRENCES}.'
                )

        try:
            required_level = int(required_level_str)
        except ValueError:
            required_level = 0

        if slug and EventSeries.objects.filter(slug=slug).exists():
            errors['slug'] = 'A series with this slug already exists.'

        if not errors:
            with transaction.atomic():
                series = EventSeries(
                    name=name,
                    slug=slug,
                    description=description,
                    cadence='weekly',
                    cadence_weeks=1,
                    day_of_week=start_date.weekday(),
                    start_time=start_time,
                    timezone=timezone_value,
                )
                series.save()

                used_slugs = set()
                for i in range(1, occurrences + 1):
                    event_start = datetime.combine(
                        start_date + timedelta(days=7 * (i - 1)),
                        start_time,
                    )
                    event_end = event_start + timedelta(hours=duration_hours)

                    base_slug = f'{series.slug}-session-{i}'
                    event_slug = _generate_unique_slug(base_slug, used_slugs)
                    used_slugs.add(event_slug)

                    Event.objects.create(
                        title=f'{series.name} — Session {i}',
                        slug=event_slug,
                        description='',
                        kind=kind,
                        platform=platform,
                        start_datetime=event_start,
                        end_datetime=event_end,
                        timezone=timezone_value,
                        status='draft',
                        required_level=required_level,
                        origin='studio',
                        event_series=series,
                        series_position=i,
                        published=True,
                    )
            return redirect('studio_event_series_detail', series_id=series.pk)

    return render(request, 'studio/event_series/form.html', {
        'form_values': form_values,
        'errors': errors,
        'max_occurrences': MAX_OCCURRENCES,
    })


@staff_required
def event_series_detail(request, series_id):
    """Detail page: lists every member event with edit/delete links."""
    series = get_object_or_404(EventSeries, pk=series_id)

    if request.method == 'POST':
        # Inline edit of series metadata only — schedule fields stay
        # immutable on the series (per-event edits handle drift).
        series.name = request.POST.get('name', series.name).strip() or series.name
        new_slug = request.POST.get('slug', series.slug).strip()
        if new_slug and new_slug != series.slug:
            if not EventSeries.objects.filter(slug=new_slug).exclude(pk=series.pk).exists():
                series.slug = new_slug
        series.description = request.POST.get('description', series.description)
        series.save()
        return redirect('studio_event_series_detail', series_id=series.pk)

    events = series.events.all().order_by(
        'series_position', 'start_datetime',
    )
    return render(request, 'studio/event_series/detail.html', {
        'series': series,
        'events': events,
    })


@staff_required
@require_POST
def event_series_add_occurrence(request, series_id):
    """Append one more event to the series with the next series_position."""
    series = get_object_or_404(EventSeries, pk=series_id)
    start_date_str = request.POST.get('start_date', '').strip()
    duration_str = request.POST.get('duration_hours', '').strip() or '1'

    try:
        start_date = _parse_date_str(start_date_str)
    except (ValueError, AttributeError):
        # Re-render the detail page with a flash-style error.
        events = series.events.all().order_by('series_position', 'start_datetime')
        return render(request, 'studio/event_series/detail.html', {
            'series': series,
            'events': events,
            'add_error': 'Start date is required (dd/mm/yyyy).',
        }, status=400)

    try:
        duration_hours = float(duration_str)
        if duration_hours <= 0:
            raise ValueError
    except ValueError:
        duration_hours = 1.0

    max_pos = (
        series.events.exclude(series_position__isnull=True)
        .order_by('-series_position').values_list('series_position', flat=True)
        .first()
    )
    next_pos = (max_pos or 0) + 1

    event_start = datetime.combine(start_date, series.start_time)
    event_end = event_start + timedelta(hours=duration_hours)

    base_slug = f'{series.slug}-session-{next_pos}'
    event_slug = _generate_unique_slug(base_slug)

    Event.objects.create(
        title=f'{series.name} — Session {next_pos}',
        slug=event_slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=event_start,
        end_datetime=event_end,
        timezone=series.timezone,
        status='draft',
        required_level=0,
        origin='studio',
        event_series=series,
        series_position=next_pos,
        published=True,
    )
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def event_series_delete(request, series_id):
    """Delete the series; ``SET_NULL`` preserves the member events."""
    series = get_object_or_404(EventSeries, pk=series_id)
    series.delete()
    return redirect('studio_event_list')
