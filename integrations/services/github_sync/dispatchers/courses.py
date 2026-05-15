"""Course sync dispatcher."""

# ruff: noqa

import os
import re
import uuid

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

from integrations.services.github_sync.dispatchers.instructors import _attach_instructors_to_course, _resolve_instructors_for_yaml

# Issue #465: maps the operator-facing string keys in YAML (the verb-aligned
# ``access:`` / ``default_unit_access:`` vocabulary) to the integer levels
# stored in ``Course.default_unit_required_level`` and ``Unit.required_level``.
# Raw integers are accepted too (see _parse_access_value) so existing repos
# can pass numbers if they prefer the old shape.
_ACCESS_NAME_TO_LEVEL = {
    'open': 0,
    'registered': 5,
    'basic': 10,
    'main': 20,
    'premium': 30,
}
_VALID_ACCESS_LEVELS = frozenset(_ACCESS_NAME_TO_LEVEL.values())


def _parse_access_value(raw, *, field_name, rel_path):
    """Resolve a YAML ``access:`` / ``default_unit_access:`` value to an int.

    Accepts named values (``open``, ``registered``, ``basic``, ``main``,
    ``premium``, case-insensitive) and the matching raw integers
    (0, 5, 10, 20, 30). Returns the integer level or raises
    :class:`GitHubSyncError` with a message that identifies the file and
    the offending value so the SyncLog entry tells the operator exactly
    which YAML to fix.

    Booleans are rejected up front because YAML parses ``true`` /
    ``false`` as ``bool`` (a subclass of ``int``); silently letting a
    boolean through would map ``true`` to level 1 — gibberish.

    ``None`` is the responsibility of the caller (an absent key keeps
    the database column NULL); this helper assumes a real value.
    """
    if isinstance(raw, bool):
        raise GitHubSyncError(
            f'Invalid {field_name} in {rel_path}: {raw!r} '
            f'(expected one of {sorted(_ACCESS_NAME_TO_LEVEL)} or '
            f'{sorted(_VALID_ACCESS_LEVELS)})'
        )
    if isinstance(raw, int):
        if raw in _VALID_ACCESS_LEVELS:
            return raw
        raise GitHubSyncError(
            f'Invalid {field_name} in {rel_path}: {raw!r} '
            f'(expected one of {sorted(_ACCESS_NAME_TO_LEVEL)} or '
            f'{sorted(_VALID_ACCESS_LEVELS)})'
        )
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in _ACCESS_NAME_TO_LEVEL:
            return _ACCESS_NAME_TO_LEVEL[key]
        raise GitHubSyncError(
            f'Invalid {field_name} in {rel_path}: {raw!r} '
            f'(expected one of {sorted(_ACCESS_NAME_TO_LEVEL)} or '
            f'{sorted(_VALID_ACCESS_LEVELS)})'
        )
    raise GitHubSyncError(
        f'Invalid {field_name} in {rel_path}: {raw!r} '
        f'(expected one of {sorted(_ACCESS_NAME_TO_LEVEL)} or '
        f'{sorted(_VALID_ACCESS_LEVELS)})'
    )


def _dispatch_courses(source, repo_dir, course_dirs, commit_sha, stats,
                      known_images=None):
    """Walker dispatch handler: process course directories.

    Iterates ``course_dirs`` (absolute paths to dirs containing
    ``course.yaml``) and upserts a ``Course`` row plus its Modules and
    Units for each. Performs the stale-Course sweep at the end:

    - When a stale row's ``content_id`` matches an active published row
      (anywhere in the DB, not just this repo), the stale row is treated
      as an orphan from a rename / cross-repo move: enrollments,
      individual access grants, cohorts, and per-unit progress are
      reattached to the published row by ``Unit.content_id``, then the
      orphan is deleted (issue #366).
    - Otherwise the row is soft-deleted to ``status='draft'`` so any
      historical FKs are preserved (legacy behavior, unchanged).
    """
    from content.models import Course

    seen_course_slugs = set()
    failed_course_slugs = set()

    for course_dir in course_dirs:
        _sync_single_course(
            course_dir, repo_dir, source, commit_sha, stats,
            seen_course_slugs, failed_course_slugs,
            known_images=known_images,
        )

    _cleanup_stale_courses_for_source(
        source, seen_course_slugs, failed_course_slugs, stats,
    )


def _cleanup_stale_courses_for_source(
    source, seen_course_slugs, failed_course_slugs, stats,
):
    from content.models import Course

    stale_courses = list(Course.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_course_slugs).exclude(slug__in=failed_course_slugs))
    cleanup_stale_synced_objects(
        stale_courses,
        stats=stats,
        detail=lambda course, action: {
            'title': course.title,
            'slug': course.slug,
            'action': action,
            'content_type': 'course',
            'course_id': course.pk,
            'course_slug': course.slug,
        },
        cleanup=_apply_stale_course_cleanup,
    )


def _apply_stale_course_cleanup(courses):
    from content.models import Course

    for course in courses:
        sibling = None
        if course.content_id is not None:
            sibling = Course.objects.filter(
                content_id=course.content_id,
                status='published',
            ).exclude(pk=course.pk).first()

        if sibling is not None:
            _reattach_course_fks(course, sibling)
            course.delete()
        else:
            course.status = 'draft'
            course.save(update_fields=['status', 'updated_at'])


