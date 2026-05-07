"""Instructor sync dispatcher and attachment helpers."""

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

def _dispatch_instructors(source, repo_dir, file_list, commit_sha, stats):
    """Walker dispatch handler: process instructor YAML files.

    ``file_list`` is the set of repo-relative ``.yaml``/``.yml`` paths
    under any ``instructors/`` subtree, classified by
    ``_classify_repo_files``.

    Edge cases:

    - Two yaml files in the same sync sharing the same ``id``: log and
      skip the second; the sync continues and the SyncLog ends up
      ``status='partial'`` via the existing error-counting logic.
    - ``id`` not matching ``[a-z0-9-]+``: rejected with a clear error.
    - Yaml deleted from the repo between syncs: the corresponding
      Instructor row is soft-deleted (``status='draft'``). Through-table
      relationships on Course/Workshop/Event are preserved (FK uses
      ``on_delete=PROTECT``; we only flip the status field).
    """
    from content.models import Instructor

    seen_ids = set()
    failed_ids = set()

    for rel_path in sorted(file_list):
        filepath = os.path.join(repo_dir, rel_path)
        data = None

        try:
            data = _parse_yaml_file(filepath)

            # Required: id, name. ``_validate_frontmatter`` raises a
            # clear ValueError that the outer except records into
            # stats['errors'] without aborting the sync.
            _validate_frontmatter(data, 'instructor', rel_path)

            instructor_id = str(data.get('id', '')).strip()
            if not INSTRUCTOR_ID_RE.match(instructor_id):
                msg = (
                    f"Invalid instructor id '{instructor_id}' in {rel_path}: "
                    f"must match [a-z0-9-]+ (lowercase letters, digits, "
                    f"hyphens only)."
                )
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                failed_ids.add(instructor_id)
                continue

            # Duplicate-id check within this sync.
            if instructor_id in seen_ids:
                msg = (
                    f"Duplicate instructor id '{instructor_id}' in "
                    f"{rel_path}: another file in this sync already "
                    f"defined it. Skipped."
                )
                logger.error(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            seen_ids.add(instructor_id)

            name = data.get('name', '').strip() or instructor_id
            bio = data.get('bio', '') or ''
            photo_url = data.get('photo_url', '') or ''

            # Validate links shape: list of {label, url} dicts.
            raw_links = data.get('links', []) or []
            links = []
            if isinstance(raw_links, list):
                for entry in raw_links:
                    if isinstance(entry, dict) and 'url' in entry:
                        links.append({
                            'label': str(entry.get('label', '') or ''),
                            'url': str(entry.get('url', '') or ''),
                        })
            else:
                logger.warning(
                    'links: in %s must be a list, got %s. Skipping links field.',
                    rel_path, type(raw_links).__name__,
                )

            defaults = {
                'name': name,
                'bio': bio,
                'photo_url': photo_url,
                'links': links,
                'status': 'published',
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Lookup by (instructor_id, source_repo) first so two repos
            # could in principle each manage a different instructor with
            # the same slug (today only one source defines instructors,
            # but the guard is cheap). Fall back to instructor_id only
            # for backfill rows whose source_repo is NULL.
            inst = Instructor.objects.filter(
                instructor_id=instructor_id,
                source_repo=source.repo_name,
            ).first()
            if inst is None:
                inst = Instructor.objects.filter(
                    instructor_id=instructor_id,
                ).first()

            if inst is None:
                inst = Instructor(instructor_id=instructor_id, **defaults)
                inst.save()
                stats['created'] += 1
                stats['items_detail'].append({
                    'title': name,
                    'slug': instructor_id,
                    'action': 'created',
                    'content_type': 'instructor',
                })
            else:
                if _defaults_differ(inst, defaults):
                    for k, v in defaults.items():
                        setattr(inst, k, v)
                    inst.save()
                    stats['updated'] += 1
                    stats['items_detail'].append({
                        'title': name,
                        'slug': instructor_id,
                        'action': 'updated',
                        'content_type': 'instructor',
                    })
                else:
                    stats['unchanged'] += 1

        except Exception as e:
            try:
                failed_id = str(data.get('id', '')) if data else ''
            except Exception:
                failed_id = ''
            failed_ids.add(failed_id)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete: yaml deleted from the repo -> status='draft'. We
    # restrict the cleanup to rows that came from THIS source so that
    # backfill rows (source_repo IS NULL) and rows from other sources
    # are never touched.
    stale = Instructor.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(instructor_id__in=seen_ids).exclude(instructor_id__in=failed_ids)
    for s in stale:
        stats['items_detail'].append({
            'title': s.name,
            'slug': s.instructor_id,
            'action': 'deleted',
            'content_type': 'instructor',
        })
    deleted_count = stale.count()
    stale.update(status='draft')
    stats['deleted'] += deleted_count


def _resolve_instructors_for_yaml(data, rel_path, stats):
    """Resolve ``instructors:`` list-of-ids from yaml into Instructor rows.

    Used by ``_sync_single_course``, ``_sync_single_workshop``, and
    ``_sync_events`` to attach the M2M after the parent row is saved.

    Returns a list of ``Instructor`` rows in the order the ids appear in
    yaml. Unknown ids are logged and skipped (sync continues). A
    malformed ``instructors:`` field (not a list) is logged and treated
    as if it were absent. ``None`` is returned when the yaml does not
    define the field at all (or defines it as an empty list) — callers
    use this to distinguish "leave existing relationships untouched"
    from "no resolved instructors".
    """
    from content.models import Instructor

    if 'instructors' not in data:
        return None
    raw = data.get('instructors')
    if raw is None:
        return None
    if not isinstance(raw, list):
        msg = (
            f'instructors: in {rel_path} must be a list of strings, '
            f'got {type(raw).__name__}. Ignoring field.'
        )
        logger.warning(msg)
        stats['errors'].append({'file': rel_path, 'error': msg})
        return None
    if not raw:
        # Empty list — treat the same as missing (issue spec: "Empty
        # `instructors:` list ... is treated the same as missing —
        # leaves M2M and legacy fields untouched"). Return None so
        # callers don't replace anything.
        return None

    ids = [str(x).strip() for x in raw if str(x).strip()]
    if not ids:
        return None

    found = {
        i.instructor_id: i
        for i in Instructor.objects.filter(instructor_id__in=ids)
    }
    resolved = []
    for instructor_id in ids:
        if instructor_id in found:
            resolved.append(found[instructor_id])
        else:
            # Unknown id is a soft warning, not a hard error: the spec
            # explicitly says "sync continues without raising". Logging
            # only (not appending to stats['errors']) keeps the SyncLog
            # status='success' rather than tipping it to 'partial' for
            # what is usually a transient skew between the two repos.
            logger.warning(
                "Unknown instructor id '%s' referenced from %s. Skipped.",
                instructor_id, rel_path,
            )

    return resolved


def _attach_instructors_to_course(course, resolved, stats):
    """Replace course.instructors M2M from the resolved instructor list.

    Idempotent: clears the through-table only when the resolved id list
    differs from what's currently attached, so re-running sync is a
    no-op when nothing changed. Issue #423 removed the legacy
    ``instructor_name`` / ``instructor_bio`` mirror writes — the M2M
    relationship is the canonical source of truth.
    """
    from content.models import CourseInstructor

    if resolved is None:
        return  # Field missing/empty -> leave M2M untouched.
    if not resolved:
        return  # All ids unknown -> leave M2M untouched.

    new_ids = [i.pk for i in resolved]
    current_qs = CourseInstructor.objects.filter(course=course).order_by('position')
    current_ids = list(current_qs.values_list('instructor_id', flat=True))

    if current_ids != new_ids:
        current_qs.delete()
        CourseInstructor.objects.bulk_create([
            CourseInstructor(
                course=course, instructor=inst, position=i,
            )
            for i, inst in enumerate(resolved)
        ])


def _attach_instructors_to_workshop(workshop, resolved, stats):
    """Replace workshop.instructors M2M from the resolved instructor list.

    Issue #423 removed the legacy ``instructor_name`` mirror write.
    """
    from content.models import WorkshopInstructor

    if resolved is None:
        return
    if not resolved:
        return

    new_ids = [i.pk for i in resolved]
    current_qs = WorkshopInstructor.objects.filter(
        workshop=workshop,
    ).order_by('position')
    current_ids = list(current_qs.values_list('instructor_id', flat=True))

    if current_ids != new_ids:
        current_qs.delete()
        WorkshopInstructor.objects.bulk_create([
            WorkshopInstructor(
                workshop=workshop, instructor=inst, position=i,
            )
            for i, inst in enumerate(resolved)
        ])


def _attach_instructors_to_event(event, resolved, stats):
    """Replace event.instructors M2M from the resolved instructor list.

    Issue #423 removed the legacy ``speaker_name`` / ``speaker_bio``
    mirror writes.
    """
    from events.models import EventInstructor

    if resolved is None:
        return
    if not resolved:
        return

    new_ids = [i.pk for i in resolved]
    current_qs = EventInstructor.objects.filter(event=event).order_by('position')
    current_ids = list(current_qs.values_list('instructor_id', flat=True))

    if current_ids != new_ids:
        current_qs.delete()
        EventInstructor.objects.bulk_create([
            EventInstructor(
                event=event, instructor=inst, position=i,
            )
            for i, inst in enumerate(resolved)
        ])


