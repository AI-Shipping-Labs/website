"""Event sync helpers and dispatcher."""

# ruff: noqa

import os
import re
import uuid

import requests
from django.conf import settings
from django.utils import timezone

from integrations.services.github_sync.common import INSTRUCTOR_ID_RE, GitHubSyncError, logger
from integrations.services.github_sync.lifecycle import (
    cleanup_stale_synced_objects,
    find_synced_object,
    upsert_synced_object,
)
from integrations.services.github_sync.media import rewrite_cover_image_url, rewrite_image_urls, _check_broken_image_refs
from integrations.services.github_sync.parsing import (
    _check_slug_collision,
    _compute_content_hash,
    _defaults_differ,
    _derive_readme_content_id,
    _derive_workshop_page_content_id,
    _extract_readme_title,
    _parse_markdown_file,
    _parse_yaml_file,
    _render_event_recap_file,
    _validate_frontmatter,
)
from integrations.services.github_sync.repo import derive_slug, extract_sort_order, _matches_ignore_patterns

from integrations.services.github_sync.dispatchers.instructors import _attach_instructors_to_event, _resolve_instructors_for_yaml

_UNSET = object()


def _coerce_external_host_for_sync(raw, *, slug=''):
    """Issue #579. ``Event.save()`` skips field validators, so a
    content-repo author who types ``DataTalks Club`` in frontmatter
    would still land a non-canonical value in the DB. Coerce anything
    outside ``EXTERNAL_HOST_CHOICES`` to '' and emit a sync warning so
    the bad frontmatter is visible in logs.
    """
    # Inline import: matches the existing dispatcher pattern (see Event
    # imports below) to defer ``events`` app loading until call time.
    from events.models.event import EXTERNAL_HOST_CHOICES

    valid = {value for value, _ in EXTERNAL_HOST_CHOICES}
    value = (raw or '').strip()
    if value in valid:
        return value
    logger.warning(
        'Sync: unknown external_host %r on event slug=%r — coercing to ""',
        value, slug,
    )
    return ''


def _build_synced_event_content_defaults(
    *,
    source,
    source_path,
    commit_sha,
    content_id,
    title,
    description='',
    tags=None,
    cover_image_url=_UNSET,
    recording_url='',
    recording_embed_url='',
    transcript_url='',
    timestamps=None,
    materials=None,
    core_tools=None,
    learning_objectives=None,
    outcome='',
    required_level=0,
    related_course='',
    kind='standard',
    published_at=_UNSET,
    recap=_UNSET,
    external_host='',
):
    """Build content fields shared by standalone and workshop event sync.

    Operational fields such as schedule, status, platform, Zoom details,
    registration settings, and published state stay out of this mapping so
    existing Studio-managed values are not overwritten during content sync.
    """
    defaults = {
        'title': title,
        'description': description,
        'recording_url': recording_url,
        'recording_embed_url': recording_embed_url,
        'transcript_url': transcript_url,
        'timestamps': timestamps or [],
        'materials': materials or [],
        'core_tools': core_tools or [],
        'learning_objectives': learning_objectives or [],
        'outcome': outcome,
        'tags': tags or [],
        'required_level': required_level,
        'related_course': related_course,
        'kind': kind,
        # Issue #572 / #579: third-party host indicator. Empty string is
        # the back-compat default for existing files without the
        # frontmatter key. Issue #579 constrains the value to a known
        # partner list; non-canonical values coerce to '' (with a sync
        # warning) so save() never lands bad data.
        'external_host': _coerce_external_host_for_sync(
            external_host, slug=source_path,
        ),
        'content_id': content_id,
        # Issue #564: synced events must carry origin='github' so the
        # save-time invariant on ``Event`` (origin='github' iff
        # source_repo is set) holds.
        'origin': 'github',
        'source_repo': source.repo_name,
        'source_path': source_path,
        'source_commit': commit_sha,
    }
    if cover_image_url is not _UNSET:
        defaults['cover_image_url'] = cover_image_url
    if published_at is not _UNSET:
        defaults['published_at'] = published_at
    if recap is not _UNSET:
        defaults.update({
            'recap_file': recap.get('recap_file', ''),
            'recap_markdown': recap.get('recap_markdown', ''),
            'recap_html': recap.get('recap_html', ''),
            'recap_data': recap.get('recap_data', {}),
        })
    return defaults


