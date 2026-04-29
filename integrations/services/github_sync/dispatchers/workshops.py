"""Workshop sync dispatcher."""

# ruff: noqa

import os
import re
import uuid

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

from integrations.services.github_sync.dispatchers.instructors import _attach_instructors_to_workshop, _resolve_instructors_for_yaml
from integrations.services.github_sync.dispatchers.events import _coerce_event_datetime, _event_requests_zoom_meeting, _maybe_create_zoom_meeting_for_synced_event
from integrations.services.github_sync.dispatchers.courses import _build_workshop_page_lookup, _resolve_workshop_landing_copy

def _coerce_workshop_date(value):
    """Parse a workshop ``date:`` frontmatter value into a ``datetime.date``.

    Accepts strings (ISO format) and ``datetime.date`` / ``datetime.datetime``
    values (PyYAML often parses an ISO date directly into a ``date``).
    Returns ``None`` on empty input so the caller can decide whether to fall
    back to the folder-name date prefix.
    """
    import datetime as dt

    if value in (None, ''):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value.strip())
    raise ValueError(f'Unsupported workshop date value: {value!r}')


def _extract_workshop_folder_date(folder_name):
    """Pull a ``YYYY-MM-DD`` prefix out of a workshop folder name if present.

    Folder names follow the ``YYYY-MM-DD-slug`` convention. Returns a
    ``datetime.date`` on a match or ``None`` otherwise. Used as a fallback
    when ``workshop.yaml`` omits the ``date:`` key.
    """
    import datetime as dt

    match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', folder_name)
    if not match:
        return None
    try:
        return dt.date(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
        )
    except ValueError:
        return None


def _dispatch_workshops(source, repo_dir, workshop_dirs, commit_sha, stats,
                        known_images=None):
    """Walker dispatch handler: process workshop directories.

    ``workshop_dirs`` is the list of absolute paths to dirs containing
    ``workshop.yaml`` (collected by ``_classify_repo_files``).

    Stale workshops (folder deleted between syncs) are set to
    ``status='draft'``. The linked Event is NOT unpublished — it's
    standalone and may have been edited independently in Studio.
    """
    from content.models import Workshop

    seen_slugs = set()
    failed_slugs = set()

    for workshop_path in workshop_dirs:
        _sync_single_workshop(
            workshop_path, repo_dir, source, commit_sha, stats,
            seen_slugs, failed_slugs, known_images=known_images,
        )

    # Stale cleanup: workshops whose source folder disappeared this sync.
    stale = Workshop.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    for ws in stale:
        stats['items_detail'].append({
            'title': ws.title,
            'slug': ws.slug,
            'action': 'deleted',
            'content_type': 'workshop',
        })
    deleted_count = stale.count()
    stale.update(status='draft')
    stats['deleted'] += deleted_count


