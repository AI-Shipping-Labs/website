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
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from content.access import VISIBILITY_CHOICES
from events.models import Event, EventSeries
from events.services.series_registration import (
    enroll_series_registrants_in_event,
)
from events.tasks.create_series_zoom_meetings import (
    eligible_occurrence_count,
    enqueue_create_series_zoom_meetings,
)
from integrations.services.banner_generator import (
    is_enabled as banner_generator_is_enabled,
)
from integrations.services.banner_generator.dispatch import (
    enqueue_force,
    enqueue_if_missing,
)
from notifications.models import Notification
from notifications.services import (
    NotificationService,
    get_series_notification_eligible_user_count,
)
from notifications.services.notification_service import series_notification_title
from notifications.services.slack_announcements import (
    post_series_slack_announcement,
)
from studio.decorators import staff_required
from studio.services.banner_panel import banner_panel_context
from studio.utils import studio_pagination_context
from studio.views.events import (
    _default_timezone_for,
    _should_autodetect_tz,
    annotate_derived_status,
)

logger = logging.getLogger(__name__)

# Sanity guard so a typo in the form cannot generate 1000 events.
MAX_OCCURRENCES = 26

# Issue #958: valid occurrence/series access levels.
_VALID_REQUIRED_LEVELS = {value for value, _label in VISIBILITY_CHOICES}


def _series_notification_context(series):
    return {
        'series_notify_audience_count': (
            get_series_notification_eligible_user_count(series)
        ),
    }


def _parse_date_str(date_str):
    """Parse dd/mm/yyyy into a ``date``."""
    day, month, year = date_str.split('/')
    return datetime(int(year), int(month), int(day)).date()