def _reattach_course_fks(orphan_course, target_course):
    """Move enrollment / progress / cohort FKs off ``orphan_course``.

    Issue #366: when a course is renamed (slug changes but ``content_id``
    is stable), the sync may end up with two ``Course`` rows that share
    a ``content_id``. Any ``Enrollment``, ``CourseAccess``, or
    ``Cohort`` rows attached to the orphan need to follow the live
    course; ``UserCourseProgress`` rows are repointed unit-by-unit by
    matching ``Unit.content_id`` so per-lesson completion survives.

    Units in the orphan that have no ``content_id`` match in the target
    are left attached to the orphan: deleting them would silently lose
    a user's completion record. The caller decides whether to delete
    the orphan course (cascading those leftovers) or keep it around.

    The only side-effect is FK rewrites + a WARNING log per orphan unit
    that couldn't be matched. Idempotent: running with the same orphan
    twice is a no-op (no rows left to move).
    """
    from content.models import (
        CourseAccess,
        Enrollment,
        Unit,
        UserCourseProgress,
    )
    from content.models.cohort import Cohort

    Enrollment.objects.filter(course=orphan_course).update(
        course=target_course,
    )
    CourseAccess.objects.filter(course=orphan_course).update(
        course=target_course,
    )
    Cohort.objects.filter(course=orphan_course).update(course=target_course)

    target_unit_by_content_id = {
        unit.content_id: unit
        for unit in Unit.objects.filter(
            module__course=target_course,
        ).exclude(content_id__isnull=True)
    }

    orphan_units = Unit.objects.filter(
        module__course=orphan_course,
    ).select_related('module')

    for unit in orphan_units:
        target = (
            target_unit_by_content_id.get(unit.content_id)
            if unit.content_id is not None else None
        )
        if target is None:
            # No content_id match — refusing to silently lose the
            # progress data. Leave UserCourseProgress on this orphan
            # unit; if the orphan course is deleted, the cascade will
            # remove the progress (which is the correct outcome: the
            # unit no longer exists anywhere).
            if UserCourseProgress.objects.filter(unit=unit).exists():
                logger.warning(
                    'Course %s (%s): orphan unit %s (content_id=%s) has '
                    'no match in target course %s; leaving '
                    'UserCourseProgress rows attached to the orphan.',
                    orphan_course.slug, orphan_course.pk, unit.slug,
                    unit.content_id, target_course.slug,
                )
            continue
        if target.pk == unit.pk:
            # Unit was already moved (orphan_course == target_course
            # via stale module/unit reuse); nothing to do.
            continue
        UserCourseProgress.objects.filter(unit=unit).update(unit=target)


def _sync_single_course(
    course_dir, repo_dir, source, commit_sha, stats,
    seen_course_slugs, failed_course_slugs, known_images=None,
):
    """Parse one course.yaml + module dirs into a Course with Modules/Units.

    Used by both multi-course mode (each child dir is its own course) and
    single-course mode (the resolved content_dir is the course root).

    Respects ``ignore:`` in ``course.yaml`` (a list of globs relative to the
    course root) — matched files are skipped everywhere in the course. If no
    ``description:`` is set in ``course.yaml`` and ``README.md`` exists at the
    course root and is not ignored, the README body becomes the course
    description.
    """
    from content.models import Course

    course_yaml_path = os.path.join(course_dir, 'course.yaml')
    course_data = None
    try:
        course_data = _parse_yaml_file(course_yaml_path)
        slug = course_data.get('slug', os.path.basename(course_dir.rstrip(os.sep)))
        rel_path = os.path.relpath(course_dir, repo_dir)

        # Edge Case 7: Frontmatter validation
        _validate_frontmatter(course_data, 'course', rel_path)

        # Require content_id in frontmatter
        course_content_id = course_data.get('content_id')
        if not course_content_id:
            msg = f'Skipping {rel_path}: missing content_id in frontmatter'
            logger.warning(msg)
            stats['errors'].append({'file': rel_path, 'error': msg})
            return

        if _course_slug_collision_blocked(
            Course, slug, course_content_id, source.repo_name, rel_path, stats,
        ):
            failed_course_slugs.add(slug)
            return

        seen_course_slugs.add(slug)

        course_ignore_patterns = _course_ignore_patterns(course_data)
        course_defaults = _build_course_defaults(
            course_data, slug, course_content_id, course_dir, rel_path,
            source, commit_sha, course_ignore_patterns,
        )
        candidates, course = _resolve_course_identity(
            Course, course_content_id, slug, source.repo_name,
        )
        result = _upsert_course_record(
            Course, course, course_defaults, slug, rel_path, source.repo_name,
            stats,
        )
        course = result.instance

        if not result.created:
            _delete_duplicate_course_siblings(candidates, course)

        _sync_course_children(
            course, course_data, course_dir, repo_dir, rel_path, source,
            commit_sha, stats, known_images, course_ignore_patterns,
        )

    except Exception as e:
        try:
            failed_slug = (course_data or {}).get(
                'slug', os.path.basename(course_dir.rstrip(os.sep)),
            )
        except Exception:
            failed_slug = os.path.basename(course_dir.rstrip(os.sep))
        failed_course_slugs.add(failed_slug)
        stats['errors'].append({
            'file': os.path.relpath(course_yaml_path, repo_dir),
            'error': str(e),
        })
        logger.warning(
            'Error syncing course %s: %s',
            os.path.basename(course_dir.rstrip(os.sep)), e,
        )


def _course_slug_collision_blocked(
    Course, slug, course_content_id, repo_name, rel_path, stats,
):
    existing_with_slug = Course.objects.filter(slug=slug).exclude(
        source_repo=repo_name,
    ).first()
    existing_cid = (
        str(existing_with_slug.content_id)
        if existing_with_slug is not None
        and existing_with_slug.content_id is not None
        else None
    )
    if existing_with_slug is None or existing_cid == str(course_content_id):
        return False

    other_source = existing_with_slug.source_repo or 'studio'
    logger.warning(
        "Slug collision: '%s' already exists from source '%s' "
        "(source_repo=%s). Skipped %s.",
        slug, other_source, existing_with_slug.source_repo, rel_path,
    )
    stats['errors'].append({
        'file': rel_path,
        'error': (
            f"Slug collision: '{slug}' already exists from a "
            f"different source. Skipped."
        ),
    })
    return True