def _sync_single_workshop(
    workshop_dir, repo_dir, source, commit_sha, stats,
    seen_slugs, failed_slugs, known_images=None,
):
    """Parse one ``workshop.yaml`` folder into a ``Workshop`` with pages.

    Mirrors ``_sync_single_course`` but without a module layer. Validates
    frontmatter (including the split-gate rule) before writing anything and
    logs per-file errors to ``stats['errors']`` rather than aborting the sync.
    """
    # Deferred imports: the integrations service is loaded by the Django
    # AppConfig chain, and importing content/events models at module-top
    # would tie those apps' import timing to this one. Matches the pattern
    # used by every other ``_sync_*`` helper in this file.
    from content.access import VISIBILITY_CHOICES
    from content.models import Workshop

    yaml_path = os.path.join(workshop_dir, 'workshop.yaml')
    data = None
    try:
        data = _parse_yaml_file(yaml_path)
        rel_path = os.path.relpath(workshop_dir, repo_dir)
        yaml_rel_path = os.path.relpath(yaml_path, repo_dir)

        # Required frontmatter: content_id, slug, title, pages_required_level.
        _validate_frontmatter(data, 'workshop', yaml_rel_path)

        workshop_content_id = data.get('content_id')
        slug = data.get('slug')
        title = data.get('title')
        pages_required_level = data.get('pages_required_level')

        # Validate pages_required_level is a legal visibility tier level.
        valid_levels = {level for level, _ in VISIBILITY_CHOICES}
        if pages_required_level not in valid_levels:
            raise ValueError(
                f'Invalid pages_required_level={pages_required_level!r}; '
                f'must be one of {sorted(valid_levels)}'
            )

        # Landing gate (optional, default 0). Must be <= pages gate.
        # Fails closed so a misconfigured yaml can never leak gated
        # content under a stricter gate than intended.
        landing_required_level = data.get('landing_required_level', 0)
        if landing_required_level not in valid_levels:
            raise ValueError(
                f'Invalid landing_required_level={landing_required_level!r}; '
                f'must be one of {sorted(valid_levels)}'
            )
        if landing_required_level > pages_required_level:
            raise ValueError(
                f'landing_required_level ({landing_required_level}) must be '
                f'<= pages_required_level ({pages_required_level}).'
            )

        # Recording block (optional). When present and ``url`` is set,
        # ``required_level`` must be set AND must be >= pages_required_level.
        # Fails closed — missing or too-low gate means no workshop row.
        recording = data.get('recording') or {}
        if not isinstance(recording, dict):
            raise ValueError(
                f'recording must be a mapping/dict, got {type(recording).__name__}'
            )
        recording_url = recording.get('url', '') or ''
        if recording_url:
            recording_required_level = recording.get('required_level')
            if recording_required_level is None:
                raise ValueError(
                    'recording.url is set but recording.required_level is '
                    'missing — refusing to leak the recording under the '
                    'pages_required_level gate.'
                )
            if recording_required_level not in valid_levels:
                raise ValueError(
                    f'Invalid recording.required_level='
                    f'{recording_required_level!r}; must be one of '
                    f'{sorted(valid_levels)}'
                )
            if recording_required_level < pages_required_level:
                raise ValueError(
                    f'recording.required_level ({recording_required_level}) '
                    f'must be >= pages_required_level ({pages_required_level}).'
                )
        else:
            # No recording URL — default the gate to pages_required_level
            # so the model invariant holds. When the recording is added
            # later, the author must update the yaml with a proper level.
            recording_required_level = pages_required_level

        # Slug collision check across sources.
        if _check_slug_collision(Workshop, slug, source.repo_name, rel_path):
            stats['errors'].append({
                'file': yaml_rel_path,
                'error': (
                    f"Slug collision: '{slug}' already exists from a "
                    f"different source. Skipped."
                ),
            })
            failed_slugs.add(slug)
            return

        seen_slugs.add(slug)

        # Workshop date: prefer ``date:`` frontmatter, fall back to the
        # ``YYYY-MM-DD`` prefix on the folder name.
        workshop_date = _coerce_workshop_date(data.get('date'))
        if workshop_date is None:
            workshop_date = _extract_workshop_folder_date(
                os.path.basename(workshop_dir.rstrip(os.sep)),
            )
        if workshop_date is None:
            raise ValueError(
                'workshop.yaml is missing a `date:` and the folder name '
                "doesn't start with YYYY-MM-DD — can't infer workshop date."
            )

        # Cover image — rewrite relative paths to CDN URLs like course.yaml.
        cover_image_url = rewrite_cover_image_url(
            data.get('cover_image', '') or data.get('cover_image_url', ''),
            source, yaml_rel_path,
        )

        # Issue #304: build the page lookup once and reuse it for the
        # landing-description copy_file resolution AND the per-page
        # rewriting in _sync_workshop_pages. The lookup includes virtual
        # entries for README.md (and an explicit copy_file when set), so
        # ``[README.md](README.md)`` resolves to the landing URL instead
        # of emitting a broken-link warning.
        page_lookup = _build_workshop_page_lookup(
            workshop_dir, slug, workshop_title=title,
            copy_file=data.get('copy_file'),
        )

        # Resolve the landing description: copy_file (explicit) -> README.md
        # (implicit default) -> yaml description: -> empty. The body is
        # frontmatter-stripped, leading-H1-stripped, image-URL-rewritten,
        # and intra-workshop-link-rewritten. ``Workshop.save()`` will then
        # render description_html through render_markdown exactly once.
        landing_description = _resolve_workshop_landing_copy(
            workshop_dir, data, rel_path, page_lookup, slug,
            source.repo_name, stats['errors'],
        )

        workshop_defaults = {
            'title': title,
            'description': landing_description,
            'date': workshop_date,
            'instructor_name': data.get('instructor_name', '') or '',
            'tags': data.get('tags', []) or [],
            'cover_image_url': cover_image_url,
            'status': 'published',
            'landing_required_level': landing_required_level,
            'pages_required_level': pages_required_level,
            'recording_required_level': recording_required_level,
            'code_repo_url': data.get('code_repo_url', '') or '',
            'source_repo': source.repo_name,
            'source_path': rel_path,
            'source_commit': commit_sha,
            'content_id': workshop_content_id,
        }

        # Find by content_id (stable) first, then slug (backward compat).
        workshop = Workshop.objects.filter(
            content_id=workshop_content_id,
            source_repo=source.repo_name,
        ).first()
        if workshop is None:
            workshop = Workshop.objects.filter(
                slug=slug,
                source_repo=source.repo_name,
            ).first()

        if workshop is None:
            workshop = Workshop(slug=slug, **workshop_defaults)
            workshop.save()
            created = True
            changed = True
        else:
            identity_changed = (
                workshop.slug != slug
                or workshop.source_path != rel_path
            )
            if identity_changed or _defaults_differ(workshop, workshop_defaults):
                workshop.slug = slug
                for k, v in workshop_defaults.items():
                    setattr(workshop, k, v)
                workshop.save()
                created = False
                changed = True
            else:
                created = False
                changed = False

        if changed:
            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': title,
                'slug': slug,
                'action': action,
                'content_type': 'workshop',
            })
        else:
            stats['unchanged'] += 1

        # Resolve instructors: list and attach M2M (post-save).
        resolved_instructors = _resolve_instructors_for_yaml(
            data, yaml_rel_path, stats,
        )
        _attach_instructors_to_workshop(
            workshop, resolved_instructors, stats,
        )

        # Link or create the Event. Shared slug — ``/events/<slug>`` and
        # ``/workshops/<slug>`` live under different prefixes.
        _link_or_create_workshop_event(
            workshop, data, recording, recording_required_level,
            workshop_date, source, rel_path, yaml_rel_path, commit_sha, stats,
        )

        # Sync pages — every *.md file in the folder except README.md.
        # Pass the pre-built page_lookup so the rewriter sees the same
        # virtual entries (README.md, copy_file) we used for the landing
        # description.
        _sync_workshop_pages(
            workshop, workshop_dir, repo_dir, source.repo_name,
            commit_sha, stats, known_images=known_images,
            page_lookup=page_lookup,
        )

    except Exception as e:
        try:
            failed_slug = (data or {}).get(
                'slug', os.path.basename(workshop_dir.rstrip(os.sep)),
            )
        except Exception:
            failed_slug = os.path.basename(workshop_dir.rstrip(os.sep))
        failed_slugs.add(failed_slug)
        stats['errors'].append({
            'file': os.path.relpath(yaml_path, repo_dir),
            'error': str(e),
        })
        logger.warning(
            'Error syncing workshop %s: %s',
            os.path.basename(workshop_dir.rstrip(os.sep)), e,
        )


