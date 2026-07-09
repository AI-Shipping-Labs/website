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

from events.services.timestamps import normalize_event_timestamps_for_sync
from integrations.services.github_sync.dispatchers.instructors import _attach_instructors_to_event, _resolve_instructors_for_yaml
from integrations.services.github_sync.dispatchers.hosts import _attach_hosts_to_event, _resolve_hosts_for_event_yaml

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
        'timestamps': normalize_event_timestamps_for_sync(timestamps),
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
            # Issue #673: include the integer primary key so the Studio
            # sync history can render the canonical
            # ``/events/<id>/<slug>`` link without a follow-up DB hit.
            'id': event.pk,
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


def _normalize_title_for_match(title):
    """Normalize an event/workshop title for the dedup heuristic (issue #880).

    Lowercase, collapse internal whitespace to single spaces, and strip
    surrounding whitespace and punctuation. Deliberately conservative — it
    only smooths over case and spacing differences ("  Take-Home  Assignment
    Live " vs "take-home assignment live"), not semantic ones, so the
    title+date key stays a low-false-positive match.

    Lives here (rather than in ``dispatchers/workshops.py``, where it
    originated in issue #880) so both the workshop link heuristic and the
    standalone-event adopt heuristic (issue #998) can share it without a
    circular import — ``workshops`` already imports from ``events`` at module
    top, so the reverse import is unavailable. ``workshops`` re-exports this
    name for back-compat.
    """
    if not title or not isinstance(title, str):
        return ''
    normalized = title.strip().lower()
    # Collapse any run of whitespace to a single space.
    normalized = re.sub(r'\s+', ' ', normalized)
    # Strip surrounding punctuation (but keep internal punctuation such as the
    # hyphen in "take-home" so distinct titles stay distinct).
    normalized = normalized.strip('.,;:!?-–—"\'')
    return normalized.strip()


def _resolve_heuristic_studio_event(title, start_datetime):
    """Conservative title + same-UTC-day adopt match for STANDARD events.

    Issue #998 — the standalone-event analogue of
    ``_resolve_heuristic_workshop_event``. Used by ``_dispatch_events`` just
    before it would mint a brand-new ``origin='github'`` row: if exactly one
    pre-existing Studio event looks like the same real session, the dispatcher
    ADOPTS it instead of creating a duplicate.

    Returns ``(matched_count, event)``:

    - ``(0, None)`` when nothing matches — caller mints a new event (unchanged
      zero-match behaviour).
    - ``(1, <Event>)`` when EXACTLY ONE candidate matches on the same UTC
      calendar day AND normalized title — caller adopts it.
    - ``(n, None)`` with ``n > 1`` when ambiguous — caller refuses to guess,
      logs a warning, and falls through to the normal new-event path.

    Candidate set (conservative, false-positive guards):
    - ``origin='studio'`` only — github-origin rows are matched by the normal
      ``slug + source_repo`` path.
    - ``content_id__isnull=True`` — a studio event that already carries a
      ``content_id`` was already adopted; excluding it prevents re-adoption
      churn.
    - ``event_series__isnull=True`` — recurring-series occurrences share a
      title across many dates; scoping the heuristic to non-series rows keeps
      it conservative (a series occurrence should be linked explicitly).

    Match key:
    - Date: ``event.start_datetime`` date in UTC equals the incoming event's
      start date in UTC (``TruncDate(..., tzinfo=utc)``). Studio rows carry
      real local times while GitHub-authored YAML may carry a date-only /
      00:00 UTC value, so we compare on DATE, not datetime.
    - Title: normalized equality of ``event.title`` and the incoming title.
    """
    import datetime as dt

    from django.db.models.functions import TruncDate
    from events.models import Event

    target_title = _normalize_title_for_match(title)
    if not target_title or start_datetime is None:
        return 0, None

    target_date = start_datetime.astimezone(dt.timezone.utc).date()

    # Narrow to non-series, content_id-less, same-UTC-day studio candidates in
    # the DB, then apply the normalized-title equality in Python (normalization
    # isn't expressible as a portable ORM filter). Truncate in UTC explicitly so
    # the calendar-day comparison is independent of the connection timezone.
    same_day = Event.objects.filter(
        origin='studio',
        content_id__isnull=True,
        event_series__isnull=True,
    ).annotate(
        start_date=TruncDate('start_datetime', tzinfo=dt.timezone.utc),
    ).filter(start_date=target_date)

    matches = [
        event for event in same_day
        if _normalize_title_for_match(event.title) == target_title
    ]
    if len(matches) == 1:
        return 1, matches[0]
    return len(matches), None