def _course_ignore_patterns(course_data):
    raw_ignore = course_data.get('ignore', []) or []
    return [str(p) for p in raw_ignore]


def _build_course_defaults(
    course_data, slug, course_content_id, course_dir, rel_path, source,
    commit_sha, course_ignore_patterns,
):
    description = _resolve_course_description(
        course_data, course_dir, course_ignore_patterns,
    )
    default_unit_required_level = _resolve_default_unit_required_level(
        course_data, rel_path,
    )
    return {
        'title': course_data.get('title', slug),
        'description': description,
        'cover_image_url': rewrite_cover_image_url(
            course_data.get('cover_image', '')
            or course_data.get('cover_image_url', ''),
            source,
            os.path.join(rel_path, 'course.yaml'),
        ),
        'required_level': course_data.get('required_level', 0),
        'default_unit_required_level': default_unit_required_level,
        'discussion_url': course_data.get('discussion_url', ''),
        'tags': course_data.get('tags', []),
        'testimonials': course_data.get('testimonials', []),
        'status': 'published',
        'source_repo': source.repo_name,
        'source_path': rel_path,
        'source_commit': commit_sha,
        'content_id': course_content_id,
    }


def _resolve_course_description(course_data, course_dir, course_ignore_patterns):
    description = course_data.get('description', '') or ''
    if description:
        return description

    readme_path = os.path.join(course_dir, 'README.md')
    if (
        not os.path.isfile(readme_path)
        or _matches_ignore_patterns('README.md', course_ignore_patterns)
    ):
        return ''
    try:
        _, readme_body = _parse_markdown_file(readme_path)
    except (ValueError, OSError) as e:
        # ``ValueError`` covers frontmatter parse failures
        # (``_parse_markdown_file`` wraps ``yaml.YAMLError`` as
        # ``ValueError``); ``OSError`` covers missing/unreadable files.
        # Other exception types propagate.
        logger.warning('Failed to read course README at %s: %s', readme_path, e)
        return ''
    if readme_body and readme_body.strip():
        return readme_body
    return ''


def _resolve_default_unit_required_level(course_data, rel_path):
    default_unit_access_raw = course_data.get('default_unit_access')
    if default_unit_access_raw is None:
        return None
    return _parse_access_value(
        default_unit_access_raw,
        field_name='default_unit_access',
        rel_path=os.path.join(rel_path, 'course.yaml'),
    )


def _resolve_course_identity(Course, course_content_id, slug, repo_name):
    candidates = list(Course.objects.filter(content_id=course_content_id))
    course_by_content_id = None
    if candidates:
        candidates.sort(key=lambda c: (
            0 if c.status == 'published' else 1,
            0 if c.source_repo == repo_name else 1,
            c.pk,
        ))
        course_by_content_id = candidates[0]

    course = find_synced_object((
        lambda: course_by_content_id,
        lambda: Course.objects.filter(
            slug=slug,
            source_repo=repo_name,
        ).first(),
    ))
    return candidates, course


def _upsert_course_record(
    Course, course, course_defaults, slug, rel_path, repo_name, stats,
):
    return upsert_synced_object(
        model=Course,
        lookup=lambda: course,
        defaults=course_defaults,
        stats=stats,
        create_kwargs={'slug': slug},
        identity_changed=lambda obj: (
            obj.slug != slug
            or obj.source_path != rel_path
            or obj.source_repo != repo_name
            or obj.status != 'published'
        ),
        apply_identity=lambda obj: setattr(obj, 'slug', slug),
        detail=lambda obj, action: {
            'title': course_defaults.get('title', slug),
            'slug': slug,
            'action': action,
            'content_type': 'course',
            'course_id': obj.pk,
            'course_slug': obj.slug,
        },
    )


def _delete_duplicate_course_siblings(candidates, course):
    for sibling in candidates:
        if sibling.pk == course.pk:
            continue
        _reattach_course_fks(sibling, course)
        sibling.delete()


def _sync_course_children(
    course, course_data, course_dir, repo_dir, rel_path, source, commit_sha,
    stats, known_images, course_ignore_patterns,
):
    resolved_instructors = _resolve_instructors_for_yaml(
        course_data, rel_path, stats,
    )
    _attach_instructors_to_course(course, resolved_instructors, stats)
    _sync_course_modules(
        course, course_dir, repo_dir, source.repo_name,
        commit_sha, stats, known_images=known_images,
        course_ignore_patterns=course_ignore_patterns,
    )