def _link_or_create_workshop_event(
    workshop, data, recording, recording_required_level, workshop_date,
    source, rel_path, yaml_rel_path, commit_sha, stats,
):
    """Attach a matching ``Event`` to ``workshop``, creating one if missing.

    Idempotency: if an Event already exists with ``slug == workshop.slug``
    we link to it and update *content* fields only (recording metadata,
    title, description, tags, etc.). Operational fields — ``start_datetime``,
    ``end_datetime``, ``status``, ``zoom_*`` — are intentionally left
    alone. Running the sync a second time never creates a second Event.

    The Event carries its own ``content_id`` separate from the Workshop's
    (they're different models). We mint a stable UUIDv5 keyed by
    ``(repo, source_path)`` so re-syncs pick up the same event row.
    """
    import datetime as dt

    # Deferred imports: same rationale as _sync_single_workshop — content
    # and events models are loaded lazily to keep the integrations app
    # importable independently of the content app's readiness.
    from content.models import Workshop
    from events.models import Event

    # Content fields we always update from the workshop.yaml
    tags = data.get('tags', []) or []
    materials = recording.get('materials', []) or []
    timestamps = recording.get('timestamps', []) or []
    recording_url = recording.get('url', '') or ''
    recording_embed_url = recording.get('embed_url', '') or ''

    content_defaults = {
        'title': workshop.title,
        'description': workshop.description,
        'tags': tags,
        'cover_image_url': workshop.cover_image_url,
        'recording_url': recording_url,
        'recording_embed_url': recording_embed_url,
        'timestamps': timestamps,
        'materials': materials,
        'required_level': recording_required_level,
        'speaker_name': workshop.instructor_name,
        'kind': 'workshop',
        'content_id': _derive_workshop_event_content_id(
            source.repo_name, rel_path,
        ),
        'source_repo': source.repo_name,
        'source_path': yaml_rel_path,
        'source_commit': commit_sha,
    }

    # Look up by slug first — that's the idempotent key per the spec.
    event = Event.objects.filter(slug=workshop.slug).first()
    if event is None:
        start_dt = dt.datetime.combine(
            workshop_date, dt.time.min, tzinfo=dt.timezone.utc,
        )
        event = Event(
            slug=workshop.slug,
            start_datetime=start_dt,
            status='completed',
            published=True,
            **content_defaults,
        )
        event.save()
        stats['items_detail'].append({
            'title': workshop.title,
            'slug': workshop.slug,
            'action': 'created',
            'content_type': 'event',
        })
        stats['created'] += 1
    else:
        # Existing Event: update *content* fields only. Operational fields
        # (start_datetime, status, join fields, published) are not in
        # content_defaults so they're never touched.
        if _defaults_differ(event, content_defaults):
            for k, v in content_defaults.items():
                setattr(event, k, v)
            event.save()
            stats['items_detail'].append({
                'title': event.title,
                'slug': event.slug,
                'action': 'updated',
                'content_type': 'event',
            })
            stats['updated'] += 1
        else:
            stats['unchanged'] += 1

    # Link the Workshop to the Event if not already linked (or if linked
    # to a stale event row). Use update() to avoid re-running Workshop.save()
    # and the render pipeline.
    if workshop.event_id != event.pk:
        Workshop.objects.filter(pk=workshop.pk).update(event=event)
        workshop.event_id = event.pk


