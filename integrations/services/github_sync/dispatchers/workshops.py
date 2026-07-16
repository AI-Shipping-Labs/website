"""Workshop sync dispatcher."""

import os
import re
import uuid

from integrations.services.banner_generator.dispatch import enqueue_if_missing as _enqueue_banner_if_missing
from integrations.services.github_sync.common import logger
from integrations.services.github_sync.dispatchers.courses import (
    _build_workshop_page_lookup,
    _parse_access_value,
    _resolve_workshop_landing_copy,
)
from integrations.services.github_sync.dispatchers.events import (
    _build_synced_event_content_defaults,
    _normalize_title_for_match,
    _upsert_synced_event_content,
)
from integrations.services.github_sync.dispatchers.hosts import _attach_hosts_to_event, _resolve_hosts_for_event_yaml
from integrations.services.github_sync.dispatchers.instructors import (
    _attach_instructors_to_workshop,
    _resolve_instructors_for_yaml,
)
from integrations.services.github_sync.lifecycle import (
    cleanup_stale_synced_objects,
    find_synced_object,
    upsert_synced_object,
)
from integrations.services.github_sync.media import (
    _check_broken_image_refs,
    rewrite_cover_image_url,
    rewrite_image_urls,
)
from integrations.services.github_sync.parsing import (
    _check_slug_collision,
    _derive_workshop_page_content_id,
    _parse_markdown_file,
    _parse_yaml_file,
    _validate_frontmatter,
)
from integrations.services.github_sync.repo import derive_slug, extract_sort_order


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


def _validate_workshop_materials(raw, yaml_rel_path):
    """Validate workshop-level ``materials:`` and return a normalized list.

    Issue #646. Each item must be a dict with at least ``title`` (str)
    and ``url`` (str). ``type`` is optional. On bad shape, raises
    ``ValueError`` with the file path so the caller's ``except`` arm
    records the error in ``stats['errors']`` and skips the workshop
    (same failure mode as other workshop yaml errors).

    Accepts ``None``, missing key, or empty list as "no materials" and
    returns ``[]`` so callers can write the value verbatim.
    """
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f'workshop.yaml `materials:` must be a list of {{title, url, '
            f'type}} dicts, got {type(raw).__name__} ({yaml_rel_path}).'
        )
    cleaned = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f'workshop.yaml `materials[{idx}]` must be a dict with '
                f'`title` and `url`, got {type(item).__name__} '
                f'({yaml_rel_path}).'
            )
        title = item.get('title')
        url = item.get('url')
        if not isinstance(title, str) or not title.strip():
            raise ValueError(
                f'workshop.yaml `materials[{idx}]` is missing a non-empty '
                f'`title` ({yaml_rel_path}).'
            )
        if not isinstance(url, str) or not url.strip():
            raise ValueError(
                f'workshop.yaml `materials[{idx}]` is missing a non-empty '
                f'`url` ({yaml_rel_path}).'
            )
        normalized = {'title': title.strip(), 'url': url.strip()}
        type_ = item.get('type')
        if isinstance(type_, str) and type_.strip():
            normalized['type'] = type_.strip()
        cleaned.append(normalized)
    return cleaned


def _validate_workshop_core_tools(raw, yaml_rel_path):
    """Validate workshop ``core_tools:`` and return normalized display names.

    Missing, empty, or blank-only lists are valid and normalize to ``[]``.
    Non-list values and non-string list items fail closed so malformed
    metadata is visible in sync errors instead of silently landing in the
    public filter surface.
    """
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f'workshop.yaml `core_tools:` must be a list of strings, got '
            f'{type(raw).__name__} ({yaml_rel_path}).'
        )

    cleaned = []
    seen = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, str):
            raise ValueError(
                f'workshop.yaml `core_tools[{idx}]` must be a string, got '
                f'{type(item).__name__} ({yaml_rel_path}).'
            )
        tool = item.strip()
        if not tool:
            continue
        key = tool.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(tool)
    return cleaned