def _adopt_candidate_owns_slug(adopt_event, slug):
    """Return True when the adopt candidate is the row that owns ``slug``.

    Issue #998. ``_check_slug_collision`` skips a file when a row with the
    same slug exists under a different ``source_repo`` (a studio row has an
    empty ``source_repo``). When that colliding row is exactly the studio
    event we are about to adopt, it is NOT a foreign-source collision — the
    file must proceed to the adopt path instead of being skipped. This guard
    is intentionally narrow: it only suppresses the collision skip when the
    single resolved adopt candidate carries the same slug, so a genuinely
    different same-slug event still hits the existing collision skip.
    """
    return adopt_event is not None and adopt_event.slug == slug


def _ambiguous_studio_candidate_ids(title, start_datetime):
    """List the ids of the studio events that make an adopt match ambiguous.

    Issue #998. Recomputed (cheaply) only on the >1-match skip-and-warn path
    so the logged warning and the ``stats['errors']`` entry can name the
    colliding candidate ids for an operator to resolve manually.
    """
    import datetime as dt

    from django.db.models.functions import TruncDate
    from events.models import Event

    target_title = _normalize_title_for_match(title)
    if not target_title or start_datetime is None:
        return []

    target_date = start_datetime.astimezone(dt.timezone.utc).date()
    same_day = Event.objects.filter(
        origin='studio',
        content_id__isnull=True,
        event_series__isnull=True,
    ).annotate(
        start_date=TruncDate('start_datetime', tzinfo=dt.timezone.utc),
    ).filter(start_date=target_date)

    return sorted(
        event.pk for event in same_day
        if _normalize_title_for_match(event.title) == target_title
    )


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
            except (ValueError, OSError):
                # Discovery pass only — a bad yaml here will be reported
                # by the main loop below. ``_parse_yaml_file`` wraps
                # ``yaml.YAMLError`` as ``ValueError``.
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

            # Resolve the incoming event start (used by the adopt heuristic
            # below and, on the zero-match path, by the new-event create
            # kwargs). Compute it once here so the adopt match — which runs
            # BEFORE the new-event branch — can compare on the same UTC day.
            incoming_start_dt = _coerce_event_datetime(
                data.get('start_datetime'),
            )
            if not incoming_start_dt:
                incoming_start_dt = _coerce_event_datetime(
                    data.get('published_at'),
                )
            if not incoming_start_dt:
                incoming_start_dt = timezone.now()

            # Issue #998: adopt-on-sync. Before the slug-collision gate, look
            # for a pre-existing, content_id-less, non-series studio event that
            # matches this incoming event by normalized title + same UTC day.
            # Exactly one match -> ADOPT it (attach this content_id, flip
            # origin) instead of minting a duplicate. >1 match -> skip-and-warn,
            # fall through to the normal new-event path. 0 match -> unchanged.
            #
            # Resolving the candidate here also reconciles with the slug
            # collision check: when the incoming YAML slug equals the matched
            # studio event's slug, that is NOT a foreign-source collision —
            # it is the row we are about to adopt, so we must not skip it.
            adopt_matched_count, adopt_event = _resolve_heuristic_studio_event(
                data.get('title', slug), incoming_start_dt,
            )

            # Slug collision check. Skip it only when the colliding row is
            # exactly the studio event we are about to adopt (same slug, no
            # content_id, non-series, normalized-title + same-UTC-day match).
            # A genuinely different event that merely shares a slug still hits
            # the collision skip.
            if not _adopt_candidate_owns_slug(adopt_event, slug):
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
            except (ValueError, OSError) as exc:
                # ``ValueError`` covers frontmatter / path-validation /
                # YAML parse failures; ``OSError`` covers missing files
                # and read errors. Other exception types (template
                # syntax bugs, etc.) propagate to the outer per-file
                # handler which records them into ``stats['errors']``.
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
            # Issue #797: validate against the known image set so a missing
            # reference surfaces as a partial-sync error rather than a
            # broken CDN URL.
            cover_image_url = _UNSET
            cover_image = data.get('cover_image', '')
            if cover_image:
                cover_image_url = rewrite_cover_image_url(
                    cover_image, source, rel_path,
                    known_images=known_images, errors=stats['errors'],
                )

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

            # Look up by slug+source_repo (back-compat) first, then by
            # content_id+source_repo. The content_id lookup (issue #998) is
            # what makes a re-sync of an ADOPTED event idempotent: adoption
            # keeps the studio event's original slug, which differs from the
            # incoming YAML slug, so the slug lookup misses on the second
            # sync — but the content_id we attached on adopt still matches,
            # preventing a duplicate-content_id INSERT.
            event = find_synced_object((
                lambda: Event.objects.filter(
                    slug=slug, source_repo=source.repo_name,
                ).first(),
                lambda: Event.objects.filter(
                    content_id=event_content_id,
                    source_repo=source.repo_name,
                ).first(),
            ))
            create_kwargs = None
            # The slug recorded in seen_slugs / detail. On adopt this becomes
            # the studio event's existing slug (we never rename it), so stale
            # cleanup never unpublishes the adopted row.
            effective_slug = slug
            if event is not None:
                # A github-origin row already owns this content_id/slug. Keep
                # its current slug for stale-cleanup/detail (it may be the
                # studio slug retained from a prior adopt, not the YAML slug).
                effective_slug = event.slug
            else:
                # Issue #998: no github-origin row exists for this slug yet.
                # Before minting a new event, try to adopt a matching studio
                # event so we never create a second row for one real session.
                if adopt_matched_count > 1:
                    # Ambiguous: refuse to guess. A wrong adopt corrupts a real
                    # Studio event; a logged duplicate is recoverable via
                    # events/services/event_merge.py. Skip the adopt and fall
                    # through to the normal new-event path, recording the
                    # ambiguity so an operator can resolve it manually.
                    candidate_ids = _ambiguous_studio_candidate_ids(
                        data.get('title', slug), incoming_start_dt,
                    )
                    msg = (
                        f'Adopt-on-sync ambiguous for {rel_path}: '
                        f'{adopt_matched_count} content_id-less studio events '
                        f'match title {data.get("title", slug)!r} on '
                        f'{incoming_start_dt.astimezone(dt.timezone.utc).date().isoformat()} '
                        f'(candidate ids: {candidate_ids}). Not adopting — '
                        f'creating a new event instead. Resolve manually.'
                    )
                    logger.warning(msg)
                    stats['errors'].append({'file': rel_path, 'error': msg})

                if adopt_matched_count == 1 and adopt_event is not None:
                    # Exactly one match -> ADOPT. Route the incoming content
                    # defaults through the matched studio event. The defaults
                    # carry origin='github' + source_repo + source_path +
                    # source_commit + content_id together, so the #564 save
                    # invariant flips atomically. Operational fields
                    # (start_datetime, status, zoom_*, slug, registrations) are
                    # NOT in content_defaults, so they survive on the adopted
                    # row. Keep the studio slug as canonical.
                    event = adopt_event
                    effective_slug = adopt_event.slug
                    create_kwargs = None
                else:
                    # Zero-match (or ambiguous fall-through): mint a new
                    # github-origin event exactly as before. Content-repo
                    # events default to recording-style rows unless
                    # operational frontmatter says otherwise.
                    create_kwargs = {
                        'slug': slug,
                        'start_datetime': incoming_start_dt,
                        'end_datetime': _coerce_event_datetime(
                            data.get('end_datetime'),
                        ),
                        'status': data.get('status') or 'completed',
                        'timezone': data.get('timezone') or settings.TIME_ZONE,
                        'platform': data.get('platform') or 'zoom',
                        'location': data.get('location', '') or '',
                        'published': data.get('published', True),
                    }

            # Record the effective slug (studio slug on adopt) so stale
            # cleanup keeps the row and the detail link points at the right
            # slug.
            if effective_slug != slug:
                seen_slugs.discard(slug)
                seen_slugs.add(effective_slug)

            result = _upsert_synced_event_content(
                lookup=lambda: event,
                defaults=content_defaults,
                stats=stats,
                create_kwargs=create_kwargs,
                detail_slug=effective_slug,
            )
            event = result.instance
            if result.created:
                _maybe_create_zoom_meeting_for_synced_event(event, data)

            # Issue #595: warn (don't block) when the rendered description
            # or recap HTML still links to a retired URL prefix
            # (e.g. /event-recordings/...). Only check on a write —
            # unchanged rows already passed this gate during their own
            # sync. Both ``description_html`` and ``recap_html`` are
            # author-supplied markdown so both are scanned.
            if result.changed:
                from content.utils.legacy_urls import detect_legacy_urls
                detect_legacy_urls(
                    event.description_html, rel_path, stats['errors'],
                )
                detect_legacy_urls(
                    event.recap_html, rel_path, stats['errors'],
                )

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

            resolved_hosts = _resolve_hosts_for_event_yaml(
                data, rel_path, stats, legacy_name_field='speaker_name',
            )
            _attach_hosts_to_event(event, resolved_hosts)

        except Exception as e:
            # Intentional broad catch: a single malformed event file must
            # not abort the whole sync; the error is recorded into
            # ``stats['errors']`` and surfaced in the SyncLog row.
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except (AttributeError, TypeError):
                # ``data`` may be ``None`` or a non-dict yaml top level
                # (the parser raised before normalising). Both cases are
                # safe to treat as "no slug" and fall back to filename.
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
            # Issue #673: include the id so the Studio history link can
            # render the canonical id+slug URL.
            'id': ev.pk,
            'action': action,
            'content_type': 'event',
        },
        cleanup=lambda events: Event.objects.filter(
            pk__in=[event.pk for event in events],
        ).update(published=False),
    )