def _localize_to_utc(local_date, local_time, tz_name):
    """Combine ``(date, time)`` interpreted in ``tz_name`` and convert to UTC.

    Issue #665. The Studio picker captures wall-clock values in the
    admin's chosen timezone; storage is always UTC so the calendar
    arithmetic on member events stays stable across DST.
    """
    zone = ZoneInfo(tz_name) if is_valid_timezone(tz_name) else ZoneInfo('UTC')
    local_aware = datetime.combine(local_date, local_time).replace(tzinfo=zone)
    return local_aware.astimezone(ZoneInfo('UTC'))


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
    search = (request.GET.get('q') or '').strip()
    series_list = EventSeries.objects.all().order_by('-created_at')
    if search:
        series_list = series_list.filter(
            Q(name__icontains=search)
            | Q(slug__icontains=search)
            | Q(cadence__icontains=search)
            | Q(timezone__icontains=search)
        )
    pager = studio_pagination_context(request, series_list)
    return render(request, 'studio/event_series/list.html', {
        'series_list': pager['page'].object_list,
        'search': search,
        **pager,
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
    default_tz = _default_timezone_for(request.user)
    form_values = {
        'name': '',
        'slug': '',
        'description': '',
        'start_date': '',
        'start_time': '',
        'duration_hours': '1',
        'occurrences': '6',
        # Issue #665: default to admin's preferred TZ, never 'Europe/Berlin'.
        'timezone': default_tz,
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
        # Issue #665: validate the TZ; reject unknown IANA names.
        posted_tz = form_values['timezone'] or default_tz
        if posted_tz and not is_valid_timezone(posted_tz):
            errors['timezone'] = 'Unknown timezone.'
            timezone_value = default_tz
        else:
            timezone_value = posted_tz
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
                    day_of_week=start_date.weekday(),
                    start_time=start_time,
                    timezone=timezone_value,
                    # Issue #958: the chosen level is the canonical series
                    # level AND is stamped on each generated occurrence.
                    required_level=required_level,
                )
                series.save()

                used_slugs = set()
                for i in range(1, occurrences + 1):
                    occurrence_date = start_date + timedelta(days=7 * (i - 1))
                    event_start = _localize_to_utc(
                        occurrence_date, start_time, timezone_value,
                    )
                    event_end = event_start + timedelta(hours=duration_hours)

                    base_slug = f'{series.slug}-session-{i}'
                    event_slug = _generate_unique_slug(base_slug, used_slugs)
                    used_slugs.add(event_slug)

                    occurrence = Event.objects.create(
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
                    # Issue #857: shared auto-enroll hook. A freshly
                    # created series has no registrants yet, so this is a
                    # no-op here — wired for consistency with the other
                    # occurrence-creation paths.
                    enroll_series_registrants_in_event(occurrence)
            # Issue #896: enqueue an auto-banner render for the new series
            # (fire-and-forget; no-ops when banner-generator is disabled).
            enqueue_if_missing('event_series', series.pk)
            return redirect('studio_event_series_detail', series_id=series.pk)

    tz_value = form_values['timezone'] or default_tz
    return render(request, 'studio/event_series/form.html', {
        'form_values': form_values,
        'errors': errors,
        'max_occurrences': MAX_OCCURRENCES,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        'tz_settings_link': _should_autodetect_tz(request.user),
        # Issue #855: with no saved preference and a fresh GET, default the
        # picker to the browser zone instead of UTC. A re-rendered POST
        # carries the admin's chosen value, so don't auto-detect then.
        'tz_autodetect': (
            _should_autodetect_tz(request.user)
            and request.method != 'POST'
        ),
    })


def _propagate_series_to_children(series):
    """Push the series slug + description down to every child event.

    Issue #854 Part B. Called only when the staff member ticks the
    "Propagate the changes to the events" checkbox — it is the explicit
    "I accept overwrites" signal. With the box UNCHECKED this is never
    called, so manual per-event slug/description edits survive.

    When checked, EVERY linked child is overwritten regardless of whether
    its slug/description was hand-edited:

    - description: set to the series ``description``; ``Event.save()``
      re-renders ``description_html``.
    - slug: regenerated from the new series slug, preserving the
      occurrence's ``series_position``. Base is
      ``"{series.slug}-session-{series_position}"`` (or
      ``"{series.slug}-session"`` when ``series_position`` is null),
      de-duplicated via ``_dedup_sibling_slug`` against this series' own
      children only. A collision with an UNRELATED event (one not linked
      to this series) is NOT dodged — it surfaces as an ``IntegrityError``
      at ``save()`` and rolls the whole save back (the caller wraps this
      in ``transaction.atomic``).

    De-dup rule: sibling occurrences within the same series get a numeric
    suffix to stay unique relative to each other (the canonical
    ``-session-{n}`` scheme already keeps them distinct, but a null
    ``series_position`` collapses several children onto the same base).
    A generated slug that collides with an UNRELATED event (one not
    linked to this series) is treated as a hard failure: ``save()`` hits
    the unique constraint and raises ``IntegrityError``, which the caller
    catches to roll the whole propagation back. We do NOT silently mutate
    an organizer's chosen slug to dodge an external collision — staff see
    the error and pick a different series slug.

    Returns the count of child events updated.
    """
    children = list(
        series.events.all().order_by('series_position', 'start_datetime'),
    )
    child_ids = {child.pk for child in children}
    used = set()
    for child in children:
        if child.series_position:
            base_slug = f'{series.slug}-session-{child.series_position}'
        else:
            base_slug = f'{series.slug}-session'
        # De-dup only against this series' own children (siblings re-slugged
        # in the same pass). A collision with an event OUTSIDE the series is
        # intentionally NOT dodged — it surfaces as an IntegrityError on
        # save() and rolls the propagation back.
        new_slug = _dedup_sibling_slug(base_slug, used, child_ids)
        used.add(new_slug)
        child.slug = new_slug
        child.description = series.description
        child.save()
    return len(children)


def _dedup_sibling_slug(base, used, sibling_ids):
    """Disambiguate a child slug against its siblings only.

    Issue #854 Part B: numeric suffixes resolve collisions among the
    series' own children (and against the children's about-to-be-replaced
    old slugs). Collisions with events outside ``sibling_ids`` are left
    alone so they raise at ``save()`` for an atomic rollback.
    """
    candidate = base
    suffix = 2
    while (
        candidate in used
        or Event.objects.filter(slug=candidate)
        .filter(pk__in=sibling_ids)
        .exists()
    ):
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


@staff_required
def event_series_detail(request, series_id):
    """Detail page: lists every member event with edit/delete links.

    Issue #854 Part B: the metadata POST can opt in to propagating the
    series slug + description down to every child event via the
    "Propagate the changes to the events" checkbox. Default UNCHECKED:
    only the ``EventSeries`` row is updated and manual child edits
    survive. CHECKED is an explicit "I accept overwrites" signal — every
    linked child's slug and description is regenerated regardless of any
    hand-edits. The series save + child propagation run in a single
    ``transaction.atomic`` so a slug collision rolls the whole thing back.
    """
    series = get_object_or_404(EventSeries, pk=series_id)

    if request.method == 'POST':
        # Inline edit of series metadata only — schedule fields stay
        # immutable on the series (per-event edits handle drift).
        propagate = request.POST.get('propagate') == 'on'
        try:
            with transaction.atomic():
                series.name = (
                    request.POST.get('name', series.name).strip()
                    or series.name
                )
                new_slug = request.POST.get('slug', series.slug).strip()
                if new_slug and new_slug != series.slug:
                    collision = (
                        EventSeries.objects.filter(slug=new_slug)
                        .exclude(pk=series.pk).exists()
                    )
                    if not collision:
                        series.slug = new_slug
                series.description = request.POST.get(
                    'description', series.description,
                )
                # Issue #958: staff may edit the canonical series level here.
                # Editing it does NOT rewrite existing occurrences — it only
                # changes what future added occurrences inherit/validate
                # against. A tampered/unknown value is ignored.
                posted_level = (
                    request.POST.get('required_level') or ''
                ).strip()
                if posted_level:
                    try:
                        new_level = int(posted_level)
                    except ValueError:
                        new_level = None
                    if new_level in _VALID_REQUIRED_LEVELS:
                        series.required_level = new_level
                # Issue #858: "Visible to the public" toggle. The metadata
                # form always submits the checkbox field, so an unchecked box
                # (absent in POST) means hide. Default stays True for series
                # created before this field existed.
                series.is_active = request.POST.get('is_active') == 'on'
                series.save()
                if propagate:
                    updated = _propagate_series_to_children(series)
        except IntegrityError:
            # A child slug collision rolled the whole save back. Re-render
            # the detail page with an error; the series slug is unchanged.
            series.refresh_from_db()
            # Issue #957: chronological order, matching the main detail render
            # and the public series page.
            events = list(series.events.all().order_by(
                'start_datetime', 'id',
            ))
            now = dj_timezone.now()
            for event in events:
                annotate_derived_status(event, now=now)
            tz_value = series.timezone or _default_timezone_for(request.user)
            return render(request, 'studio/event_series/detail.html', {
                'series': series,
                'events': events,
                'add_error': (
                    'Could not propagate: a generated event slug collided '
                    'with an existing event. No changes were saved.'
                ),
                'timezone_value': tz_value,
                'timezone_label': get_timezone_label(tz_value) or tz_value,
                'timezone_options': build_timezone_options(),
                'tz_settings_link': _should_autodetect_tz(request.user),
                'tz_autodetect': (
                    not series.timezone
                    and _should_autodetect_tz(request.user)
                ),
                **_series_notification_context(series),
            }, status=400)
        # Issue #896: re-enqueue the auto-banner render when the name drifts.
        # ``enqueue_if_missing`` hashes ``series.name`` and short-circuits
        # when the hash is unchanged, so editing only the description does
        # NOT waste a render.
        enqueue_if_missing('event_series', series.pk)
        if propagate:
            messages.success(
                request,
                f'Updated {updated} event{"" if updated == 1 else "s"}.',
            )
        return redirect('studio_event_series_detail', series_id=series.pk)

    # Issue #957: match the public series page — list occurrences in
    # chronological order so the staff table agrees with what visitors see,
    # regardless of stale ``series_position`` values from a multi-batch rebuild.
    events = list(series.events.all().order_by(
        'start_datetime', 'id',
    ))
    # Issue #893: annotate each occurrence with the same derived status the
    # events list uses so the detail table can render the shared badge.
    now = dj_timezone.now()
    for event in events:
        annotate_derived_status(event, now=now)
    # Issue #665: the add-occurrence form inherits the series' TZ; show
    # it in the picker so the admin sees the active zone next to the
    # date input.
    tz_value = series.timezone or _default_timezone_for(request.user)
    return render(request, 'studio/event_series/detail.html', {
        'series': series,
        'events': events,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        'tz_settings_link': _should_autodetect_tz(request.user),
        # Issue #855: an existing series keeps its stored timezone. Only
        # auto-detect the browser zone when the series has none and the
        # admin has no saved preference.
        'tz_autodetect': (
            not series.timezone
            and _should_autodetect_tz(request.user)
        ),
        # Issue #859: one-click "create Zoom meetings for all events".
        'zoom_eligible_count': eligible_occurrence_count(series),
        'zoom_last_run': series.zoom_meetings_last_run,
        **_series_notification_context(series),
        # Issues #896/#931: banner / social-image panel.
        **banner_panel_context(
            content_type='event_series',
            record=series,
            regenerate_url_name='studio_event_series_regenerate_banner',
            upload_url_name='studio_event_series_upload_banner',
            remove_url_name='studio_event_series_remove_banner',
            url_kwarg='series_id',
        ),
    })


def _render_add_occurrence_error(request, series, add_error):
    """Re-render the detail page with a flash-style add error and 400.

    Issue #854 Part A: a partial occurrence is never written — the view
    falls through here before creating any ``Event`` row.
    """
    # Issue #957: chronological order, consistent with the main detail render.
    events = list(
        series.events.all().order_by('start_datetime', 'id'),
    )
    now = dj_timezone.now()
    for event in events:
        annotate_derived_status(event, now=now)
    tz_value = series.timezone or _default_timezone_for(request.user)
    return render(request, 'studio/event_series/detail.html', {
        'series': series,
        'events': events,
        'add_error': add_error,
        'timezone_value': tz_value,
        'timezone_label': get_timezone_label(tz_value) or tz_value,
        'timezone_options': build_timezone_options(),
        'tz_settings_link': _should_autodetect_tz(request.user),
        'tz_autodetect': (
            not series.timezone
            and _should_autodetect_tz(request.user)
        ),
        **_series_notification_context(series),
    }, status=400)


@staff_required
@require_POST
def event_series_add_occurrence(request, series_id):
    """Append one more event to the series with the next series_position.

    Issue #854 Part A: the add form accepts an explicit per-occurrence
    start time (HH:MM, 24h) and an optional title, so an organizer can
    schedule an irregular series — each occurrence can land on a
    different weekday and time of day. The time defaults to the series
    ``start_time`` but can be overridden per occurrence; a blank title
    falls back to ``"{series.name} — Session {n}"`` and a provided title
    drives the slug.
    """
    series = get_object_or_404(EventSeries, pk=series_id)
    start_date_str = request.POST.get('start_date', '').strip()
    start_time_str = request.POST.get('start_time', '').strip()
    duration_str = request.POST.get('duration_hours', '').strip() or '1'
    title_input = request.POST.get('title', '').strip()

    try:
        start_date = _parse_date_str(start_date_str)
    except (ValueError, AttributeError):
        return _render_add_occurrence_error(
            request, series, 'Start date is required (dd/mm/yyyy).',
        )

    # Issue #854 Part A: per-occurrence start time. Default to the series
    # start_time when left blank; reject a malformed HH:MM with no row
    # written.
    if start_time_str:
        try:
            hour, minute = start_time_str.split(':')
            start_time = datetime(
                2000, 1, 1, int(hour), int(minute),
            ).time()
        except (ValueError, AttributeError):
            return _render_add_occurrence_error(
                request, series, 'Start time must be HH:MM (24h).',
            )
    else:
        start_time = series.start_time

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

    # Issue #665: combine the picked date with the chosen start time in
    # the chosen TZ (defaults to the series' TZ; admin can override per
    # occurrence) and convert to UTC for storage. A tampered TZ value
    # falls back to the series TZ.
    posted_tz = (request.POST.get('timezone') or '').strip()
    occurrence_tz = (
        posted_tz if posted_tz and is_valid_timezone(posted_tz)
        else (series.timezone or 'UTC')
    )
    event_start = _localize_to_utc(
        start_date, start_time, occurrence_tz,
    )
    event_end = event_start + timedelta(hours=duration_hours)

    # Issue #958: the occurrence level defaults to (inherits) the series'
    # required_level. Studio is human-controlled, so a differing level is
    # permitted here (the template's confirmation prompt is the override).
    # A tampered / unknown level falls back to the series level.
    posted_level = (request.POST.get('required_level') or '').strip()
    if posted_level:
        try:
            occurrence_level = int(posted_level)
        except ValueError:
            occurrence_level = series.required_level
        if occurrence_level not in _VALID_REQUIRED_LEVELS:
            occurrence_level = series.required_level
    else:
        occurrence_level = series.required_level

    # Issue #854 Part A: an explicit title drives the slug; a blank title
    # falls back to the default "{series.name} — Session {n}" and the
    # default slug base.
    if title_input:
        event_title = title_input
        base_slug = slugify(title_input) or f'{series.slug}-session-{next_pos}'
    else:
        event_title = f'{series.name} — Session {next_pos}'
        base_slug = f'{series.slug}-session-{next_pos}'
    event_slug = _generate_unique_slug(base_slug)

    new_event = Event.objects.create(
        title=event_title,
        slug=event_slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=event_start,
        end_datetime=event_end,
        timezone=occurrence_tz,
        status='draft',
        required_level=occurrence_level,
        origin='studio',
        event_series=series,
        series_position=next_pos,
        published=True,
    )
    # Issue #857: auto-enroll existing series registrants. Best-effort and
    # idempotent; a draft occurrence enrolls nobody until it is published
    # to ``upcoming`` (the helper gates on ``is_upcoming``).
    enroll_series_registrants_in_event(new_event)
    return redirect('studio_event_series_detail', series_id=series.pk)


def _series_was_recently_notified(series):
    """Return True if this series was notified within the last 24 hours.

    Mirrors ``studio.views.notifications._was_recently_notified`` but keyed
    on the series notification title + public URL (issue #868).
    """
    title = series_notification_title(series)
    url = series.get_absolute_url()
    cutoff = dj_timezone.now() - timedelta(hours=24)
    return Notification.objects.filter(
        title=title,
        url=url,
        created_at__gte=cutoff,
    ).exists()


@staff_required
@require_POST
def event_series_notify(request, series_id):
    """Notify eligible subscribers about the whole series.

    Creates one notification per eligible user deep-linking to the public
    series page. Returns ``{"notified": N}``; 409 when the series was
    already notified within the last 24 hours (issue #868).
    """
    series = get_object_or_404(EventSeries, pk=series_id)

    if _series_was_recently_notified(series):
        return JsonResponse(
            {'error': 'Already notified in the last 24 hours'},
            status=409,
        )

    result = NotificationService.notify_series(series)
    return JsonResponse({'notified': result.get('notified', 0)})


@staff_required
@require_POST
def event_series_announce_slack(request, series_id):
    """Post a single Slack announcement for the whole series (issue #868).

    Returns ``{"posted": true}`` on success. When the series has no
    upcoming sessions, or Slack is not configured / the post failed,
    returns a structured ``{"error": ...}`` with status 500.
    """
    series = get_object_or_404(EventSeries, pk=series_id)

    from notifications.services.slack_announcements import (
        _series_upcoming_sessions,
    )

    if not _series_upcoming_sessions(series):
        return JsonResponse(
            {'error': 'No upcoming sessions to announce.'},
            status=500,
        )

    try:
        posted = post_series_slack_announcement(series)
    except Exception as exc:
        logger.exception(
            'Failed to post series Slack announcement for %s', series_id,
        )
        return JsonResponse({'error': str(exc)}, status=500)

    if posted:
        return JsonResponse({'posted': True})
    return JsonResponse(
        {'error': 'Slack not configured or post failed'},
        status=500,
    )


@staff_required
@require_POST
def event_series_regenerate_banner(request, series_id):
    """Force-enqueue an auto-banner render for a series (issue #896).

    Mirrors the content/event "Regenerate banner" UX: ``enqueue_force``
    bypasses the name-hash short-circuit (the operator clicked on
    purpose), flashes the result, and redirects back to the series
    detail. No-ops with a warning when banner-generator is disabled.
    """
    series = get_object_or_404(EventSeries, pk=series_id)
    if not banner_generator_is_enabled():
        messages.warning(
            request,
            'Banner generator is not configured. Add the function URL and '
            'bearer token under Studio > Settings > Content Tools first.',
        )
    else:
        enqueue_force('event_series', series.pk)
        messages.success(
            request,
            'Banner regeneration queued. Refresh in a few seconds to see '
            'the new image.',
        )
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def event_series_create_zoom(request, series_id):
    """Enqueue Zoom-meeting creation for every eligible occurrence (issue #859).

    Idempotent and partial-failure safe — the heavy lifting runs in the
    ``create_series_zoom_meetings`` background job. This view only counts the
    eligible occurrences (future, Zoom platform, no existing meeting),
    enqueues the job, flashes a "creating…" message, and redirects back so
    the request never blocks on Zoom API round-trips. When nothing is
    eligible we skip the enqueue and say so.
    """
    series = get_object_or_404(EventSeries, pk=series_id)
    eligible = eligible_occurrence_count(series)

    if eligible == 0:
        messages.info(
            request,
            'All occurrences already have Zoom meetings — nothing to create.',
        )
        return redirect('studio_event_series_detail', series_id=series.pk)

    enqueue_create_series_zoom_meetings(series.pk)
    messages.success(
        request,
        f'Creating Zoom meetings for {eligible} eligible '
        f'occurrence{"" if eligible == 1 else "s"}… '
        'Reload in a few seconds to see the result.',
    )
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def event_series_event_publish(request, series_id, event_id):
    """Publish a draft member event so the public series page lists it.

    Issue #858: replaces the confusing "draft" status as the
    series-management surface. Publishing flips ``draft`` -> ``upcoming``
    (the visible state the public events surfaces key on). Events already
    in a visible state, or in a terminal state (``cancelled`` /
    ``completed``), are left untouched so we never resurrect a cancelled
    occurrence by clicking Publish. Redirects back to the series detail.
    """
    series = get_object_or_404(EventSeries, pk=series_id)
    event = get_object_or_404(Event, pk=event_id, event_series=series)
    if event.status == 'draft':
        event.status = 'upcoming'
        event.save()
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def event_series_event_unpublish(request, series_id, event_id):
    """Unpublish an upcoming member event, pulling it from the public page.

    Issue #858: flips ``upcoming`` -> ``draft`` so the occurrence drops
    out of every public events surface. Cancelled and completed
    occurrences are terminal states and are intentionally not
    "unpublished" — clicking has no effect on them. Redirects back to the
    series detail.
    """
    series = get_object_or_404(EventSeries, pk=series_id)
    event = get_object_or_404(Event, pk=event_id, event_series=series)
    if event.status == 'upcoming':
        event.status = 'draft'
        event.save()
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def event_series_delete(request, series_id):
    """Delete the series; ``SET_NULL`` preserves the member events."""
    series = get_object_or_404(EventSeries, pk=series_id)
    series.delete()
    return redirect('studio_event_list')