def _upsert_synced_event_content(
    *,
    lookup,
    defaults,
    stats,
    create_kwargs=None,
    detail_slug=None,
):
    """Apply synced event content fields through the shared lifecycle path."""
    from events.models import Event

    slug = detail_slug or (create_kwargs or {}).get('slug') or defaults.get('source_path')
    return upsert_synced_object(
        model=Event,
        lookup=lookup,
        defaults=defaults,
        stats=stats,
        create_kwargs=create_kwargs,
        detail=lambda event, action: {
            'title': defaults.get('title', event.title),
            'slug': detail_slug or event.slug or slug,
            'action': action,
            'content_type': 'event',
        },
    )


def _event_requests_zoom_meeting(data):
    """Return True when synced frontmatter requests a Zoom-backed event."""
    location = str(data.get('location', '') or '').strip().lower()
    platform = str(data.get('platform', '') or '').strip().lower()
    return location == 'zoom' or platform == 'zoom'


def _coerce_event_datetime(value):
    """Convert synced event frontmatter values into aware datetimes."""
    import datetime as dt

    if value in (None, ''):
        return None

    if isinstance(value, dt.datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, dt.timezone.utc)
        return value

    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith('Z'):
            normalized = f'{normalized[:-1]}+00:00'
        try:
            parsed = dt.datetime.fromisoformat(normalized)
        except ValueError:
            parsed_date = dt.date.fromisoformat(normalized)
            return dt.datetime.combine(
                parsed_date, dt.time.min, tzinfo=dt.timezone.utc,
            )
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, dt.timezone.utc)
        return parsed

    raise ValueError(f'Unsupported event datetime value: {value!r}')


def _maybe_create_zoom_meeting_for_synced_event(event, data):
    """Best-effort Zoom meeting creation for newly synced events."""
    if event.platform != 'zoom' or event.status in ('completed', 'cancelled'):
        return

    if not data.get('start_datetime') or not _event_requests_zoom_meeting(data):
        return

    if event.zoom_meeting_id or event.zoom_join_url:
        return

    from integrations.services.zoom import ZoomAPIError, create_meeting

    try:
        result = create_meeting(event)
    except (ZoomAPIError, requests.RequestException) as exc:
        logger.warning(
            'Failed to auto-create Zoom meeting for synced event %s: %s',
            event.slug,
            exc,
        )
        return

    event.zoom_meeting_id = result['meeting_id']
    event.zoom_join_url = result['join_url']
    event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])