def _build_course_unit_lookup(course_dir, course_ignore_patterns=None, stats=None):
    """Build a ``{module_slug: {filename: unit_slug}}`` map for a course tree.

    Used by the markdown link rewriter (issue #226) so we can resolve sibling
    and cross-module ``.md`` links without doing a database round-trip per
    link. The slug derivation here mirrors what :func:`_sync_module_units`
    writes to ``Module.slug`` / ``Unit.slug`` so the rewriter produces URLs
    that actually resolve.

    Modules without a ``module.yaml`` are skipped (mirroring
    :func:`_sync_course_modules`). Files starting with ``.`` are ignored.
    Frontmatter ``slug`` overrides for both modules and units are honoured.

    To stay in lock-step with :func:`_sync_module_units` (issue #233),
    files matched by course-level or module-level ``ignore:`` globs are
    excluded, and non-README files missing ``content_id`` in frontmatter
    are excluded too. Without these checks the rewriter would emit
    "working-looking" URLs to units that were never persisted, producing
    silent 404s instead of the standard unresolvable-link warning.

    Args:
        course_dir: Path to the course root directory on disk.
        course_ignore_patterns: List of glob patterns from the course-level
            ``ignore:`` key, relative to ``course_dir``. Same shape as the
            value passed to :func:`_sync_course_modules`.
        stats: Optional sync stats dict. When provided, parse failures for
            ``module.yaml`` or unit ``.md`` files are appended to
            ``stats['errors']`` so they surface in the SyncLog instead of
            being silently swallowed (issue #286). When ``None``, parse
            failures are only logged.
    """
    lookup = {}
    if not os.path.isdir(course_dir):
        return lookup

    course_ignore_patterns = course_ignore_patterns or []

    for entry in sorted(os.scandir(course_dir), key=lambda e: e.name):
        if (
            not entry.is_dir()
            or entry.name.startswith('.')
            or entry.name == 'images'
        ):
            continue

        # Mirror _sync_course_modules: a module dir matched by a course-level
        # ignore glob (e.g. ``docs/**``) is skipped entirely — its files
        # never become Units, so they must not appear in the lookup.
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue

        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not os.path.exists(module_yaml_path):
            continue

        # Best-effort: skip modules whose YAML can't be parsed. We don't want
        # link rewriting to ever fail the sync, so a parse error here just
        # means those modules' units can't be link targets. Surface the
        # error to ``stats['errors']`` when available so staff see it in the
        # SyncLog instead of silently losing the module (issue #286).
        # ``_parse_yaml_file`` now wraps yaml errors as ``ValueError`` with
        # a ``Failed to parse module.yaml: ...`` prefix, so the message we
        # record matches the documented format without a separate prefix.
        try:
            module_data = _parse_yaml_file(module_yaml_path) or {}
        except ValueError as exc:
            module_data = {}
            rel_module_yaml = os.path.relpath(module_yaml_path, course_dir)
            logger.warning(
                'Failed to parse %s while building course unit lookup: %s',
                module_yaml_path, exc,
            )
            if stats is not None:
                stats['errors'].append({
                    'file': rel_module_yaml,
                    'error': str(exc),
                })
        module_slug = module_data.get('slug') or derive_slug(entry.name)

        # Module-level ignore patterns are relative to the module dir,
        # course-level patterns are relative to the course dir — same split
        # _sync_module_units uses.
        raw_module_ignore = module_data.get('ignore', []) or []
        module_ignore_patterns = [str(p) for p in raw_module_ignore]

        files = {}
        for filename in os.listdir(entry.path):
            if (
                not filename.lower().endswith('.md')
                or filename.startswith('.')
            ):
                continue
            filepath = os.path.join(entry.path, filename)
            if not os.path.isfile(filepath):
                continue

            # Same _is_ignored check _sync_module_units uses: a file matched
            # by either glob list is skipped from sync, so it must also be
            # skipped from the lookup.
            rel_to_course = os.path.relpath(filepath, course_dir)
            if _matches_ignore_patterns(
                rel_to_course, course_ignore_patterns,
            ):
                continue
            if _matches_ignore_patterns(filename, module_ignore_patterns):
                continue

            try:
                metadata, _ = _parse_markdown_file(filepath)
            except ValueError as exc:
                # _parse_markdown_file now raises ValueError with a
                # ``Failed to parse frontmatter in <filename>: ...`` prefix
                # when frontmatter YAML fails (issue #286).
                metadata = {}
                rel_md = os.path.relpath(filepath, course_dir)
                logger.warning(
                    'Failed to parse frontmatter in %s while building '
                    'course unit lookup: %s', filepath, exc,
                )
                if stats is not None:
                    stats['errors'].append({
                        'file': rel_md,
                        'error': str(exc),
                    })

            if filename.lower() == 'readme.md':
                # README is the module overview, not a unit (issue #222).
                # We still register it under a sentinel slug so the link
                # rewriter can spot README.md targets and emit module-overview
                # URLs (handled in content/utils/md_links.py). README has no
                # content_id requirement (the sync derives one).
                unit_slug = '__module_overview__'
            else:
                # _sync_module_units skips non-README files missing
                # content_id (logs a warning, no Unit created). Mirror that
                # here so the rewriter doesn't emit URLs for ghost units.
                if not metadata.get('content_id'):
                    continue
                # Key-absent default to match _sync_module_units exactly:
                # an explicit empty ``slug:`` in YAML yields ``''`` rather
                # than falling back to the filename-derived slug. In
                # practice authors never write ``slug:`` empty.
                unit_slug = metadata.get('slug', derive_slug(filename))
            files[filename] = unit_slug

        lookup[module_slug] = files

    return lookup