def _dispatch_workshops(source, repo_dir, workshop_dirs, commit_sha, stats,
                        known_images=None, cross_workshop_lookup=None,
                        workshops_repo_name=None):
    """Walker dispatch handler: process workshop directories.

    ``workshop_dirs`` is the list of absolute paths to dirs containing
    ``workshop.yaml`` (collected by ``_classify_repo_files``).

    Stale workshops (folder deleted between syncs) are set to
    ``status='draft'``. The linked Event is NOT unpublished — it's
    standalone and may have been edited independently in Studio.

    ``cross_workshop_lookup`` (issue #526) is the sync-wide
    ``{folder_name: workshop-meta}`` map built by
    ``_build_cross_workshop_lookup``. It is threaded down so each page's
    body can resolve cross-workshop ``..``-style and absolute-GitHub URL
    links to native ``/workshops/<slug>`` URLs. ``workshops_repo_name``
    pairs with it so the GitHub-URL detector matches the right host.
    """
    seen_slugs = set()
    failed_slugs = set()

    for workshop_path in workshop_dirs:
        _sync_single_workshop(
            workshop_path, repo_dir, source, commit_sha, stats,
            seen_slugs, failed_slugs, known_images=known_images,
            cross_workshop_lookup=cross_workshop_lookup,
            workshops_repo_name=workshops_repo_name,
        )

    _cleanup_stale_workshops_for_source(
        source, seen_slugs, failed_slugs, stats,
    )


def _cleanup_stale_workshops_for_source(source, seen_slugs, failed_slugs, stats):
    from content.models import Workshop

    stale = Workshop.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    cleanup_stale_synced_objects(
        stale,
        stats=stats,
        detail=lambda ws, action: {
            'title': ws.title,
            'slug': ws.slug,
            # Include the workshop date for historical sync context.
            'date': ws.date.isoformat(),
            'action': action,
            'content_type': 'workshop',
        },
        cleanup=_mark_stale_workshops_draft,
    )


def _mark_stale_workshops_draft(workshops):
    from content.models import Workshop

    Workshop.objects.filter(
        pk__in=[workshop.pk for workshop in workshops],
    ).update(status='draft')