def _derive_workshop_event_content_id(repo_name, workshop_source_path):
    """Stable UUIDv5 for the Event row linked to a workshop.

    Deliberately distinct from the Workshop's ``content_id`` — Event and
    Workshop are different models with different stable IDs. Keyed on
    ``(repo_name, source_path)`` so re-syncs reuse the same Event row.
    """
    key = f'{repo_name}:{workshop_source_path}:workshop_event'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _sync_workshop_pages(
    workshop, workshop_dir, repo_dir, repo_name, commit_sha, stats,
    known_images=None, page_lookup=None,
):
    """Sync ``*.md`` pages under a workshop folder into ``WorkshopPage`` rows.

    Filename convention: ``NN-slug.md`` where ``NN`` is the sort order
    (e.g. ``01-overview.md`` -> ``sort_order=1, slug='overview'``).
    Frontmatter ``slug`` / ``sort_order`` override the filename-derived
    values. ``README.md`` is excluded (workshops don't surface READMEs
    in the page list — they'd clash with the auto-derived index page).

    Pages whose source files disappeared are hard-deleted — unlike
    course units, WorkshopPages don't carry user progress yet, so a
    soft-delete layer would be dead weight.

    ``page_lookup`` is an optional pre-built ``{filename -> page-meta}``
    map (issue #304) — when supplied, it's reused as-is so the same
    virtual entries (README.md, copy_file) seen by the landing
    description resolver are also visible to the per-page rewriter. When
    omitted (legacy callers, tests), the lookup is built locally without
    the README virtual entry.
    """
    # Function-scoped imports keep the integrations service decoupled
    # from content app readiness (matches the pattern used elsewhere in
    # this file). parse_video_timestamp is imported here so the diff
    # against #301 stays localised to this function.
    from content.models import WorkshopPage
    from content.templatetags.video_utils import parse_video_timestamp
    from content.utils.md_links import rewrite_workshop_md_links

    # Issue #301/#304: build a {filename -> page-meta} lookup once per
    # workshop so the link rewriter can resolve sibling ``.md`` links
    # across all pages. Reuse the caller-provided lookup when present
    # (issue #304 hoists this to ``_sync_single_workshop`` so the same
    # lookup serves both landing-description and per-page rewriting).
    if page_lookup is None:
        page_lookup = _build_workshop_page_lookup(
            workshop_dir, workshop.slug, workshop_title=workshop.title,
        )

    seen_paths = set()

    for filename in sorted(os.listdir(workshop_dir)):
        if (
            not filename.endswith('.md')
            or filename.upper() == 'README.MD'
            or filename.startswith('.')
        ):
            continue

        filepath = os.path.join(workshop_dir, filename)
        if not os.path.isfile(filepath):
            continue
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Required field: title (body without a title would render poorly).
            _validate_frontmatter(metadata, 'workshop_page', rel_path)

            # Derive slug / sort_order from filename unless overridden.
            sort_order = metadata.get(
                'sort_order', extract_sort_order(filename),
            )
            slug = metadata.get('slug', derive_slug(filename))

            # content_id: explicit in frontmatter, or derive stable UUID.
            content_id = metadata.get('content_id')
            if not content_id:
                content_id = _derive_workshop_page_content_id(
                    repo_name, rel_path,
                )

            seen_paths.add(rel_path)

            # Rewrite relative image URLs and flag broken references so the
            # sync report surfaces them (same pattern as articles/units).
            base_dir = os.path.dirname(rel_path)
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, repo_name, base_dir,
                    known_images, stats['errors'],
                )
            body = rewrite_image_urls(body, repo_name, base_dir)
            # Issue #301: rewrite intra-workshop ``.md`` links to platform
            # URLs. Run on the raw markdown (before WorkshopPage.save() calls
            # markdown.markdown()) so the rewriter doesn't have to parse HTML.
            body = rewrite_workshop_md_links(
                body,
                workshop_slug=workshop.slug,
                page_lookup=page_lookup,
                source_path=rel_path,
                sync_errors=stats.get('errors'),
            )

            # Parse and validate the optional `video_start` frontmatter
            # key. Stored verbatim as a string when valid; logged to
            # stats['errors'] and stored as '' when malformed. Format:
            # MM:SS or H:MM:SS (see parse_video_timestamp).
            raw_video_start = (metadata.get('video_start') or '')
            if isinstance(raw_video_start, str):
                raw_video_start = raw_video_start.strip()
            else:
                raw_video_start = str(raw_video_start).strip()
            video_start = ''
            if raw_video_start:
                try:
                    parse_video_timestamp(raw_video_start)
                except ValueError as exc:
                    stats['errors'].append({
                        'file': rel_path,
                        'error': (
                            f'Invalid video_start={raw_video_start!r} '
                            f'(must be MM:SS or H:MM:SS): {exc}'
                        ),
                    })
                else:
                    video_start = raw_video_start

            defaults = {
                'title': metadata['title'],
                'slug': slug,
                'sort_order': sort_order,
                'body': body,
                'video_start': video_start,
                'source_repo': repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': content_id,
            }

            # Look up by (workshop, slug) — that's the unique-constraint
            # key. Falling back to (workshop, source_path) misses when a
            # file is renamed but slug stays the same, then INSERT would
            # collide on the unique constraint instead of doing an update.
            page = WorkshopPage.objects.filter(
                workshop=workshop, slug=slug,
            ).first()
            if page is None:
                page = WorkshopPage.objects.filter(
                    workshop=workshop, source_path=rel_path,
                ).first()

            if page is None:
                page = WorkshopPage(workshop=workshop, **defaults)
                page.save()
                created = True
                changed = True
            else:
                if _defaults_differ(page, defaults):
                    for k, v in defaults.items():
                        setattr(page, k, v)
                    page.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            if not changed:
                stats['unchanged'] += 1
                continue

            action = 'created' if created else 'updated'
            if created:
                stats['created'] += 1
            else:
                stats['updated'] += 1
            stats['items_detail'].append({
                'title': page.title,
                'slug': page.slug,
                'action': action,
                'content_type': 'workshop_page',
            })

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning('Error syncing workshop page %s: %s', rel_path, e)

    # Hard-delete pages whose source files disappeared.
    stale = WorkshopPage.objects.filter(workshop=workshop).exclude(
        source_path__in=seen_paths,
    )
    deleted_count = stale.count()
    if deleted_count:
        for p in stale:
            stats['items_detail'].append({
                'title': p.title,
                'slug': p.slug,
                'action': 'deleted',
                'content_type': 'workshop_page',
            })
        stale.delete()
        stats['deleted'] += deleted_count