def _build_workshop_page_lookup(
    workshop_dir, workshop_slug, workshop_title=None, copy_file=None,
):
    """Build a ``{filename: {'slug', 'title', 'url'}}`` map for a workshop folder.

    Used by :func:`rewrite_workshop_md_links` (issue #301) so we can resolve
    intra-workshop sibling ``.md`` links to platform URLs and substitute
    page titles when the link's visible text is the bare filename.

    Slug derivation mirrors :func:`_sync_workshop_pages`: frontmatter ``slug``
    overrides, otherwise :func:`derive_slug` strips the numeric prefix and
    ``.md`` extension. Files missing a frontmatter ``title`` are skipped from
    the lookup — they won't sync as pages either, so a link to them stays
    unresolved and surfaces as a broken-link warning the same way courses do.
    ``README.md`` and dotfiles are excluded from the tutorial-page list
    (workshops don't surface a README page) but ``README.md`` is added as a
    virtual entry pointing at the workshop landing URL when the file exists,
    so ``[README.md](README.md)`` links resolve cleanly (issue #304).

    Args:
        workshop_dir: Absolute path to the workshop folder.
        workshop_slug: ``Workshop.slug`` for URL construction.
        workshop_title: ``Workshop.title``, used for the title-substitution
            rule on the virtual ``README.md`` (and ``copy_file``) entries.
            Optional for backwards compat; when omitted the virtual entries
            are skipped (older callers fall back to plain tutorial-page
            lookups).
        copy_file: Optional resolved ``copy_file`` value from
            ``workshop.yaml``. When set to a non-README ``.md`` filename,
            adds an additional virtual entry mapping that filename to the
            workshop landing URL (so a link like
            ``[01-intro.md](01-intro.md)`` rewrites to ``/workshops/<slug>``
            instead of the tutorial URL).
    """
    lookup = {}
    if not os.path.isdir(workshop_dir):
        return lookup

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

        # Best-effort: a parse error here just means we can't resolve links
        # to this file. We don't want link rewriting to ever fail the sync.
        try:
            metadata, _ = _parse_markdown_file(filepath)
        except (ValueError, OSError):
            # ``ValueError`` covers frontmatter parse failures;
            # ``OSError`` covers IO. Anything else is a real bug and
            # should propagate to the outer per-file handler.
            continue

        title = metadata.get('title')
        if not title:
            # Page won't sync (validation requires title), so it can't be a
            # link target. Skip the lookup so the rewriter emits the standard
            # "no page found" warning if anyone links to it.
            continue

        slug = metadata.get('slug') or derive_slug(filename)
        url = f'/workshops/{workshop_slug}/tutorial/{slug}'
        lookup[filename] = {
            'slug': slug,
            'title': title,
            'url': url,
        }

    # Virtual entries (issue #304): point ``[README.md](README.md)`` and
    # any explicit ``copy_file`` reference at the workshop landing URL
    # rather than emitting an unresolvable-link warning. The title-swap
    # rule from #301 surfaces the workshop title as the visible link text
    # when the link label equals the bare filename.
    if workshop_title:
        landing_url = f'/workshops/{workshop_slug}'
        readme_path = os.path.join(workshop_dir, 'README.md')
        if os.path.isfile(readme_path):
            lookup['README.md'] = {
                'slug': '',
                'title': workshop_title,
                'url': landing_url,
            }
        if copy_file:
            # Only register the copy_file virtual entry when it's a plain
            # filename (no path components), it's a .md file, the file
            # exists, and it isn't already README.md (which we already
            # handled above). Validation errors are reported by
            # _resolve_workshop_landing_copy; here we just want to be
            # defensive so a malformed copy_file never produces a misleading
            # link rewrite.
            if (
                isinstance(copy_file, str)
                and copy_file
                and '/' not in copy_file
                and '..' not in copy_file
                and copy_file.lower().endswith('.md')
                and copy_file.upper() != 'README.MD'
            ):
                copy_path = os.path.join(workshop_dir, copy_file)
                if os.path.isfile(copy_path):
                    lookup[copy_file] = {
                        'slug': '',
                        'title': workshop_title,
                        'url': landing_url,
                    }

    return lookup


def _resolve_workshop_landing_copy(
    workshop_dir, data, rel_path, page_lookup, workshop_slug, repo_name,
    sync_errors, cross_workshop_lookup=None, workshops_repo_name=None,
    source_workshop_folder=None,
):
    """Resolve the markdown body for a workshop's landing description.

    Resolution order (issue #304):

    1. ``copy_file:`` set in yaml -> read that file (validate first).
    2. Unset ``copy_file`` AND ``README.md`` exists -> read ``README.md``.
    3. Neither file resolves AND yaml has ``description:`` -> use it as-is.
    4. None of the above -> empty string. No error.

    When a file source resolves alongside a yaml ``description``, the file
    wins; an info-level note is logged so authors notice the redundant yaml
    field.

    The returned body has been frontmatter-stripped, leading-H1-stripped,
    image-URL-rewritten, and intra-workshop-link-rewritten — ready to be
    assigned to ``Workshop.description``. ``Workshop.save()`` re-renders
    ``description_html`` exactly once via ``render_markdown``.

    Errors with explicit ``copy_file`` settings (missing file, non-md,
    path traversal/subdir) are appended to ``sync_errors`` and the function
    returns ``''``. The workshop sync still proceeds — copy errors never
    skip the workshop row.

    The file-read + frontmatter-strip + leading-H1-strip phase is delegated
    to the content-type-agnostic helper
    :func:`content.utils.copy_file.resolve_copy_file_content` (issue #307).
    Workshop-specific concerns (image-URL rewriting, intra-workshop link
    rewriting, the "yaml description shadowed by file" info note) stay
    here.

    Args:
        workshop_dir: Absolute path to the workshop folder.
        data: Parsed ``workshop.yaml`` dict.
        rel_path: Repo-relative path of the workshop folder (used for
            error messages and image URL rewriting).
        page_lookup: Pre-built ``{filename: {...}}`` map; passed to the
            link rewriter.
        workshop_slug: ``Workshop.slug`` for the link rewriter.
        repo_name: Source repo name for image CDN URL rewriting.
        sync_errors: Mutable list to append error / info records to.

    Returns:
        str: Fully-processed markdown body, or empty string when no source
        applies.
    """
    from content.utils.copy_file import resolve_copy_file_content
    from content.utils.md_links import (
        rewrite_cross_workshop_md_links,
        rewrite_workshop_md_links,
    )

    explicit_copy_file = data.get('copy_file')
    yaml_description = data.get('description', '') or ''

    try:
        body, error = resolve_copy_file_content(
            workshop_dir, explicit_copy_file, default='README.md',
        )
    except (ValueError, OSError) as e:
        # Treat parse failure on a resolved copy_file as an error so authors
        # notice. The helper does not catch IO/parse errors itself — that is
        # a caller policy decision. ``ValueError`` covers YAML/frontmatter
        # parse failures surfaced through ``_read_markdown_body``;
        # ``OSError`` covers missing or unreadable files.
        attempted = (
            explicit_copy_file.strip()
            if isinstance(explicit_copy_file, str) and explicit_copy_file
            else 'README.md'
        )
        sync_errors.append({
            'file': rel_path,
            'error': (
                f'Failed to read landing copy from {attempted}: {e}'
            ),
        })
        return ''

    if error is not None:
        sync_errors.append({'file': rel_path, 'error': error})
        return ''

    if body is None:
        # No file source resolved (no copy_file declared and no README.md).
        # Fall back to yaml description (current behavior).
        return yaml_description

    # We resolved a file. Determine which filename so we can pass an
    # accurate ``source_path`` to the rewriters and emit the shadow note.
    source_filename = (
        explicit_copy_file.strip() if explicit_copy_file else 'README.md'
    )

    # Inform authors when the yaml description is being shadowed so they
    # can clean up the redundant field. Info-level (logger.info) plus a
    # SyncLog entry so it's visible without grepping logs.
    if yaml_description.strip():
        msg = (
            f'workshop.yaml description: is shadowed by '
            f'{source_filename} for workshop landing in {rel_path}'
        )
        logger.info(msg)
        sync_errors.append({
            'file': rel_path,
            'error': msg,
            'severity': 'info',
        })

    if not body or not body.strip():
        # File present but empty / only frontmatter — leave description
        # empty. No error per the spec.
        return ''

    # Rewrite relative image URLs using the workshop folder as the base
    # path (same as tutorial pages would).
    body = rewrite_image_urls(body, repo_name, rel_path)

    # Rewrite intra-workshop ``.md`` links (including the README virtual
    # entry that points back at the landing — useful when copy_file is a
    # tutorial file that itself links to README). Issue #526: pass the
    # cross_workshop_lookup so the intra-workshop pass suppresses its
    # "out-of-tree" warning for ``..``-prefixed links — the cross-workshop
    # pass below picks them up.
    landing_source_path = os.path.join(rel_path, source_filename)
    body = rewrite_workshop_md_links(
        body,
        workshop_slug=workshop_slug,
        page_lookup=page_lookup,
        source_path=landing_source_path,
        sync_errors=sync_errors,
        cross_workshop_lookup=cross_workshop_lookup,
    )

    # Issue #526: rewrite cross-workshop links so the README's
    # ``[Previous workshop](../<sibling-folder>/)`` resolves to a native
    # ``/workshops/<slug>`` URL on the workshop landing page.
    if cross_workshop_lookup is not None and workshops_repo_name:
        body = rewrite_cross_workshop_md_links(
            body,
            cross_workshop_lookup=cross_workshop_lookup,
            workshops_repo_name=workshops_repo_name,
            source_workshop_folder=source_workshop_folder,
            source_path=landing_source_path,
            sync_errors=sync_errors,
        )

    return body


