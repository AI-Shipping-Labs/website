"""Recording metadata and explicit event references for workshop sync."""


def resolve_explicit_workshop_event(event_id, event_slug):
    """An explicit ID wins; an unresolved reference never enables fallback."""
    from events.models import Event

    if event_id not in (None, ''):
        return True, Event.objects.filter(pk=event_id).first()
    if event_slug not in (None, ''):
        slug = str(event_slug).strip()
        if slug:
            return True, Event.objects.filter(slug=slug).first()
    return False, None


def _event_without_recording_url(data):
    event_id = data.get('event_id')
    if isinstance(event_id, bool) or isinstance(event_id, float):
        raise ValueError(f'Invalid event_id {event_id!r}; expected an event ID.')
    event_slug = data.get('event_slug')
    if event_id in (None, '') and event_slug is not None and not isinstance(event_slug, str):
        raise ValueError(f'Invalid event_slug {event_slug!r}; expected a slug string.')
    if event_id in (None, '') and event_slug and not event_slug.strip():
        raise ValueError('Invalid event_slug; expected a nonblank slug string.')
    reference_present, event = resolve_explicit_workshop_event(
        event_id, event_slug,
    )
    if reference_present and event is None:
        reference = f'event_slug {data.get("event_slug")!r}'
        if event_id not in (None, ''):
            reference = f'event_id {event_id}'
        raise ValueError(f'{reference} not found; refusing to create a duplicate event.')
    return event


def _recording_level(recording, pages_level, valid_levels):
    level = recording.get('required_level')
    if level is None:
        raise ValueError(
            'recording.url is set but recording.required_level is missing — '
            'refusing to leak the recording under the pages_required_level gate.'
        )
    if level not in valid_levels:
        raise ValueError(
            f'Invalid recording.required_level={level!r}; '
            f'must be one of {sorted(valid_levels)}'
        )
    if level < pages_level:
        raise ValueError(
            f'recording.required_level ({level}) must be '
            f'>= pages_required_level ({pages_level}).'
        )
    return level


def read_workshop_recording(data, pages_level, valid_levels):
    """Resolve a URL-backed recording or preserve an explicitly linked event."""
    recording = data.get('recording') or {}
    if not isinstance(recording, dict):
        raise ValueError(
            f'recording must be a mapping/dict, got {type(recording).__name__}'
        )
    url = str(recording.get('url', '') or '').strip()
    if url:
        level = _recording_level(recording, pages_level, valid_levels)
        return recording, url, level, None
    event = _event_without_recording_url(data)
    level = pages_level
    if event is not None:
        level = max(pages_level, event.required_level)
    return recording, url, level, event


def link_preserved_workshop_event(workshop, event):
    """Link only: live event content and recording assets remain event-owned."""
    from content.models import Workshop

    if workshop.event_id != event.pk:
        Workshop.objects.filter(pk=workshop.pk).update(event=event)
        workshop.event_id = event.pk