def _dispatch_events(source, repo_dir, file_list, commit_sha, stats,
                     known_images=None):
    """Walker dispatch handler: process event YAML files.

    ``file_list`` is the set of repo-relative paths classified by
    ``_classify_repo_files`` as having a ``start_datetime`` field.

    Upserts into the Event model. Only updates content fields; operational
    fields (start_datetime, zoom_join_url, status, etc.) are never
    overwritten by sync if the event already exists.
    """
    import datetime as dt

    from events.models import Event

    seen_slugs = set()
    failed_slugs = set()
    data = None  # ensure name is bound for the except clause
    recap_rel_paths = set()

    for rel_path in file_list:
        filename = os.path.basename(rel_path)
        if filename.endswith(('.yaml', '.yml')):
            try:
                yaml_data = _parse_yaml_file(os.path.join(repo_dir, rel_path))
            except Exception:
                continue
            recap_file = yaml_data.get('recap_file') or yaml_data.get('recap-file')
            if recap_file and not os.path.isabs(recap_file):
                recap_rel_paths.add(os.path.normpath(
                    os.path.join(os.path.dirname(rel_path), recap_file),
                ))

    for rel_path in sorted(file_list):
        if os.path.normpath(rel_path) in recap_rel_paths:
            continue

        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)

        try:
            # Parse YAML or markdown with frontmatter
            if filename.endswith('.md'):
                data, body = _parse_markdown_file(filepath)
                if body and body.strip():
                    data['description'] = body.strip()
            else:
                data = _parse_yaml_file(filepath)

            slug = data.get('slug', os.path.splitext(filename)[0])

            # Require content_id
            event_content_id = data.get('content_id')
            if not event_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Slug collision check
            if _check_slug_collision(Event, slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_slugs.add(slug)
                continue

            seen_slugs.add(slug)

            try:
                rendered_recap = _render_event_recap_file(
                    repo_dir, rel_path, data, source, rel_path,
                )
            except Exception as exc:
                msg = f'Error rendering recap for {rel_path}: {exc}'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                rendered_recap = {
                    'recap_file': data.get('recap_file') or data.get('recap-file') or '',
                    'recap_markdown': '',
                    'recap_html': '',
                    'recap_data': {},
                }

            # Handle cover_image
            cover_image_url = _UNSET
            cover_image = data.get('cover_image', '')
            if cover_image:
                cover_image_url = rewrite_cover_image_url(cover_image, source, rel_path)

            # Handle published_at
            published_at_value = _UNSET
            published_at = data.get('published_at')
            if published_at:
                if isinstance(published_at, str):
                    published_at_value = dt.datetime.combine(
                        dt.date.fromisoformat(published_at),
                        dt.time.min,
                        tzinfo=dt.timezone.utc,
                    )
                elif isinstance(published_at, (dt.date, dt.datetime)):
                    if isinstance(published_at, dt.date) and not isinstance(published_at, dt.datetime):
                        published_at_value = dt.datetime.combine(
                            published_at, dt.time.min, tzinfo=dt.timezone.utc,
                        )
                    else:
                        published_at_value = published_at

            content_defaults = _build_synced_event_content_defaults(
                source=source,
                source_path=rel_path,
                commit_sha=commit_sha,
                content_id=event_content_id,
                title=data.get('title', slug),
                description=data.get('description', ''),
                tags=data.get('tags', []),
                cover_image_url=cover_image_url,
                recording_url=(
                    data.get('recording_url', '') or data.get('video_url', '')
                ),
                recording_embed_url=(
                    data.get('recording_embed_url', '')
                    or data.get('google_embed_url', '')
                ),
                transcript_url=data.get('transcript_url', ''),
                timestamps=data.get('timestamps', []),
                materials=data.get('materials', []),
                core_tools=data.get('core_tools', []),
                learning_objectives=data.get('learning_objectives', []),
                outcome=data.get('outcome', ''),
                required_level=data.get('required_level', 0),
                related_course=data.get('related_course', ''),
                kind=data.get('kind', 'standard') or 'standard',
                external_host=data.get('external_host', '') or '',
                published_at=published_at_value,
                recap=rendered_recap,
            )

            event = find_synced_object((
                lambda: Event.objects.filter(
                    slug=slug, source_repo=source.repo_name,
                ).first(),
            ))
            create_kwargs = None
            if event is None:
                # Content-repo events still default to recording-style rows
                # unless operational frontmatter explicitly says otherwise.
                start_dt_value = _coerce_event_datetime(data.get('start_datetime'))
                if not start_dt_value:
                    start_dt_value = _coerce_event_datetime(published_at)
                if not start_dt_value:
                    start_dt_value = timezone.now()

                create_kwargs = {
                    'slug': slug,
                    'start_datetime': start_dt_value,
                    'end_datetime': _coerce_event_datetime(
                        data.get('end_datetime'),
                    ),
                    'status': data.get('status') or 'completed',
                    'timezone': data.get('timezone') or settings.TIME_ZONE,
                    'platform': data.get('platform') or 'zoom',
                    'location': data.get('location', '') or '',
                    'published': data.get('published', True),
                }

            result = _upsert_synced_event_content(
                lookup=lambda: event,
                defaults=content_defaults,
                stats=stats,
                create_kwargs=create_kwargs,
                detail_slug=slug,
            )
            event = result.instance
            if result.created:
                _maybe_create_zoom_meeting_for_synced_event(event, data)

            # Resolve instructors: list and attach M2M (post-save).
            # Runs on both newly-created and existing events so a yaml
            # edit that adds/removes instructors flows through on every
            # sync.
            resolved_instructors = _resolve_instructors_for_yaml(
                data, rel_path, stats,
            )
            _attach_instructors_to_event(
                event, resolved_instructors, stats,
            )

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete: if a synced event file is removed, set published=False.
    # Workshop-linked events (``kind='workshop'``) are managed by
    # ``_sync_single_workshop`` / ``_link_or_create_workshop_event``, so
    # exclude them here — otherwise the events dispatcher's cleanup
    # would unpublish a freshly-created workshop event because no event
    # YAML existed for it.
    stale = Event.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs).exclude(
        kind='workshop',
    )
    cleanup_stale_synced_objects(
        stale,
        stats=stats,
        detail=lambda ev, action: {
            'title': ev.title,
            'slug': ev.slug,
            'action': action,
            'content_type': 'event',
        },
        cleanup=lambda events: Event.objects.filter(
            pk__in=[event.pk for event in events],
        ).update(published=False),
    )