def _sync_course_modules(course, course_dir, repo_dir, repo_name, commit_sha, stats,
                         known_images=None, course_ignore_patterns=None):
    """Sync modules and units for a course.

    ``course_ignore_patterns`` are globs relative to ``course_dir`` from the
    course-level ``ignore:`` key. A directory whose path matches is skipped
    entirely. The patterns are also passed down to unit sync so individual
    files matched at the course level are skipped wherever they appear.
    """
    from content.models import Module

    course_ignore_patterns = course_ignore_patterns or []
    seen_module_paths = set()

    # Build the course-wide unit lookup once before processing any unit so the
    # markdown link rewriter (issue #226) can resolve sibling and cross-module
    # `.md` links to platform URLs. Pass course-level ignore patterns so the
    # lookup mirrors what _sync_module_units actually persists (issue #233).
    #
    # We deliberately don't pass ``stats`` here: the sync loop below
    # (_sync_course_modules) and ``_sync_module_units`` already catch and
    # record parse errors for each ``module.yaml`` and unit ``.md`` file
    # they touch. Forwarding ``stats`` to the lookup would record those
    # same errors twice (issue #286).
    unit_lookup = _build_course_unit_lookup(
        course_dir, course_ignore_patterns=course_ignore_patterns,
    )

    for entry in sorted(os.scandir(course_dir), key=lambda e: e.name):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue

        # Skip whole module dirs that match course-level ignore globs
        # (e.g. `docs/**` ignores the docs/ directory in addition to its files).
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue

        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not os.path.exists(module_yaml_path):
            continue

        try:
            module_data = _parse_yaml_file(module_yaml_path)
            rel_path = os.path.relpath(entry.path, repo_dir)

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(module_data, 'module', rel_path)

            seen_module_paths.add(rel_path)

            # Derive sort_order and slug from directory name
            sort_order = module_data.get(
                'sort_order', extract_sort_order(entry.name),
            )
            slug = module_data.get('slug', derive_slug(entry.name))

            module_defaults = {
                'title': module_data.get('title', entry.name),
                'slug': slug,
                'sort_order': sort_order,
                'source_repo': repo_name,
                'source_commit': commit_sha,
            }
            # Issue #310: prefer source_path lookup, fall back to
            # (course, slug). Module has no content_id field, but the
            # (course, slug) fallback lets a dir-rename that keeps the
            # slug stay idempotent — without it the source_path-based
            # lookup misses and the unique (course, slug) constraint
            # fires on insert.
            module = Module.objects.filter(
                course=course, source_path=rel_path,
            ).first()
            if module is None:
                module = Module.objects.filter(
                    course=course, slug=slug,
                ).first()

            if module is None:
                module = Module(
                    course=course, source_path=rel_path, **module_defaults,
                )
                module.save()
                created = True
                changed = True
            else:
                identity_changed = (
                    module.source_path != rel_path
                    or module.slug != slug
                )
                if identity_changed or _defaults_differ(module, module_defaults):
                    module.source_path = rel_path
                    for k, v in module_defaults.items():
                        setattr(module, k, v)
                    module.save()
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
                # Per-level breakdown (issue #224): track each module touched
                # so the dashboard can show "Modules: X created Y updated"
                # and link to the studio edit page.
                stats['items_detail'].append({
                    'title': module.title,
                    'slug': module.slug,
                    'action': action,
                    'content_type': 'module',
                    'course_id': course.pk,
                    'course_slug': course.slug,
                    'module_id': module.pk,
                })
            else:
                stats['unchanged'] += 1

            # Module-level ignore patterns (relative to module dir). Course
            # patterns are translated/filtered separately in _sync_module_units.
            raw_module_ignore = module_data.get('ignore', []) or []
            module_ignore_patterns = [str(p) for p in raw_module_ignore]

            # Sync units within this module
            _sync_module_units(
                module, entry.path, repo_dir, repo_name, commit_sha, stats,
                known_images=known_images,
                course_dir=course_dir,
                course_ignore_patterns=course_ignore_patterns,
                module_ignore_patterns=module_ignore_patterns,
                course_slug=course.slug,
                unit_lookup=unit_lookup,
            )

        except Exception as e:
            stats['errors'].append({
                'file': os.path.relpath(module_yaml_path, repo_dir),
                'error': str(e),
            })

    # Remove stale modules
    stale_modules = Module.objects.filter(
        course=course,
        source_repo=repo_name,
    ).exclude(source_path__in=seen_module_paths)
    deleted_count = stale_modules.count()
    stale_modules.delete()
    stats['deleted'] += deleted_count