def _sync_single_workshop(
    workshop_dir, repo_dir, source, commit_sha, stats,
    seen_slugs, failed_slugs, known_images=None,
    cross_workshop_lookup=None, workshops_repo_name=None,
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
    from content.access import UNIT_VISIBILITY_CHOICES
    from content.models import Workshop, normalize_workshop_skill_level

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
        # All three workshop gates accept LEVEL_REGISTERED (5) — "free but
        # requires sign-in" — in addition to the open/paid tiers, matching
        # the model fields' UNIT_VISIBILITY_CHOICES.
        valid_levels = {level for level, _ in UNIT_VISIBILITY_CHOICES}
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
        recording_url = str(recording.get('url', '') or '').strip()
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
        # Issue #797: validate the resolved path against the set of images
        # the S3 uploader actually saw; surface missing references into
        # ``stats['errors']`` so the SyncLog status downgrades to
        # ``partial`` instead of shipping a broken CDN URL.
        cover_image_url = rewrite_cover_image_url(
            data.get('cover_image', '') or data.get('cover_image_url', ''),
            source, yaml_rel_path,
            known_images=known_images, errors=stats['errors'],
        )

        try:
            skill_level = normalize_workshop_skill_level(
                data.get('skill_level'),
            )
        except ValueError as exc:
            raise ValueError(f'{exc} ({yaml_rel_path})') from exc

        # Issue #646: top-level ``materials:`` key on the workshop yaml.
        # Workshop-scoped materials are gated by ``pages_required_level``;
        # they coexist with (and override) ``recording.materials`` which
        # continues to populate ``Event.materials`` further down. Fail
        # closed on a bad shape (string, missing title/url) so a malformed
        # entry doesn't leak through silently. Empty/missing key is fine
        # — the resolution rule falls back to ``Event.materials``.
        workshop_materials = _validate_workshop_materials(
            data.get('materials'), yaml_rel_path,
        )

        core_tools = _validate_workshop_core_tools(
            data.get('core_tools'), yaml_rel_path,
        )

        # Issue #304: build the page lookup once and reuse it for the
        # landing-description copy_file resolution AND the per-page
        # rewriting in _sync_workshop_pages. The lookup includes virtual
        # entries for README.md (and an explicit copy_file when set), so
        # ``[README.md](README.md)`` resolves to the landing URL instead
        # of emitting a broken-link warning.
        #
        # Slug-only workshop URLs are canonical; keep a single key threaded
        # through all sync-generated links.
        workshop_url_key = slug
        page_lookup = _build_workshop_page_lookup(
            workshop_dir, slug, workshop_title=title,
            copy_file=data.get('copy_file'),
            workshop_url_key=workshop_url_key,
        )

        # Resolve the landing description: copy_file (explicit) -> README.md
        # (implicit default) -> yaml description: -> empty. The body is
        # frontmatter-stripped, leading-H1-stripped, image-URL-rewritten,
        # and intra-workshop-link-rewritten. ``Workshop.save()`` will then
        # render description_html through render_markdown exactly once.
        # Issue #526: also pass through the cross-workshop rewriter so
        # ``[Previous workshop](../<sibling-folder>/)`` in README.md
        # resolves on the workshop landing page too.
        landing_description = _resolve_workshop_landing_copy(
            workshop_dir, data, rel_path, page_lookup, slug,
            source.repo_name, stats['errors'],
            cross_workshop_lookup=cross_workshop_lookup,
            workshops_repo_name=workshops_repo_name,
        )

        workshop_defaults = {
            'title': title,
            'description': landing_description,
            'date': workshop_date,
            'tags': data.get('tags', []) or [],
            'skill_level': skill_level,
            'core_tools': core_tools,
            'cover_image_url': cover_image_url,
            'status': 'published',
            'landing_required_level': landing_required_level,
            'pages_required_level': pages_required_level,
            'recording_required_level': recording_required_level,
            'code_repo_url': data.get('code_repo_url', '') or '',
            'materials': workshop_materials,
            'source_repo': source.repo_name,
            'source_path': rel_path,
            'source_commit': commit_sha,
            'content_id': workshop_content_id,
        }

        # Find by content_id only. Workshop content_id is mandatory and is
        # the stable sync identity across slug/source-path changes.
        workshop = find_synced_object((
            lambda: Workshop.objects.filter(
                content_id=workshop_content_id,
                source_repo=source.repo_name,
            ).first(),
        ))
        if workshop is None and Workshop.objects.filter(slug=slug).exists():
            raise ValueError(
                f"Workshop slug '{slug}' already exists with a different "
                'content_id. Refusing to match by slug; fix the incoming '
                'workshop.yaml content_id or choose a new slug.'
            )

        result = upsert_synced_object(
            model=Workshop,
            lookup=lambda: workshop,
            defaults=workshop_defaults,
            stats=stats,
            create_kwargs={'slug': slug},
            identity_changed=lambda obj: (
                obj.slug != slug
                or obj.source_path != rel_path
            ),
            apply_identity=lambda obj: setattr(obj, 'slug', slug),
            detail=lambda obj, action: {
                'title': title,
                'slug': slug,
                # Include the workshop date for historical sync context.
                'date': workshop_date.isoformat(),
                'action': action,
                'content_type': 'workshop',
            },
        )
        workshop = result.instance

        # Issue #595: warn (don't block) when the rendered workshop
        # landing description still links to a retired URL prefix
        # (e.g. /event-recordings/...). Only check on a write — unchanged
        # rows already passed this gate during their own sync.
        if result.changed:
            from content.utils.legacy_urls import detect_legacy_urls
            detect_legacy_urls(
                workshop.description_html, rel_path, stats['errors'],
            )

        # Issue #788/#900: enqueue auto-banner render on EVERY sync, not
        # only when the row changed. ``_enqueue_banner_if_missing`` itself
        # short-circuits when cover_image_url is set or when the title hash
        # already matches, so re-syncs are cheap — but a previously-synced
        # cover-less workshop whose first render was lost (e.g. a cold
        # Lambda timeout) gets backfilled on the next no-op sync instead of
        # being skipped forever.
        _enqueue_banner_if_missing('workshop', workshop.pk)

        # Resolve instructors: list and attach M2M (post-save).
        resolved_instructors = _resolve_instructors_for_yaml(
            data, yaml_rel_path, stats,
        )
        _attach_instructors_to_workshop(
            workshop, resolved_instructors, stats,
        )

        resolved_event_hosts = _resolve_hosts_for_event_yaml(
            data, yaml_rel_path, stats, legacy_name_field='instructor_name',
        )

        if recording_url:
            # Link or create the Event only for recording-backed workshops.
            # Shared slug — ``/events/<slug>`` and ``/workshops/<slug>`` live
            # under different prefixes.
            recording_for_event = dict(recording)
            recording_for_event['url'] = recording_url
            # Issue #879: optional explicit reference to an existing Studio
            # event. ``event_id`` (the Event pk) is primary; ``event_slug``
            # is a friendlier fallback resolved only when ``event_id`` is
            # absent. Threaded into the resolver so an author can bind a
            # GitHub workshop to the Studio event for the same session
            # instead of minting a duplicate.
            _link_or_create_workshop_event(
                workshop, data, recording_for_event, recording_required_level,
                workshop_date, source, rel_path, yaml_rel_path, commit_sha, stats,
                event_id=data.get('event_id'),
                event_slug=data.get('event_slug'),
                resolved_hosts=resolved_event_hosts,
            )
        else:
            _cleanup_generated_empty_workshop_event(
                workshop, source, stats,
            )

        # Sync pages — every *.md file in the folder except README.md.
        # Pass the pre-built page_lookup so the rewriter sees the same
        # virtual entries (README.md, copy_file) we used for the landing
        # description. Issue #526: also pass the sync-wide cross-workshop
        # lookup so each page can rewrite cross-workshop links.
        _sync_workshop_pages(
            workshop, workshop_dir, repo_dir, source.repo_name,
            commit_sha, stats, known_images=known_images,
            page_lookup=page_lookup,
            cross_workshop_lookup=cross_workshop_lookup,
            workshops_repo_name=workshops_repo_name,
            workshop_url_key=workshop_url_key,
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


# Content fields built by ``_build_synced_event_content_defaults`` that
# describe the GitHub SOURCE of a row rather than its displayable content.
# Issue #879: when LINKING a workshop to a pre-existing event (especially a
# Studio-origin one) these must NOT be applied — writing ``origin='github'``
# / ``source_repo`` onto an ``origin='studio'`` event would both flip its
# ownership and trip the ``Event.save()`` invariant. We strip them so the
# link-to-existing path updates displayable content only.
_EVENT_SOURCE_OWNERSHIP_FIELDS = (
    'origin',
    'source_repo',
    'source_path',
    'source_commit',
    'content_id',
)


def _resolve_heuristic_workshop_event(workshop, workshop_date):
    """Conservative title+date dedup match (issue #880).

    Runs only when there is no explicit reference (#879) and no slug match.
    Returns ``(matched_count, event)``:

    - ``(0, None)`` when nothing matches — caller falls through to the
      logged auto-create fallback (unchanged behaviour for genuinely new
      workshops).
    - ``(1, <Event>)`` when EXACTLY ONE existing event matches on the same
      calendar day AND normalized title — caller links to it, content fields
      only, never minting a duplicate.
    - ``(n, None)`` with ``n > 1`` when the heuristic is ambiguous — caller
      refuses to guess, records an error, and creates nothing.

    Match key:
    - Date: ``event.start_datetime`` date (in UTC) equals the workshop's
      calendar ``date``. The GitHub create path mints 00:00 UTC and Studio
      events carry real times, so we compare on DATE, not datetime.
    - Title: normalized equality of ``event.title`` and ``workshop.title``.

    Only events the link can safely attach to are considered. A pre-existing
    ``origin='github', kind='workshop'`` row for this same workshop is handled
    by the idempotent slug/content_id path, not here, so we restrict the
    candidate set to ``origin='studio'`` events.
    """
    import datetime as dt

    from django.db.models.functions import TruncDate

    from events.models import Event

    target_title = _normalize_title_for_match(workshop.title)
    if not target_title:
        return 0, None

    # Narrow to same-calendar-day studio candidates in the DB, then apply the
    # normalized-title equality in Python (normalization isn't expressible as a
    # portable ORM filter). Truncate in UTC explicitly so the calendar-day
    # comparison matches the 00:00 UTC the GitHub create path mints, regardless
    # of the active connection timezone.
    same_day = Event.objects.filter(origin='studio').annotate(
        start_date=TruncDate('start_datetime', tzinfo=dt.timezone.utc),
    ).filter(start_date=workshop_date)

    matches = [
        event for event in same_day
        if _normalize_title_for_match(event.title) == target_title
    ]
    if len(matches) == 1:
        return 1, matches[0]
    return len(matches), None


def _resolve_explicit_workshop_event(event_id, event_slug):
    """Resolve an explicit ``event_id`` / ``event_slug`` reference.

    Issue #879. Returns ``(reference_present, event)``:

    - ``(False, None)`` when neither key is set — caller falls through to the
      legacy slug-match-then-create path.
    - ``(True, <Event>)`` when the reference resolves to an existing event.
    - ``(True, None)`` when a reference WAS given but does not resolve — the
      caller must record an error and skip, never create a fallback duplicate.

    ``event_id`` takes precedence; ``event_slug`` is consulted only when
    ``event_id`` is absent.
    """
    from events.models import Event

    if event_id not in (None, ''):
        return True, Event.objects.filter(pk=event_id).first()
    if event_slug not in (None, ''):
        slug = str(event_slug).strip()
        if slug:
            return True, Event.objects.filter(slug=slug).first()
    return False, None


def _link_or_create_workshop_event(
    workshop, data, recording, recording_required_level, workshop_date,
    source, rel_path, yaml_rel_path, commit_sha, stats,
    event_id=None, event_slug=None, resolved_hosts=None,
):
    """Attach a matching ``Event`` to ``workshop``, creating one if missing.

    Issue #879 — explicit link model. When ``workshop.yaml`` declares an
    ``event_id`` (the Studio Event pk) or ``event_slug``, this resolves that
    exact event and links to it, updating *content* fields only. A bad
    reference (set but unresolvable) is reported into ``stats['errors']`` and
    the workshop is skipped this run — we never silently mint a duplicate to
    cover an author's typo. With no reference, the legacy slug-match-then-create
    path below runs unchanged (the no-reference heuristic is #880's job).

    Idempotency: if an Event already exists (resolved explicitly OR via the
    legacy ``slug == workshop.slug`` lookup) we link to it and update *content*
    fields only (recording metadata, title, description, tags, etc.).
    Operational fields — ``start_datetime``, ``end_datetime``, ``status``,
    ``zoom_*`` — are intentionally left alone. Running the sync a second time
    never creates a second Event.

    Origin invariant (issue #564/#879): when the resolved event is
    ``origin='studio'`` the link must keep it ``origin='studio'``,
    ``source_repo=''``. The WORKSHOP carries the GitHub source metadata, not
    the event, so the source-ownership content fields are stripped from the
    defaults on the link-to-existing branch.

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

    content_defaults = _build_synced_event_content_defaults(
        source=source,
        source_path=yaml_rel_path,
        commit_sha=commit_sha,
        content_id=_derive_workshop_event_content_id(
            source.repo_name, rel_path,
        ),
        title=workshop.title,
        description=workshop.description,
        tags=data.get('tags', []) or [],
        cover_image_url=workshop.cover_image_url,
        recording_url=recording.get('url', '') or '',
        recording_embed_url=recording.get('embed_url', '') or '',
        timestamps=recording.get('timestamps', []) or [],
        materials=recording.get('materials', []) or [],
        required_level=recording_required_level,
        kind='workshop',
    )

    # Issue #879: an explicit reference short-circuits the slug lookup.
    reference_present, explicit_event = _resolve_explicit_workshop_event(
        event_id, event_slug,
    )
    if reference_present and explicit_event is None:
        # A reference WAS given but didn't resolve. Record the error and
        # skip — falling through to slug-match/create would re-introduce the
        # duplicate this link model exists to prevent. The author must fix
        # the reference.
        ref = (
            f'event_id {event_id}' if event_id not in (None, '')
            else f'event_slug {event_slug!r}'
        )
        stats['errors'].append({
            'file': yaml_rel_path,
            'error': (
                f'workshop {workshop.slug}: {ref} not found — not linking, '
                f'not creating a duplicate. Fix the reference in workshop.yaml.'
            ),
        })
        return

    if reference_present:
        # Explicit link to a pre-existing event. Update displayable content
        # only — never the source-ownership fields (which would flip a
        # Studio event's origin and trip Event.save()'s invariant).
        existing_event = explicit_event
        create_kwargs = None
    else:
        # No-reference resolution order (issue #880): slug match -> title+date
        # heuristic -> create as a last resort.
        existing_event = Event.objects.filter(slug=workshop.slug).first()
        if existing_event is not None:
            # Step 2: slug match (existing behaviour, regression-safe).
            create_kwargs = None
        else:
            # Step 3: conservative title+date dedup heuristic. Link only when
            # EXACTLY ONE existing studio event matches; refuse to guess when
            # more than one does; fall through to create on zero matches.
            matched_count, heuristic_event = _resolve_heuristic_workshop_event(
                workshop, workshop_date,
            )
            if matched_count > 1:
                # Ambiguous: do NOT guess. Record an error asking the author
                # for an explicit event_id and create nothing — a wrong link
                # or a duplicate are both worse than refusing.
                stats['errors'].append({
                    'file': yaml_rel_path,
                    'error': (
                        f'workshop {workshop.slug}: {matched_count} existing '
                        f'events match title {workshop.title!r} on '
                        f'{workshop_date.isoformat()} — refusing to guess. '
                        f'Set an explicit event_id in workshop.yaml to link '
                        f'the right one.'
                    ),
                })
                return
            if heuristic_event is not None:
                # Exactly one match: link to it, content fields only.
                existing_event = heuristic_event
                create_kwargs = None
            else:
                # Step 4: no match on any step — auto-create the fallback
                # github-origin workshop event and log it so the operator can
                # promote/link it later (no-match policy, issue #865).
                start_dt = dt.datetime.combine(
                    workshop_date, dt.time.min, tzinfo=dt.timezone.utc,
                )
                create_kwargs = {
                    'slug': workshop.slug,
                    'start_datetime': start_dt,
                    'status': 'completed',
                    'published': True,
                }
                logger.warning(
                    'Workshop %s: no Studio event matched (title %r on %s) — '
                    'auto-creating a github-origin workshop event. Link it to '
                    'a Studio event with an explicit event_id to dedup.',
                    workshop.slug, workshop.title, workshop_date.isoformat(),
                )

    # When linking to a pre-existing STUDIO event, drop the source-ownership
    # fields so it keeps ``origin='studio'``/``source_repo=''`` — writing
    # ``origin='github'`` would both flip its ownership and trip
    # ``Event.save()``'s invariant. For a github-origin existing row (a
    # workshop event this sync minted on a previous run) the fields are
    # re-applied so ``source_commit``/``content_id`` stay current on re-sync.
    if existing_event is not None and existing_event.origin == 'studio':
        defaults = {
            key: value
            for key, value in content_defaults.items()
            if key not in _EVENT_SOURCE_OWNERSHIP_FIELDS
        }
    else:
        defaults = content_defaults

    result = _upsert_synced_event_content(
        lookup=lambda: existing_event,
        defaults=defaults,
        stats=stats,
        create_kwargs=create_kwargs,
        detail_slug=workshop.slug,
    )
    event = result.instance

    # Link the Workshop to the Event if not already linked (or if linked
    # to a stale event row). Use update() to avoid re-running Workshop.save()
    # and the render pipeline.
    if workshop.event_id != event.pk:
        Workshop.objects.filter(pk=workshop.pk).update(event=event)
        workshop.event_id = event.pk

    _attach_hosts_to_event(event, resolved_hosts)


def _cleanup_generated_empty_workshop_event(workshop, source, stats):
    """Unlink old sync-generated workshop Events when a workshop has no recording.

    Before issue #631, every workshop folder created a completed Event even
    when ``recording.url`` was empty. Keep this cleanup deliberately narrow:
    only GitHub-origin, workshop.yaml-derived, empty completed workshop events
    are considered safe. Studio-origin rows, upcoming/live rows, and rows with
    any recording URL stay untouched.
    """
    from content.models import Workshop
    from events.models import Event

    event = None
    if workshop.event_id:
        event = Event.objects.filter(pk=workshop.event_id).first()
    if event is None:
        event = Event.objects.filter(
            slug=workshop.slug,
            kind='workshop',
            origin='github',
            source_repo=source.repo_name,
        ).first()
    if event is None:
        return

    if not _is_safe_generated_empty_workshop_event(event, source):
        return

    if workshop.event_id == event.pk:
        Workshop.objects.filter(pk=workshop.pk, event=event).update(event=None)
        workshop.event_id = None

    if event.status != 'draft' or event.published or event.published_at:
        Event.objects.filter(pk=event.pk).update(
            status='draft',
            published=False,
            published_at=None,
        )
        stats['items_detail'].append({
            'title': event.title,
            'slug': event.slug,
            # Issue #673: include the id so the Studio sync history can
            # render the canonical ``/events/<id>/<slug>`` link.
            'id': event.pk,
            'action': 'deleted',
            'content_type': 'event',
        })
        stats['deleted'] += 1


def _is_safe_generated_empty_workshop_event(event, source):
    if event.origin != 'github':
        return False
    if event.source_repo != source.repo_name:
        return False
    if event.kind != 'workshop':
        return False
    if event.status != 'completed':
        return False
    if event.recording_url or event.recording_s3_url or event.recording_embed_url:
        return False
    if event.zoom_meeting_id or event.zoom_join_url or event.location:
        return False
    if not event.source_path or os.path.basename(event.source_path) != 'workshop.yaml':
        return False

    event_source_dir = os.path.dirname(event.source_path)
    expected_content_id = _derive_workshop_event_content_id(
        source.repo_name, event_source_dir,
    )
    return str(event.content_id) == expected_content_id


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
    cross_workshop_lookup=None, workshops_repo_name=None,
    workshop_url_key=None,
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
    from content.utils.includes import expand_content_includes
    from content.utils.md_links import (
        rewrite_cross_workshop_md_links,
        rewrite_workshop_md_links,
    )

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

            # Mark the file as seen up front so a downstream validation
            # failure (e.g. an ``access:`` override that violates the
            # landing invariant) does not cause the existing row to be
            # swept up by the stale-page cleanup at the end of the
            # function. The intent is "skip mutation, keep the row" —
            # cleaning it up would corrupt user-visible state on a
            # repository edit error.
            seen_paths.add(rel_path)

            # Issue #571: per-page ``access:`` override. Absent = NULL
            # column = inherit Workshop.pages_required_level. When the
            # override drops below the workshop landing gate the page
            # would be more accessible than the landing it lives under
            # — fail closed and skip the page (no row mutation) so the
            # invariant is preserved exactly like the page-level
            # validation in WorkshopPage.clean().
            page_access_raw = metadata.get('access')
            if page_access_raw is not None:
                page_required_level = _parse_access_value(
                    page_access_raw,
                    field_name='access',
                    rel_path=rel_path,
                )
                if page_required_level < workshop.landing_required_level:
                    stats['errors'].append({
                        'file': rel_path,
                        'error': (
                            f'Page access ({page_required_level}) must be '
                            f'>= workshop landing_required_level '
                            f'({workshop.landing_required_level}). Skipped.'
                        ),
                    })
                    continue
            else:
                page_required_level = None

            # content_id: explicit in frontmatter, or derive stable UUID.
            content_id = metadata.get('content_id')
            if not content_id:
                content_id = _derive_workshop_page_content_id(
                    repo_name, rel_path,
                )

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
            # Issue #526: pass cross_workshop_lookup so the intra-workshop
            # pass suppresses its "out-of-tree" warning for ``..``-prefixed
            # links — the cross-workshop pass below picks them up.
            body = rewrite_workshop_md_links(
                body,
                workshop_slug=workshop.slug,
                page_lookup=page_lookup,
                source_path=rel_path,
                sync_errors=stats.get('errors'),
                cross_workshop_lookup=cross_workshop_lookup,
                # Rewrite to the canonical slug-only URL shape.
                workshop_url_key=workshop_url_key or workshop.url_key,
            )
            # Issue #526: rewrite cross-workshop links AFTER the
            # intra-workshop pass so it picks up the ``..``-prefixed and
            # absolute-GitHub-URL links the previous pass intentionally
            # leaves untouched.
            if cross_workshop_lookup is not None and workshops_repo_name:
                body = rewrite_cross_workshop_md_links(
                    body,
                    cross_workshop_lookup=cross_workshop_lookup,
                    workshops_repo_name=workshops_repo_name,
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
                'required_level': page_required_level,
                'source_repo': repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': content_id,
            }

            # Look up by (workshop, slug) — that's the unique-constraint
            # key. Falling back to (workshop, source_path) misses when a
            # file is renamed but slug stays the same, then INSERT would
            # collide on the unique constraint instead of doing an update.
            page = find_synced_object((
                lambda: WorkshopPage.objects.filter(
                    workshop=workshop, slug=slug,
                ).first(),
                lambda: WorkshopPage.objects.filter(
                    workshop=workshop, source_path=rel_path,
                ).first(),
            ))

            page_result = upsert_synced_object(
                model=WorkshopPage,
                lookup=lambda: page,
                defaults=defaults,
                stats=stats,
                create_kwargs={'workshop': workshop},
                detail=lambda obj, action: {
                    'title': obj.title,
                    'slug': obj.slug,
                    'action': action,
                    'content_type': 'workshop_page',
                },
                identity_changed=lambda obj: (
                    obj.slug != slug
                    or obj.source_path != rel_path
                ),
                apply_identity=lambda obj: setattr(obj, 'slug', slug),
            )

            expanded_body_html = expand_content_includes(
                page_result.instance.body_html,
                repo_dir=repo_dir,
                base_dir=os.path.dirname(filepath),
                context={
                    'metadata': metadata,
                    'workshop': workshop,
                    'page': page_result.instance,
                },
            )
            if expanded_body_html != page_result.instance.body_html:
                page_result.instance.body_html = expanded_body_html
                WorkshopPage.objects.filter(pk=page_result.instance.pk).update(
                    body_html=expanded_body_html,
                )

            # Issue #595: warn (don't block) when the rendered page HTML
            # still links to a retired URL prefix (e.g. /event-recordings/).
            # Only check on a write — unchanged rows already passed this
            # gate during their own sync.
            if page_result.changed:
                from content.utils.legacy_urls import detect_legacy_urls
                detect_legacy_urls(
                    page_result.instance.body_html, rel_path, stats['errors'],
                )

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning('Error syncing workshop page %s: %s', rel_path, e)

    # Hard-delete pages whose source files disappeared.
    stale = WorkshopPage.objects.filter(workshop=workshop).exclude(
        source_path__in=seen_paths,
    )
    cleanup_stale_synced_objects(
        stale,
        stats=stats,
        detail=lambda page, action: {
            'title': page.title,
            'slug': page.slug,
            'action': action,
            'content_type': 'workshop_page',
        },
        cleanup=lambda pages: WorkshopPage.objects.filter(
            pk__in=[page.pk for page in pages],
        ).delete(),
    )
