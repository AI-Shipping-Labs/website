"""Sync source-declared course cohorts and report scheduling diagnostics."""

import datetime

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date

from content.models.cohort import Cohort
from content.services.course_cohorts import (
    cohort_requires_event_series,
    ensure_course_self_paced_cohort,
)
from content.sync_parsers.common import GitHubSyncError, logger
from events.models import EventSeries

_DATED_REQUIRED_FIELDS = ('key', 'name', 'start_date', 'end_date')
_SELF_PACED_REQUIRED_FIELDS = ('key', 'name')
_VALID_MODES = frozenset({'cohort', 'self_paced'})


def sync_course_cohorts(course, course_data, rel_path):
    """Upsert declared cohorts without deleting omitted historical rows."""
    entries = course_data.get('cohorts') or []
    if not isinstance(entries, list):
        raise GitHubSyncError(
            f'Invalid cohorts in {rel_path}: expected a list'
        )
    for entry in entries:
        if not isinstance(entry, dict):
            raise GitHubSyncError(
                f'Invalid cohorts entry in {rel_path}: expected a mapping, '
                f'got {type(entry).__name__}'
            )
        key, defaults = _parse_cohort_entry(entry, rel_path)
        _upsert_cohort(course, key, defaults, rel_path)
    ensure_course_self_paced_cohort(course)


def note_course_cohort_warnings(course, course_data, rel_path, stats):
    """Record current/upcoming dated cohorts missing an event series."""
    entries = course_data.get('cohorts') or []
    keys = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get('key') is not None:
            keys.append(str(entry['key']).strip())
    cohorts = Cohort.objects.filter(course=course, external_key__in=keys)
    for cohort in cohorts:
        if cohort.event_series_id or not cohort_requires_event_series(cohort):
            continue
        message = (
            f"Warning: cohort '{cohort.external_key}' ({cohort.name}) in "
            f'{rel_path} has no event_series, so its live sessions show "Not '
            'scheduled". Add event_series: <series slug> to the cohorts entry '
            'or link a series in Studio.'
        )
        logger.warning(message)
        if stats is not None:
            stats['errors'].append({
                'file': rel_path,
                'error': message,
                'severity': 'info',
            })


def _parse_cohort_entry(entry, rel_path):
    mode = _entry_mode(entry, rel_path)
    required = _DATED_REQUIRED_FIELDS
    if mode == 'self_paced':
        required = _SELF_PACED_REQUIRED_FIELDS
    _require_fields(entry, required, rel_path)
    key = str(entry['key']).strip()
    if mode == 'self_paced':
        defaults = _self_paced_defaults(entry, key, rel_path)
    else:
        defaults = _dated_defaults(entry, rel_path)
    has_series, series = _resolve_event_series(entry, key, rel_path)
    if has_series:
        defaults['event_series'] = series
    return key, defaults


def _entry_mode(entry, rel_path):
    raw_mode = entry.get('mode', 'cohort')
    mode = None
    if not isinstance(raw_mode, bool):
        mode = str(raw_mode).strip().lower()
    if mode in _VALID_MODES:
        return mode
    raise GitHubSyncError(
        f'Invalid cohorts entry in {rel_path}: unknown mode {raw_mode!r} '
        "(expected 'cohort' or 'self_paced')"
    )


def _require_fields(entry, required, rel_path):
    missing = []
    for field in required:
        if entry.get(field) is None or entry.get(field) == '':
            missing.append(field)
    if missing:
        raise GitHubSyncError(
            f"Invalid cohorts entry in {rel_path}: missing {', '.join(missing)}"
        )


def _self_paced_defaults(entry, key, rel_path):
    if entry.get('start_date') not in (None, ''):
        _raise_self_paced_dates(key, rel_path)
    if entry.get('end_date') not in (None, ''):
        _raise_self_paced_dates(key, rel_path)
    return {
        'name': entry['name'],
        'start_date': None,
        'end_date': None,
        'mode': 'self_paced',
    }


def _raise_self_paced_dates(key, rel_path):
    raise GitHubSyncError(
        f"Invalid cohorts entry '{key}' in {rel_path}: mode: self_paced "
        'must not set start_date/end_date'
    )


def _dated_defaults(entry, rel_path):
    return {
        'name': entry['name'],
        'start_date': _parse_cohort_date(
            entry['start_date'], field_name='start_date', rel_path=rel_path,
        ),
        'end_date': _parse_cohort_date(
            entry['end_date'], field_name='end_date', rel_path=rel_path,
        ),
        'mode': 'cohort',
    }


def _resolve_event_series(entry, key, rel_path):
    if 'event_series' not in entry:
        return False, None
    raw_value = entry['event_series']
    slug = ''
    if raw_value is not None and not isinstance(raw_value, bool):
        slug = str(raw_value).strip()
    if not slug:
        raise GitHubSyncError(
            f"Invalid cohorts entry '{key}' in {rel_path}: event_series must "
            'be an event series slug'
        )
    series = EventSeries.objects.filter(slug=slug).first()
    if series is None:
        raise GitHubSyncError(
            f"Invalid cohorts entry '{key}' in {rel_path}: unknown "
            f"event_series '{slug}' (create the series in Studio first)"
        )
    return True, series


def _upsert_cohort(course, key, defaults, rel_path):
    cohort = Cohort.objects.filter(course=course, external_key=key).first()
    if cohort is None:
        cohort = Cohort(course=course, external_key=key, **defaults)
        _save_validated(cohort, key, rel_path)
        return
    changed_fields = []
    for field, value in defaults.items():
        if getattr(cohort, field) != value:
            setattr(cohort, field, value)
            changed_fields.append(field)
    if changed_fields:
        _save_validated(cohort, key, rel_path, changed_fields)


def _save_validated(cohort, key, rel_path, changed_fields=None):
    try:
        cohort.full_clean()
    except ValidationError as exc:
        raise GitHubSyncError(
            f"Invalid cohorts entry '{key}' in {rel_path}: "
            f'{"; ".join(exc.messages)}'
        ) from exc
    if changed_fields is None:
        cohort.save()
    else:
        cohort.save(update_fields=changed_fields)


def _parse_cohort_date(value, *, field_name, rel_path):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        parsed = parse_date(value.strip())
        if parsed is not None:
            return parsed
    raise GitHubSyncError(
        f'Invalid cohorts entry in {rel_path}: {field_name} {value!r} is '
        'not a valid date (expected YYYY-MM-DD)'
    )
