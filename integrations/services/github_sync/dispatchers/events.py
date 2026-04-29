"""Event sync helpers and dispatcher."""

# ruff: noqa

import os
import re
import uuid

import requests
from django.conf import settings
from django.utils import timezone

from integrations.services.github_sync.common import INSTRUCTOR_ID_RE, GitHubSyncError, logger
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

            # Content fields that sync always updates
            content_defaults = {
                'title': data.get('title', slug),
                'description': data.get('description', ''),
                'recording_url': data.get('recording_url', '') or data.get('video_url', ''),
                'recording_embed_url': (
                    data.get('recording_embed_url', '')
                    or data.get('google_embed_url', '')
                ),
                'transcript_url': data.get('transcript_url', ''),
                'timestamps': data.get('timestamps', []),
                'materials': data.get('materials', []),
                'core_tools': data.get('core_tools', []),
                'learning_objectives': data.get('learning_objectives', []),
                'outcome': data.get('outcome', ''),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'speaker_name': data.get('speaker_name', ''),
                'speaker_bio': data.get('speaker_bio', ''),
                'related_course': data.get('related_course', ''),
                'published': data.get('published', True),
                'recap_file': rendered_recap['recap_file'],
                'recap_markdown': rendered_recap['recap_markdown'],
                'recap_html': rendered_recap['recap_html'],
                'recap_data': rendered_recap['recap_data'],
                'content_id': event_content_id,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Handle cover_image
            cover_image = data.get('cover_image', '')
            if cover_image:
                content_defaults['cover_image_url'] = rewrite_cover_image_url(
                    cover_image, source, rel_path,
                )

            # Handle published_at
            published_at = data.get('published_at')
            if published_at:
                if isinstance(published_at, str):
                    content_defaults['published_at'] = dt.datetime.combine(
                        dt.date.fromisoformat(published_at),
                        dt.time.min,
                        tzinfo=dt.timezone.utc,
                    )
                elif isinstance(published_at, (dt.date, dt.datetime)):
                    if isinstance(published_at, dt.date) and not isinstance(published_at, dt.datetime):
                        content_defaults['published_at'] = dt.datetime.combine(
                            published_at, dt.time.min, tzinfo=dt.timezone.utc,
                        )
                    else:
                        content_defaults['published_at'] = published_at

            # Try to find existing event by slug + source_repo
            try:
                event = Event.objects.get(slug=slug, source_repo=source.repo_name)
                # Issue #225: skip the save when every synced content field
                # matches the DB row. Operational fields (start_datetime,
                # zoom_join_url, status, etc.) are not in content_defaults,
                # so they are never touched here.
                if _defaults_differ(event, content_defaults):
                    for key, value in content_defaults.items():
                        setattr(event, key, value)
                    event.save()
                    stats['updated'] += 1
                    stats['items_detail'].append({
                        'title': content_defaults.get('title', slug),
                        'slug': slug,
                        'action': 'updated',
                        'content_type': 'event',
                    })
                else:
                    stats['unchanged'] += 1
            except Event.DoesNotExist:
                # Content-repo events still default to recording-style rows
                # unless operational frontmatter explicitly says otherwise.
                start_dt_value = _coerce_event_datetime(data.get('start_datetime'))
                if not start_dt_value:
                    start_dt_value = _coerce_event_datetime(published_at)
                if not start_dt_value:
                    start_dt_value = timezone.now()

                event = Event(
                    slug=slug,
                    start_datetime=start_dt_value,
                    end_datetime=_coerce_event_datetime(data.get('end_datetime')),
                    status=data.get('status') or 'completed',
                    timezone=data.get('timezone') or settings.TIME_ZONE,
                    platform=data.get('platform') or 'zoom',
                    location=data.get('location', '') or '',
                    **content_defaults,
                )
                event.save()
                stats['created'] += 1
                stats['items_detail'].append({
                    'title': content_defaults.get('title', slug),
                    'slug': slug,
                    'action': 'created',
                    'content_type': 'event',
                })
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
    for ev in stale:
        stats['items_detail'].append({
            'title': ev.title,
            'slug': ev.slug,
            'action': 'deleted',
            'content_type': 'event',
        })
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count