def _sync_module_units(module, module_dir, repo_dir, repo_name, commit_sha, stats,
                       known_images=None, course_dir=None,
                       course_ignore_patterns=None,
                       module_ignore_patterns=None,
                       course_slug=None, unit_lookup=None):
    """Sync units (markdown files) within a module directory.

    ``course_ignore_patterns`` are globs relative to ``course_dir`` (course
    root). ``module_ignore_patterns`` are globs relative to ``module_dir``.
    Files matched by either list are skipped.

    README.md at the module root is the module's overview (issue #222):
    its body is written to ``Module.overview`` and rendered into
    ``Module.overview_html``. The README does NOT become a Unit, so it is
    not counted in lesson totals and does not appear in the lesson list.
    The page at ``/courses/<course>/<module>/`` renders the overview.

    ``course_slug`` and ``unit_lookup`` are used by the markdown link
    rewriter (issue #226) to convert intra-content ``.md`` links into
    platform URLs. When either is missing, link rewriting is skipped.
    """
    from content.models import Unit, UserCourseProgress
    from content.utils.md_links import rewrite_md_links

    course_ignore_patterns = course_ignore_patterns or []
    module_ignore_patterns = module_ignore_patterns or []
    # course_dir defaults to module_dir's parent when not supplied so callers
    # that pre-date this signature still work (course-level patterns become
    # no-ops in that case because course_ignore_patterns is empty).
    if course_dir is None:
        course_dir = os.path.dirname(module_dir)

    seen_unit_paths = set()
    # Track newly created units with their hashes for rename detection
    new_unit_hashes = {}

    def _is_ignored(filename):
        """Return True if the file is matched by any course- or module-level ignore glob."""
        filepath = os.path.join(module_dir, filename)
        rel_to_course = os.path.relpath(filepath, course_dir)
        if _matches_ignore_patterns(rel_to_course, course_ignore_patterns):
            return True
        if _matches_ignore_patterns(filename, module_ignore_patterns):
            return True
        return False

    # README at module root -> Module.overview (issue #222), unless ignored.
    readme_filename = None
    for name in os.listdir(module_dir):
        if name.lower() == 'readme.md':
            readme_filename = name
            break

    if readme_filename and not _is_ignored(readme_filename):
        readme_path = os.path.join(module_dir, readme_filename)
        readme_rel = os.path.relpath(readme_path, repo_dir)
        try:
            _metadata, body = _parse_markdown_file(readme_path)

            base_dir = os.path.dirname(readme_rel)
            if known_images is not None:
                _check_broken_image_refs(
                    body, readme_rel, repo_name, base_dir,
                    known_images, stats.get('errors', []),
                )
            body = rewrite_image_urls(body, repo_name, base_dir)
            # Rewrite intra-content `.md` links to platform URLs (issue #226).
            if course_slug and unit_lookup is not None:
                body = rewrite_md_links(
                    body,
                    course_slug=course_slug,
                    module_slug=module.slug,
                    unit_lookup=unit_lookup,
                    source_path=readme_rel,
                    sync_errors=stats.get('errors'),
                )

            overview_changed = (
                module.overview != body
                or module.overview_source_path != readme_rel
            )
            if overview_changed:
                module.overview = body
                module.overview_source_path = readme_rel
                module.save(update_fields=[
                    'overview', 'overview_html', 'overview_source_path',
                ])
                # Issue #224: surface the README touch in the per-level
                # breakdown so staff can see at a glance that the module
                # overview changed. Reported as content_type='module'
                # (not 'unit'), since README is no longer a Unit.
                stats['items_detail'].append({
                    'title': f'{module.title} — overview',
                    'slug': module.slug,
                    'action': 'updated',
                    'content_type': 'module',
                    'course_id': module.course_id,
                    'course_slug': course_slug or module.course.slug,
                    'module_id': module.pk,
                })
            else:
                # Unchanged README still counts as a synced file — track it
                # in the no-change bucket so the dashboard shows accurate
                # totals (issue #225).
                stats['unchanged'] += 1
        except Exception as e:
            stats['errors'].append({
                'file': readme_rel,
                'error': str(e),
            })
    elif not readme_filename and module.overview:
        # README was removed from the repo: clear the overview so the page
        # falls back to the lesson-list-only layout.
        module.overview = ''
        module.overview_source_path = None
        module.save(update_fields=[
            'overview', 'overview_html', 'overview_source_path',
        ])

    for filename in sorted(os.listdir(module_dir)):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue

        # Respect course- and module-level ignore globs.
        if _is_ignored(filename):
            continue

        filepath = os.path.join(module_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(metadata, 'unit', rel_path)

            # Require content_id in frontmatter
            unit_content_id = metadata.get('content_id')
            if not unit_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            seen_unit_paths.add(rel_path)

            # Edge Case 1: Compute content hash for rename detection
            content_hash = _compute_content_hash(body)

            # Rewrite image URLs
            base_dir = os.path.dirname(rel_path)

            # Edge Case 8: Check broken image references
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, repo_name, base_dir,
                    known_images, stats.get('errors', []),
                )

            body = rewrite_image_urls(body, repo_name, base_dir)
            # Rewrite intra-content `.md` links to platform URLs (issue #226).
            if course_slug and unit_lookup is not None:
                body = rewrite_md_links(
                    body,
                    course_slug=course_slug,
                    module_slug=module.slug,
                    unit_lookup=unit_lookup,
                    source_path=rel_path,
                    sync_errors=stats.get('errors'),
                )

            is_homework = metadata.get('is_homework', False)

            # Derive sort_order and slug from filename
            sort_order = metadata.get(
                'sort_order', extract_sort_order(filename),
            )
            slug = metadata.get('slug', derive_slug(filename))

            # Issue #465: per-unit ``access:`` override. Absent = NULL
            # column = inherit course default / course required level.
            unit_access_raw = metadata.get('access')
            if unit_access_raw is not None:
                unit_required_level = _parse_access_value(
                    unit_access_raw,
                    field_name='access',
                    rel_path=rel_path,
                )
            else:
                unit_required_level = None

            unit_is_preview = bool(metadata.get('is_preview', False))

            # When both ``access:`` and ``is_preview:`` are set we keep
            # both flags (templates branch on ``is_preview`` for the
            # sidebar Preview badge) but record an info-level note so
            # authors notice the redundant key. ``access:`` is treated
            # as the canonical signal for access logic; ``is_preview``
            # remains the legacy alias.
            if unit_access_raw is not None and unit_is_preview:
                stats['errors'].append({
                    'file': rel_path,
                    'severity': 'info',
                    'error': (
                        'Both access: and is_preview: set on unit; '
                        'access: wins. Drop is_preview to keep YAML clean.'
                    ),
                })

            defaults = {
                'title': metadata.get('title', os.path.splitext(filename)[0]),
                'slug': slug,
                'sort_order': sort_order,
                'video_url': metadata.get('video_url', ''),
                'timestamps': metadata.get('timestamps', []),
                'is_preview': unit_is_preview,
                'required_level': unit_required_level,
                'content_hash': content_hash,
                'source_repo': repo_name,
                'source_commit': commit_sha,
                'content_id': unit_content_id,
            }

            if is_homework:
                defaults['homework'] = body
            else:
                defaults['body'] = body

            # Issue #310/#311: prefer content_id-first lookup so renaming
            # a unit's filename or slug doesn't trigger a duplicate insert
            # (which then fails with a unique-constraint violation when the
            # stale row's slug collides). Fall back to source_path for
            # legacy rows that predate content_id, and to (module, slug)
            # so a rename within the same module that updates only the
            # filename is found.
            unit = Unit.objects.filter(
                content_id=unit_content_id,
                source_repo=repo_name,
                module=module,
            ).first()
            if unit is None:
                unit = Unit.objects.filter(
                    module=module, slug=slug,
                ).first()
            if unit is None:
                unit = Unit.objects.filter(
                    module=module, source_path=rel_path,
                ).first()

            if unit is None:
                unit = Unit(
                    module=module, source_path=rel_path, **defaults,
                )
                unit.save()
                created = True
                changed = True
            else:
                identity_changed = (
                    unit.source_path != rel_path
                    or unit.slug != slug
                )
                if identity_changed or _defaults_differ(unit, defaults):
                    unit.source_path = rel_path
                    for k, v in defaults.items():
                        setattr(unit, k, v)
                    unit.save()
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
                new_unit_hashes[content_hash] = unit
            else:
                stats['updated'] += 1
            # Per-level breakdown (issue #224): track each unit touched
            # so the dashboard can show "Lessons (units): X created Y updated"
            # and link to the studio edit page.
            stats['items_detail'].append({
                'title': unit.title,
                'slug': unit.slug,
                'action': action,
                'content_type': 'unit',
                'course_id': module.course_id,
                'course_slug': course_slug or module.course.slug,
                'module_id': module.pk,
                'module_slug': module.slug,
                'unit_id': unit.pk,
            })

        except Exception as e:
            stats['errors'].append({
                'file': rel_path,
                'error': str(e),
            })

    # Remove stale units, with rename detection (Edge Case 1)
    stale_units = Unit.objects.filter(
        module=module,
        source_repo=repo_name,
    ).exclude(source_path__in=seen_unit_paths)

    for stale_unit in stale_units:
        # Check if a newly created unit in the same course has the same hash
        if (stale_unit.content_hash
                and stale_unit.content_hash in new_unit_hashes):
            new_unit = new_unit_hashes[stale_unit.content_hash]
            # Migrate UnitCompletion (UserCourseProgress) records
            migrated = UserCourseProgress.objects.filter(
                unit=stale_unit,
            ).update(unit=new_unit)
            if migrated:
                logger.warning(
                    'Unit appears to have been renamed: %s -> %s, '
                    'migrated %d completion records.',
                    stale_unit.source_path, new_unit.source_path, migrated,
                )

    deleted_count = stale_units.count()
    stale_units.delete()
    stats['deleted'] += deleted_count
