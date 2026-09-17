"""Course sync dispatcher."""

import datetime
import os

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import (
    checkout_exists,
    checkout_is_dir,
    checkout_is_file,
    checkout_listdir,
    checkout_scandir,
    raise_if_checkout_error,
)
from content.sync_parsers.common import GitHubSyncError, logger
from content.sync_parsers.families.homework import sync_unit_homework
from content.sync_parsers.families.instructors import (
    _attach_instructors_to_course,
    _resolve_instructors_for_yaml,
)
from content.sync_parsers.lifecycle import (
    cleanup_stale_synced_objects,
    find_synced_object,
    upsert_synced_object,
)
from content.sync_parsers.media import (
    _check_broken_image_refs,
    rewrite_cover_image_url,
    rewrite_image_urls,
)
from content.sync_parsers.parsing import (
    _compute_content_hash,
    _defaults_differ,
    _parse_markdown_file,
    _parse_yaml_file,
    _validate_frontmatter,
)
from content.sync_parsers.repo_util import _matches_ignore_patterns, derive_slug, extract_sort_order
from integrations.services.banner_generator.dispatch import enqueue_if_missing as _enqueue_banner_if_missing

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

# Issue #1658: courses sold outside the membership plans (e.g. the Maven
# buildcamp). 'tier' is the default (existing behaviour for every course
# that omits the key); 'entitlement' skips the tier comparison entirely.
_VALID_ACCESS_MODES = frozenset({'tier', 'entitlement'})


def _resolve_access_mode(
    course_data, rel_path, *, required_level=0, default_unit_required_level=None,
):
    """Resolve and validate the optional ``access_mode:`` YAML key.

    Returns ``'tier'`` when the key is absent (today's behaviour for every
    existing course — no regression). Raises :class:`GitHubSyncError` for
    an unrecognized value, or for an ``'entitlement'`` course that would
    ship with a hole in the gating it claims to have (issue #1658 QA
    follow-up):

    - no ``enroll_url`` — no way to enroll is a content bug on its own.
    - ``required_level`` below Basic — ``can_access()`` grants
      ``LEVEL_OPEN``/``LEVEL_REGISTERED`` content before the
      entitlement/tier branch ever runs (documented, intentional
      early-return behaviour in ``content/access.py``), so a
      sub-Basic ``required_level`` makes the entitlement gate a no-op:
      every anonymous or signed-in visitor gets in while the page still
      shows "External course" copy.
    - ``default_unit_required_level`` below Basic — same hole, but for
      the per-lesson wall: this is the field that actually controls
      whether unit content is readable, and it can be set independently
      of ``required_level``. This is the field that made the real
      buildcamp course.yaml (``required_level: 0`` +
      ``default_unit_access: registered``) exploitable.

    Per-unit ``access:`` overrides are intentionally NOT checked here —
    one unit explicitly opened as a free preview lesson inside an
    otherwise-gated entitlement course is the same legitimate pattern
    tier-mode courses already use, not a content bug.
    """
    raw = course_data.get('access_mode')
    if raw is None:
        return 'tier'
    key = str(raw).strip().lower()
    if key not in _VALID_ACCESS_MODES:
        raise GitHubSyncError(
            f"Unknown access_mode {raw!r}: expected 'tier' or 'entitlement' "
            f'(in {os.path.join(rel_path, "course.yaml")})'
        )
    if key == 'entitlement':
        yaml_path = os.path.join(rel_path, 'course.yaml')
        if not str(course_data.get('enroll_url', '') or '').strip():
            raise GitHubSyncError(
                f'access_mode: entitlement requires enroll_url (in {yaml_path})'
            )
        level_basic = _ACCESS_NAME_TO_LEVEL['basic']
        if required_level < level_basic:
            raise GitHubSyncError(
                f'access_mode: entitlement requires required_level Basic or '
                f'above (got {required_level!r}) (in {yaml_path})'
            )
        if (
            default_unit_required_level is not None
            and default_unit_required_level < level_basic
        ):
            raise GitHubSyncError(
                f'access_mode: entitlement requires default_unit_access Basic '
                f'or above (got {default_unit_required_level!r}) (in {yaml_path})'
            )
    return key


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


class CoursesParser(FamilyParser):
    content_type = 'courses'
    state_name = 'courses'

    def iter_items(self, run):
        for course_dir in run.classification().course_dirs:
            rel_dir = os.path.relpath(course_dir, run.repo_dir)
            yield rel_dir, {'rel_path': rel_dir, 'course_dir': course_dir}

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        _sync_single_course(
            payload['course_dir'], run.repo_dir, run.source, run.commit_sha,
            stats, state.seen, state.failed,
            known_images=run.known_images(),
        )
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        state = self._state(run)
        stats = self.item_stats()
        deleted = _cleanup_stale_courses_for_source(
            run.source, state.seen, state.failed, stats,
        )
        self.absorb(run, stats)
        return deleted


def _cleanup_stale_courses_for_source(
    source, seen_course_slugs, failed_course_slugs, stats,
):
    from content.models import Course

    stale_courses = list(Course.objects.filter(
        source_repo=source.repo_name,
        status='published',
    ).exclude(slug__in=seen_course_slugs).exclude(slug__in=failed_course_slugs))
    return cleanup_stale_synced_objects(
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
        _sync_course_cohorts(course, course_data, rel_path)

        # Issue #788/#900: enqueue auto-banner render on EVERY sync, not
        # only on create/update. ``_enqueue_banner_if_missing`` itself
        # short-circuits when cover_image_url is set or the title hash
        # hasn't drifted, so re-syncs stay cheap — but a previously-synced
        # cover-less course whose first render was lost (e.g. a cold Lambda
        # timeout) gets backfilled on the next no-op sync.
        _enqueue_banner_if_missing('course', course.pk)

    except Exception as e:
        raise_if_checkout_error(e)
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
            exc_info=True,
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
    required_level = course_data.get('required_level', 0)
    access_mode = _resolve_access_mode(
        course_data, rel_path,
        required_level=required_level,
        default_unit_required_level=default_unit_required_level,
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
        'required_level': required_level,
        'default_unit_required_level': default_unit_required_level,
        'discussion_url': course_data.get('discussion_url', ''),
        'maven_course_key': course_data.get('maven_course_key', ''),
        'access_mode': access_mode,
        'enroll_url': course_data.get('enroll_url', '') or '',
        'program_label': course_data.get('program_label', '') or '',
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
        not checkout_is_file(readme_path)
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


_COHORT_REQUIRED_FIELDS = ('key', 'name', 'start_date', 'end_date')
_COHORT_SELF_PACED_REQUIRED_FIELDS = ('key', 'name')
_VALID_COHORT_MODES = frozenset({'cohort', 'self_paced'})


def _sync_course_cohorts(course, course_data, rel_path):
    """Upsert ``course.yaml``'s ``cohorts:`` list into ``content.Cohort``.

    Issue #1659: each entry is keyed on ``(course, external_key=key)`` — a
    new key creates a cohort, a known key updates ``name``/``start_date``/
    ``end_date``/``mode`` when they changed. A cohort key dropped from a
    later YAML edit is left untouched in the database (reconcile-never
    -destroy, same as the rest of the sync pipeline; a cohort may already
    have enrollments).

    Issue #1674: an optional ``mode:`` key (default ``cohort``, matching
    the model default). A ``mode: self_paced`` entry must NOT set
    ``start_date``/``end_date`` — sync fails that course naming the
    cohort key if either is present. A second ``mode: self_paced`` entry
    for the same course fails sync too, via ``Cohort.full_clean()``
    surfacing the partial unique constraint as a ``ValidationError``.

    Raises :class:`GitHubSyncError` on a malformed entry so the caller's
    per-course exception handler records it against this course's sync only
    — it must never abort the whole content sync run.
    """
    from content.models.cohort import Cohort

    cohorts_data = course_data.get('cohorts') or []
    if not isinstance(cohorts_data, list):
        raise GitHubSyncError(
            f'Invalid cohorts in {rel_path}: expected a list'
        )

    for entry in cohorts_data:
        if not isinstance(entry, dict):
            raise GitHubSyncError(
                f'Invalid cohorts entry in {rel_path}: expected a mapping, '
                f'got {type(entry).__name__}'
            )

        mode_raw = entry.get('mode', 'cohort')
        mode = str(mode_raw).strip().lower() if not isinstance(mode_raw, bool) else None
        if mode not in _VALID_COHORT_MODES:
            raise GitHubSyncError(
                f'Invalid cohorts entry in {rel_path}: unknown mode '
                f"{mode_raw!r} (expected 'cohort' or 'self_paced')"
            )

        if mode == 'self_paced':
            missing = [
                field for field in _COHORT_SELF_PACED_REQUIRED_FIELDS
                if entry.get(field) is None or entry.get(field) == ''
            ]
            if missing:
                raise GitHubSyncError(
                    f"Invalid cohorts entry in {rel_path}: "
                    f"missing {', '.join(missing)}"
                )
            key = str(entry['key']).strip()
            if entry.get('start_date') not in (None, '') or entry.get('end_date') not in (None, ''):
                raise GitHubSyncError(
                    f"Invalid cohorts entry '{key}' in {rel_path}: "
                    'mode: self_paced must not set start_date/end_date'
                )
            defaults = {
                'name': entry['name'],
                'start_date': None,
                'end_date': None,
                'mode': 'self_paced',
            }
        else:
            missing = [
                field for field in _COHORT_REQUIRED_FIELDS
                if entry.get(field) is None or entry.get(field) == ''
            ]
            if missing:
                raise GitHubSyncError(
                    f"Invalid cohorts entry in {rel_path}: "
                    f"missing {', '.join(missing)}"
                )
            key = str(entry['key']).strip()
            defaults = {
                'name': entry['name'],
                'start_date': _parse_cohort_date(
                    entry['start_date'], field_name='start_date', rel_path=rel_path,
                ),
                'end_date': _parse_cohort_date(
                    entry['end_date'], field_name='end_date', rel_path=rel_path,
                ),
                'mode': 'cohort',
            }

        cohort = Cohort.objects.filter(course=course, external_key=key).first()
        if cohort is None:
            cohort = Cohort(course=course, external_key=key, **defaults)
            try:
                cohort.full_clean()
            except ValidationError as exc:
                raise GitHubSyncError(
                    f"Invalid cohorts entry '{key}' in {rel_path}: "
                    f'{"; ".join(exc.messages)}'
                ) from exc
            cohort.save()
            continue

        changed_fields = [
            field for field, value in defaults.items()
            if getattr(cohort, field) != value
        ]
        if changed_fields:
            for field in changed_fields:
                setattr(cohort, field, defaults[field])
            try:
                cohort.full_clean()
            except ValidationError as exc:
                raise GitHubSyncError(
                    f"Invalid cohorts entry '{key}' in {rel_path}: "
                    f'{"; ".join(exc.messages)}'
                ) from exc
            cohort.save(update_fields=changed_fields)


def _parse_cohort_date(value, *, field_name, rel_path):
    """Resolve a ``cohorts:`` date value to a ``datetime.date``.

    PyYAML's safe loader already parses unquoted ISO ``YYYY-MM-DD`` values
    into ``datetime.date``, so that is accepted directly. A quoted string is
    parsed explicitly so authors who quote the date don't hit a type error.
    """
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
        f'not a valid date (expected YYYY-MM-DD)'
    )


def _parse_module_yaml_for_lookup(module_yaml_path, course_dir, entry_name, stats):
    """Parse one ``module.yaml`` for the link-lookup builder.

    Returns ``(module_slug, module_ignore_patterns)``. Best-effort: a parse
    failure logs/records the error and falls back to a directory-derived
    slug with no ignore patterns, mirroring the pre-#1674 inline behaviour.
    """
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
    module_slug = module_data.get('slug') or derive_slug(entry_name)
    raw_module_ignore = module_data.get('ignore', []) or []
    module_ignore_patterns = [str(p) for p in raw_module_ignore]
    return module_slug, module_ignore_patterns


def _collect_module_lookup_files(
    module_dir, course_dir, course_ignore_patterns, module_ignore_patterns, stats,
):
    """Return ``{filename: unit_slug}`` for the ``.md`` files directly in
    ``module_dir`` (not its submodule subdirectories, if any) — the same
    file-selection rule :func:`_sync_module_units` applies."""
    files = {}
    for filename in checkout_listdir(module_dir):
        if (
            not filename.lower().endswith('.md')
            or filename.startswith('.')
        ):
            continue
        filepath = os.path.join(module_dir, filename)
        if not checkout_is_file(filepath):
            continue

        rel_to_course = os.path.relpath(filepath, course_dir)
        if _matches_ignore_patterns(rel_to_course, course_ignore_patterns):
            continue
        if _matches_ignore_patterns(filename, module_ignore_patterns):
            continue

        try:
            metadata, _ = _parse_markdown_file(filepath)
        except ValueError as exc:
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
            # Registered under a sentinel slug so the link rewriter can
            # spot README.md targets and emit module-overview URLs.
            unit_slug = '__module_overview__'
        else:
            if not metadata.get('content_id'):
                continue
            unit_slug = metadata.get('slug', derive_slug(filename))
        files[filename] = unit_slug
    return files


def _build_course_unit_lookup(course_dir, course_ignore_patterns=None, stats=None):
    """Build a nested module-tree map for the markdown link rewriter.

    Issue #1674: walks one level into submodule subdirectories (the same
    depth cap the rest of the sync enforces) so cross-submodule links —
    including links to a submodule under a DIFFERENT parent week — resolve
    to the correct nested URL instead of being silently left unrewritten.
    A module directory that mixes submodules with direct unit files (sync
    rejects that course outright) is walked as a parent here too; its
    (invalid) direct files are simply not registered, matching "the sync
    never actually created those units".

    Shape: ``{top_level_slug: {'dir_name': str, 'files': {filename:
    unit_slug}, 'children': {submodule_slug: {'dir_name': str, 'files':
    {...}}}}}``. ``files`` is empty on a parent entry (mixed content is
    forbidden, so a module with ``children`` never legitimately has
    direct units); ``children`` is empty on a leaf entry.

    Used by the markdown link rewriter (issue #226) so we can resolve
    sibling and cross-module ``.md`` links without a database round-trip
    per link. The slug derivation here mirrors what :func:`_sync_module_units`
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
    if not checkout_is_dir(course_dir):
        return lookup

    course_ignore_patterns = course_ignore_patterns or []

    for entry in checkout_scandir(course_dir):
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
        if not checkout_exists(module_yaml_path):
            continue

        module_slug, module_ignore_patterns = _parse_module_yaml_for_lookup(
            module_yaml_path, course_dir, entry.name, stats,
        )

        submodule_entries = _find_submodule_dir_entries(
            entry.path, course_ignore_patterns, course_dir,
        )

        children = {}
        for sub_entry in submodule_entries:
            sub_yaml_path = os.path.join(sub_entry.path, 'module.yaml')
            sub_slug, sub_ignore_patterns = _parse_module_yaml_for_lookup(
                sub_yaml_path, course_dir, sub_entry.name, stats,
            )
            children[sub_slug] = {
                'dir_name': sub_entry.name,
                'files': _collect_module_lookup_files(
                    sub_entry.path, course_dir, course_ignore_patterns,
                    sub_ignore_patterns, stats,
                ),
            }

        # A parent module (has submodule children) never legitimately has
        # direct unit files (mixed content is rejected by sync) — skip
        # collecting them so a malformed mixed directory doesn't register
        # ghost sibling-link targets for units the sync never created.
        files = (
            {}
            if children
            else _collect_module_lookup_files(
                entry.path, course_dir, course_ignore_patterns,
                module_ignore_patterns, stats,
            )
        )

        lookup[module_slug] = {
            'dir_name': entry.name,
            'files': files,
            'children': children,
        }

    return lookup


def _build_workshop_page_lookup(
    workshop_dir, workshop_slug, workshop_title=None, copy_file=None,
    workshop_url_key=None,
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
        workshop_url_key: Canonical public workshop URL key. This is the
            slug-only key; when omitted, ``workshop_slug`` is used.
    """
    lookup = {}
    if not checkout_is_dir(workshop_dir):
        return lookup

    for filename in checkout_listdir(workshop_dir):
        if (
            not filename.endswith('.md')
            or filename.upper() == 'README.MD'
            or filename.startswith('.')
        ):
            continue

        filepath = os.path.join(workshop_dir, filename)
        if not checkout_is_file(filepath):
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
        path_key = workshop_url_key or workshop_slug
        # Issue #1720: canonical page URL dropped the /tutorial/ segment.
        url = f'/workshops/{path_key}/{slug}'
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
        path_key = workshop_url_key or workshop_slug
        landing_url = f'/workshops/{path_key}'
        readme_path = os.path.join(workshop_dir, 'README.md')
        if checkout_is_file(readme_path):
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
                if checkout_is_file(copy_path):
                    lookup[copy_file] = {
                        'slug': '',
                        'title': workshop_title,
                        'url': landing_url,
                    }

    return lookup


def _resolve_workshop_landing_copy(
    workshop_dir, data, rel_path, page_lookup, workshop_slug, repo_name,
    sync_errors, cross_workshop_lookup=None, workshops_repo_name=None,
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
            source_path=landing_source_path,
            sync_errors=sync_errors,
        )

    return body


def _find_submodule_dir_entries(module_dir, course_ignore_patterns, course_dir):
    """Return dir entries under ``module_dir`` that are themselves modules.

    A subdirectory is a submodule when it carries its own ``module.yaml``
    (issue #1674) — exactly mirroring how a course directory's
    subdirectories become modules today, one level deeper.
    """
    entries = []
    for entry in checkout_scandir(module_dir):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue
        if checkout_exists(os.path.join(entry.path, 'module.yaml')):
            entries.append(entry)
    return entries


def _has_direct_unit_files(module_dir, course_ignore_patterns, module_ignore_patterns, course_dir):
    """Return True if ``module_dir`` has a non-README ``.md`` unit file.

    Mirrors the file-selection rule ``_sync_module_units`` itself applies
    (README is the overview, not a unit; ignore globs are respected) so
    the mixed-content detection agrees with what would actually sync.
    """
    for filename in checkout_listdir(module_dir):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue
        filepath = os.path.join(module_dir, filename)
        if not checkout_is_file(filepath):
            continue
        rel_to_course = os.path.relpath(filepath, course_dir)
        if _matches_ignore_patterns(rel_to_course, course_ignore_patterns):
            continue
        if _matches_ignore_patterns(filename, module_ignore_patterns):
            continue
        return True
    return False


def _upsert_module_row(
    course, parent_module, entry, module_data, rel_path, repo_name,
    commit_sha, stats, seen_module_slugs=None,
    allow_parent_with_pending_units=False,
):
    """Create or update the ``Module`` row for one module directory.

    Shared by top-level modules and submodules (issue #1674) — same
    upsert-by-source_path-then-slug lookup, same ``module.yaml`` optional
    keys (``bonus:`` -> ``is_bonus``, ``available_after_days:``). Runs
    ``full_clean()`` so the depth-cap / same-course / self-parent / mixed
    -content invariants (``Module.clean()``) are enforced on every sync,
    converting a ``ValidationError`` into a :class:`GitHubSyncError` that
    names this directory.

    Issue #1681: ``seen_module_slugs`` is a course-wide ``{(parent_pk,
    slug): rel_path}`` map shared across every directory processed this
    sync run. Two module directories that derive the same slug within the
    same sibling group (same parent, or both top-level) would otherwise be
    silently merged by the ``(course, parent, slug)`` fallback lookup below
    — the second directory's fields would overwrite the first's row in
    place, with no error. Checking first and raising a named
    :class:`GitHubSyncError` (caught by the caller and recorded in
    ``stats['errors']``) turns that silent merge into a visible error
    naming both offending source paths, without ever reaching
    ``module.save()``.

    Issue #1721: ``allow_parent_with_pending_units`` is set by
    :func:`_sync_module_dir` for every submodule entry whose parent is
    mid-transition — kept its slug across a restructure while gaining
    submodules this sync, so its own new submodules are being created
    while it still holds its OLD direct units (nothing has reparented
    them yet at this point in the walk). When true, sets the private,
    transient ``_allow_parent_with_pending_units`` attribute on the
    in-memory ``module`` instance before ``full_clean()`` so
    ``Module.clean()``'s "parent already has direct units" check is
    bypassed for THIS submodule only. The bypass is never persisted and
    never applies to any other caller.
    """
    from content.models import Module

    sort_order = module_data.get('sort_order', extract_sort_order(entry.name))
    slug = module_data.get('slug', derive_slug(entry.name))

    if seen_module_slugs is not None:
        identity_key = (parent_module.pk if parent_module else None, slug)
        other_rel_path = seen_module_slugs.get(identity_key)
        if other_rel_path is not None and other_rel_path != rel_path:
            raise GitHubSyncError(
                f"Slug collision: modules {other_rel_path!r} and "
                f"{rel_path!r} both resolve to slug {slug!r} within the "
                f'same course. Skipped {rel_path}.'
            )
        seen_module_slugs[identity_key] = rel_path

    is_bonus = bool(module_data.get('bonus', False))
    available_after_days_raw = module_data.get('available_after_days')
    available_after_days = None
    if available_after_days_raw is not None:
        try:
            available_after_days = int(available_after_days_raw)
        except (TypeError, ValueError):
            raise GitHubSyncError(
                f'Invalid available_after_days in {rel_path}/module.yaml: '
                f'{available_after_days_raw!r} (expected an integer)'
            ) from None

    module_defaults = {
        'title': module_data.get('title', entry.name),
        'slug': slug,
        'sort_order': sort_order,
        'is_bonus': is_bonus,
        'available_after_days': available_after_days,
        'parent': parent_module,
        'source_repo': repo_name,
        'source_commit': commit_sha,
    }
    # Issue #310: prefer source_path lookup, fall back to (course, parent,
    # slug). Module has no content_id field, but the fallback lets a
    # dir-rename that keeps the slug stay idempotent. Scoped by
    # ``parent_module`` too (issue #1674 grooming correction): slug
    # uniqueness is per sibling group, not course-wide, so a bare
    # (course, slug) fallback could match the wrong module once two
    # submodules under different parents share a slug (e.g. "Homework"
    # repeated under several weeks).
    module = Module.objects.filter(
        course=course, source_path=rel_path,
    ).first()
    if module is None:
        module = Module.objects.filter(
            course=course, parent=parent_module, slug=slug,
        ).first()

    if module is None:
        module = Module(course=course, source_path=rel_path, **module_defaults)
        created = True
        changed = True
    else:
        identity_changed = (
            module.source_path != rel_path
            or module.slug != slug
            or module.parent_id != (parent_module.pk if parent_module else None)
        )
        changed = identity_changed or _defaults_differ(module, module_defaults)
        module.source_path = rel_path
        for k, v in module_defaults.items():
            setattr(module, k, v)
        created = False

    if allow_parent_with_pending_units:
        module._allow_parent_with_pending_units = True

    try:
        module.full_clean()
    except ValidationError as exc:
        raise GitHubSyncError(
            f'Invalid module in {rel_path}: {"; ".join(exc.messages)}'
        ) from exc

    if changed:
        module.save()
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

    return module


def _sync_module_dir(
    course, entry, parent_module, repo_dir, repo_name, commit_sha, stats,
    known_images, course_dir, course_ignore_patterns, course_slug,
    unit_lookup, seen_module_paths, seen_module_slugs, unit_sync_state,
    allow_parent_with_pending_units=False,
):
    """Sync one module directory: the module row, then either its
    submodules (parent) or its units (leaf) — never both (issue #1674).

    ``seen_module_slugs`` and ``unit_sync_state`` are shared, mutable,
    course-wide accumulators threaded through the whole recursive tree
    walk (issue #1681) — see :func:`_upsert_module_row` and
    :func:`_sync_module_units` for what they track and why.

    ``allow_parent_with_pending_units`` (issue #1721) is passed down by
    THIS call's caller when ``parent_module`` (the module directory being
    synced one level up) is itself mid-transition — kept its slug while
    gaining submodules this sync, so it still holds its old direct units
    at this point in the walk. When true, it is forwarded to
    :func:`_upsert_module_row` for THIS entry (a submodule of that
    transitioning parent) so its own creation isn't blocked by
    ``Module.clean()``'s "parent already has direct units" check.
    """
    module_yaml_path = os.path.join(entry.path, 'module.yaml')
    module_data = _parse_yaml_file(module_yaml_path)
    rel_path = os.path.relpath(entry.path, repo_dir)

    # Edge Case 7: Frontmatter validation
    _validate_frontmatter(module_data, 'module', rel_path)

    # Recorded before the mixed-content check below so a content mistake
    # mid-edit never causes a previously-valid module to look stale and
    # get swept by the end-of-sync cleanup.
    seen_module_paths.add(rel_path)

    raw_module_ignore = module_data.get('ignore', []) or []
    module_ignore_patterns = [str(p) for p in raw_module_ignore]

    submodule_entries = _find_submodule_dir_entries(
        entry.path, course_ignore_patterns, course_dir,
    )
    has_direct_units = _has_direct_unit_files(
        entry.path, course_ignore_patterns, module_ignore_patterns, course_dir,
    )

    if submodule_entries and has_direct_units:
        # Reject the mixed directory outright — do not create either side.
        # A submodule row and/or a stray unit row created before this
        # check would leave the "does not partially create either side"
        # contract broken, so the directory-shape check runs before any
        # DB write for this directory.
        raise GitHubSyncError(
            f'Module directory {rel_path} mixes submodule subdirectories '
            'with direct unit markdown files — a module must hold either '
            'submodules or units, never both. Move the unit files into a '
            'submodule subdirectory, or remove the submodule dirs.'
        )

    module = _upsert_module_row(
        course, parent_module, entry, module_data, rel_path, repo_name,
        commit_sha, stats, seen_module_slugs=seen_module_slugs,
        allow_parent_with_pending_units=allow_parent_with_pending_units,
    )

    if submodule_entries:
        # Parent module: README (if any) is still its overview; it has no
        # direct units (guarded above), so this only processes the README.
        _sync_module_units(
            module, entry.path, repo_dir, repo_name, commit_sha, stats,
            known_images=known_images,
            course_dir=course_dir,
            course_ignore_patterns=course_ignore_patterns,
            module_ignore_patterns=module_ignore_patterns,
            course_slug=course_slug,
            unit_lookup=unit_lookup,
            unit_sync_state=unit_sync_state,
        )

        # Issue #1721: a module that keeps its slug across a restructure
        # while gaining submodules this sync still holds its OLD direct
        # units at this point — the immediate sweep just above correctly
        # declined to delete them (their content_id is claimed elsewhere
        # in the course-wide precompute), and nothing has reparented them
        # onto the new submodules yet. This is a live check: the parent
        # row and its currently-attached units already exist in the DB
        # here. When true, bypass Module.clean()'s "parent already has
        # direct units" check for every one of this parent's own
        # submodule entries this run (not just the first), since a later
        # sibling may be the one that ends up claiming a given unit.
        parent_has_pending_units = bool(submodule_entries) and module.units.exists()

        for submodule_entry in submodule_entries:
            try:
                _sync_module_dir(
                    course, submodule_entry, module, repo_dir, repo_name,
                    commit_sha, stats, known_images, course_dir,
                    course_ignore_patterns, course_slug, unit_lookup,
                    seen_module_paths, seen_module_slugs, unit_sync_state,
                    allow_parent_with_pending_units=parent_has_pending_units,
                )
            except Exception as e:
                raise_if_checkout_error(e)
                stats['errors'].append({
                    'file': os.path.relpath(
                        os.path.join(submodule_entry.path, 'module.yaml'),
                        repo_dir,
                    ),
                    'error': str(e),
                })

        if parent_has_pending_units:
            # Re-validate WITHOUT the bypass, purely to invoke the
            # existing symmetric "cannot have both submodules and direct
            # units" check (Module.clean(), belt-and-braces branch). If
            # some of the parent's original direct units were never
            # claimed by any of its own new submodules this run, that is
            # a genuine content gap (not a sync-ordering artifact): name
            # it as one error and move on rather than aborting the rest
            # of the course's sync.
            try:
                module.full_clean()
            except ValidationError as exc:
                stuck_units = list(module.units.order_by('source_path')[:5])
                remaining_count = module.units.count()
                stuck_desc = ', '.join(
                    f'{u.title!r} ({u.source_path})' for u in stuck_units
                )
                if remaining_count > len(stuck_units):
                    stuck_desc += (
                        f', and {remaining_count - len(stuck_units)} more'
                    )
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f'Module "{module.title}" ({rel_path}) gained '
                        f'submodules this sync but still has direct '
                        f'unit(s) not claimed by any of them: {stuck_desc}. '
                        'These units have no matching content_id anywhere '
                        'else in the course. Move them into one of the new '
                        'submodules or remove them, then re-sync. '
                        f'({"; ".join(exc.messages)})'
                    ),
                })
    else:
        # Leaf module (today's two-level shape, unchanged behaviour).
        _sync_module_units(
            module, entry.path, repo_dir, repo_name, commit_sha, stats,
            known_images=known_images,
            course_dir=course_dir,
            course_ignore_patterns=course_ignore_patterns,
            module_ignore_patterns=module_ignore_patterns,
            course_slug=course_slug,
            unit_lookup=unit_lookup,
            unit_sync_state=unit_sync_state,
        )


def _precompute_course_unit_identities(course_dir, repo_dir, course_ignore_patterns):
    """Dry, read-only walk of the whole course tree (issue #1681).

    Computes, BEFORE any DB write happens this sync run, every unit
    identity (``content_id``, repo-relative ``source_path``) that will
    be seen anywhere in the course. Used by the per-module stale-unit
    sweep in :func:`_sync_module_units` so a stale-candidate decision is
    safe regardless of directory-walk order: a unit that will be
    reparented onto a DIFFERENT module later in the walk is never swept
    from its old module, because its ``content_id`` already shows up in
    this course-wide set from the very start — the decision doesn't
    depend on which module directory happens to be processed first.

    Mirrors the file-selection rules :func:`_sync_module_units` itself
    applies (README excluded, ignore globs respected, ``content_id``
    required) and the directory-shape rules
    :func:`_sync_course_modules`/:func:`_sync_module_dir` apply
    (``module.yaml`` required, one level of submodules). Deliberately
    permissive about structural edge cases the real sync would reject
    (e.g. a directory mixing submodules with direct unit files, or a
    third level of nesting) — being over-inclusive here only delays a
    genuinely stale unit's cleanup to the course-end fallback sweep
    (:func:`_cleanup_stale_units_for_course`), it never causes a
    wrongful immediate delete. A markdown parse failure is likewise
    skipped silently here (best-effort hint only) — the real walk
    records that error itself when it gets to the file.
    """
    seen_paths = set()
    seen_content_ids = set()

    if not checkout_is_dir(course_dir):
        return seen_paths, seen_content_ids

    def _collect_dir(module_dir, module_ignore_patterns):
        for filename in checkout_listdir(module_dir):
            if not filename.endswith('.md') or filename.upper() == 'README.MD':
                continue
            filepath = os.path.join(module_dir, filename)
            if not checkout_is_file(filepath):
                continue
            rel_to_course = os.path.relpath(filepath, course_dir)
            if _matches_ignore_patterns(rel_to_course, course_ignore_patterns):
                continue
            if _matches_ignore_patterns(filename, module_ignore_patterns):
                continue
            try:
                metadata, _body = _parse_markdown_file(filepath)
            except ValueError:
                continue
            content_id = metadata.get('content_id')
            if not content_id:
                continue
            seen_paths.add(os.path.relpath(filepath, repo_dir))
            seen_content_ids.add(content_id)

    for entry in checkout_scandir(course_dir):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue
        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not checkout_exists(module_yaml_path):
            continue
        try:
            module_data = _parse_yaml_file(module_yaml_path) or {}
        except ValueError:
            module_data = {}
        module_ignore_patterns = [
            str(p) for p in (module_data.get('ignore', []) or [])
        ]

        _collect_dir(entry.path, module_ignore_patterns)

        for sub_entry in _find_submodule_dir_entries(
            entry.path, course_ignore_patterns, course_dir,
        ):
            sub_yaml_path = os.path.join(sub_entry.path, 'module.yaml')
            try:
                sub_data = _parse_yaml_file(sub_yaml_path) or {}
            except ValueError:
                sub_data = {}
            sub_ignore_patterns = [
                str(p) for p in (sub_data.get('ignore', []) or [])
            ]
            _collect_dir(sub_entry.path, sub_ignore_patterns)

    return seen_paths, seen_content_ids


def _count_real_errors(errors, start_index):
    """Count genuine errors recorded in ``errors`` from ``start_index`` on.

    Issue #1721 severity fix: entries appended to ``stats['errors']`` are
    not all errors. Some carry ``severity: 'info'`` -- an informational
    note (e.g. homework sync skipped because no cohort resolves, or a
    redundant-but-harmless YAML key combination) that must never suppress
    the stale-content sweeps. Anything else counts as a real error,
    INCLUDING an entry with no ``severity`` key at all: that is exactly
    the shape the original #1721 incident's 12 validation errors had, and
    the gate exists to stop a sync from reaping content while any of
    those are unresolved. Do not assume every entry carries the key.
    """
    return sum(1 for e in errors[start_index:] if e.get('severity', 'info') != 'info')


def _sync_course_modules(course, course_dir, repo_dir, repo_name, commit_sha, stats,
                         known_images=None, course_ignore_patterns=None):
    """Sync modules (and, since issue #1674, submodules) and units for a course.

    ``course_ignore_patterns`` are globs relative to ``course_dir`` from the
    course-level ``ignore:`` key. A directory whose path matches is skipped
    entirely. The patterns are also passed down to unit sync so individual
    files matched at the course level are skipped wherever they appear.

    A top-level module directory becomes a parent module when it contains
    one or more subdirectories that themselves carry a ``module.yaml`` —
    submodules are parsed exactly like top-level modules, one level
    deeper (own ``sort_order``/slug derivation, own ``module.yaml``
    overrides, own README overview, own ``ignore:`` list). A directory
    that mixes submodule subdirectories with direct unit markdown files
    fails that module's sync with a named error and creates neither side.
    """
    from content.models import Module

    course_ignore_patterns = course_ignore_patterns or []
    seen_module_paths = set()
    # Issue #1674 grooming correction: slug uniqueness is per sibling
    # group (course, parent), not course-wide — see the constraint
    # comment on ``Module.Meta``. Keyed ``(parent_pk_or_None, slug) ->
    # rel_path`` so a genuine collision within the same sibling group is
    # caught by ``_upsert_module_row`` before it can silently merge two
    # different directories into one row (issue #1681).
    seen_module_slugs = {}
    # Issue #1681: a dry, read-only pre-scan of the whole course tree,
    # computed BEFORE any DB write this run. ``_sync_module_units``'s
    # per-module stale-unit sweep uses these course-wide sets (instead
    # of that one module directory's own listing) to decide whether a
    # unit still attached to the module being synced is genuinely gone
    # or will be claimed by a DIFFERENT module later in this same walk
    # — closing the directory-walk-order race where a unit that's
    # mid-move would otherwise be deleted (cascading its
    # ``UserCourseProgress``) before its new module gets a chance to
    # reparent it, regardless of which module directory is processed
    # first.
    precomputed_seen_paths, precomputed_seen_content_ids = (
        _precompute_course_unit_identities(
            course_dir, repo_dir, course_ignore_patterns,
        )
    )
    # Course-wide accumulators built incrementally as the REAL walk
    # proceeds (unlike the precomputed sets above, these only know what
    # has actually happened so far). Used for duplicate-content_id
    # detection (real semantics require real processing order), for
    # legacy content-hash rename-migration matching (a stale unit's
    # replacement `Unit` row must actually exist in the DB before its
    # ``UserCourseProgress`` can be repointed onto it), and by the
    # end-of-course fallback sweep below, which catches anything the
    # per-module immediate sweep didn't get to — a rename-migration
    # match created later in the walk than its stale source, or any
    # edge case the precompute missed.
    unit_sync_state = {
        'seen_paths': set(),
        # content_id -> first rel_path claiming it this run. Used for
        # duplicate-content_id detection inside ``_sync_module_units``
        # and, at course-end, as part of the fallback-sweep exclusion.
        'content_id_sources': {},
        'failed_content_ids': set(),
        # content_hash -> newly created Unit, for stale-unit rename
        # migration (Edge Case 1), scoped course-wide.
        'new_hashes': {},
        'precomputed_seen_paths': precomputed_seen_paths,
        'precomputed_seen_content_ids': precomputed_seen_content_ids,
        # Issue #1721: baseline error count captured right before the
        # module/unit tree walk begins (below), deliberately excluding
        # errors recorded before this function runs (e.g. instructor
        # resolution in ``_sync_course_children``) — those are unrelated
        # to what the stale-content sweeps reason about and must not
        # block cleanup by themselves. Every sweep call site re-derives
        # ``walk_has_errors`` from this baseline fresh, rather than
        # caching a single before/after snapshot, so a module processed
        # cleanly before the first error in the walk still swept as
        # normal at its own immediate-sweep point.
        'errors_at_walk_start': len(stats['errors']),
    }

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

    for entry in checkout_scandir(course_dir):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name == 'images':
            continue

        # Skip whole module dirs that match course-level ignore globs
        # (e.g. `docs/**` ignores the docs/ directory in addition to its files).
        dir_rel_to_course = os.path.relpath(entry.path, course_dir)
        if _matches_ignore_patterns(dir_rel_to_course, course_ignore_patterns):
            continue

        module_yaml_path = os.path.join(entry.path, 'module.yaml')
        if not checkout_exists(module_yaml_path):
            continue

        try:
            _sync_module_dir(
                course, entry, None, repo_dir, repo_name, commit_sha, stats,
                known_images, course_dir, course_ignore_patterns, course.slug,
                unit_lookup, seen_module_paths, seen_module_slugs,
                unit_sync_state,
            )
        except Exception as e:
            raise_if_checkout_error(e)
            stats['errors'].append({
                'file': os.path.relpath(module_yaml_path, repo_dir),
                'error': str(e),
            })

    # Issue #1721: the shared "did this walk record any error" gate,
    # evaluated once here reflecting the WHOLE walk (every top-level
    # module and its submodules), guards all three destructive sweeps —
    # the immediate per-module sweep inside ``_sync_module_units``
    # already checked its own fresh snapshot as it went; these two
    # course-end sweeps check the final state. An aborted walk means some
    # unit files were never read, so their identities never entered
    # ``unit_sync_state``/``seen_module_paths`` — reasoning from that
    # incomplete picture is what deleted 31 live units in the #1721
    # incident. Nothing is trusted to reap content while any error from
    # this walk is unresolved.
    walk_has_errors = _count_real_errors(
        stats['errors'], unit_sync_state['errors_at_walk_start'],
    ) > 0

    if not walk_has_errors:
        # Issue #1681: course-end fallback sweep, BEFORE sweeping stale
        # modules — a unit that moved to a new (possibly newly created)
        # module earlier in the walk must already have been reparented off
        # its old module by the time that old module is deleted below, or
        # its ``UserCourseProgress`` would cascade-delete along with it. Most
        # stale units are already gone by now via the per-module immediate
        # sweep inside ``_sync_module_units`` (informed by the course-wide
        # precompute above); this catches what that sweep deliberately left
        # behind — legacy content-hash rename-migration candidates whose
        # target `Unit` row didn't exist yet at immediate-sweep time — using
        # the REAL, now-complete, accumulated course state.
        _cleanup_stale_units_for_course(course, repo_name, stats, unit_sync_state)

        # Remove stale modules (top-level and submodules — seen_module_paths
        # includes every level).
        stale_modules = Module.objects.filter(
            course=course,
            source_repo=repo_name,
        ).exclude(source_path__in=seen_module_paths)
        deleted_count = stale_modules.count()
        stale_modules.delete()
        stats['deleted'] += deleted_count
    else:
        error_count = _count_real_errors(
            stats['errors'], unit_sync_state['errors_at_walk_start'],
        )
        stats['errors'].append({
            'file': course.slug,
            'error': (
                f'Course "{course.title}": the stale-content sweep was '
                f'skipped because {error_count} module/unit error(s) were '
                'recorded during this sync. Nothing was deleted as a '
                'result. Fix the error(s) above and re-sync to clean up '
                'genuinely removed content.'
            ),
        })


def _cleanup_stale_units_for_course(course, repo_name, stats, unit_sync_state):
    """End-of-course fallback sweep for ``Unit`` rows still stale after
    every module directory's own immediate sweep.

    Issue #1681: the immediate per-module sweep inside
    :func:`_sync_module_units` — informed by the course-wide precompute
    built before this course's sync started — already closes the
    directory-walk-order race for the common case (a unit moving between
    modules, with or without a rename, in the same sync: its
    ``content_id``/``source_path`` shows up in the precompute from the
    start, so no module's sweep ever deletes it prematurely). That
    immediate sweep deliberately *defers* one category: a stale unit
    whose ``content_hash`` matches a live file somewhere else in the
    course (a legacy content-hash rename-migration candidate, Edge Case
    1) — its replacement ``Unit`` row may not have been created yet at
    immediate-sweep time, so migrating ``UserCourseProgress`` onto it
    isn't possible until the whole tree has synced. This function runs
    once, after the whole module/submodule tree for the course has
    synced (mirroring the stale-*module* sweep in
    :func:`_sync_course_modules`, which already got this right), using
    the REAL, now-complete, accumulated course state to catch those
    deferred candidates — plus anything else still stale as a safety
    net.

    A unit is stale only if it belongs to this course, its
    ``source_repo`` matches, and neither its ``content_id`` nor its
    ``source_path`` was seen anywhere in the course during this run.
    """
    from content.models import Unit, UserCourseProgress

    stale_units = Unit.objects.filter(
        module__course=course,
        source_repo=repo_name,
    ).exclude(
        source_path__in=unit_sync_state['seen_paths'],
    ).exclude(
        content_id__in=unit_sync_state['content_id_sources'].keys(),
    ).exclude(
        content_id__in=unit_sync_state['failed_content_ids'],
    )

    new_unit_hashes = unit_sync_state['new_hashes']
    for stale_unit in stale_units:
        # Check if a newly created unit anywhere in this course sync run
        # has the same hash (rename detection, Edge Case 1).
        if (stale_unit.content_hash
                and stale_unit.content_hash in new_unit_hashes):
            new_unit = new_unit_hashes[stale_unit.content_hash]
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


def _sync_module_units(module, module_dir, repo_dir, repo_name, commit_sha, stats,
                       known_images=None, course_dir=None,
                       course_ignore_patterns=None,
                       module_ignore_patterns=None,
                       course_slug=None, unit_lookup=None,
                       unit_sync_state=None):
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

    ``unit_sync_state`` (issue #1681) is the course-wide state dict built
    by :func:`_sync_course_modules`: ``seen_paths``/``content_id_sources``
    /``failed_content_ids``/``new_hashes`` accumulate incrementally as
    the real walk proceeds (used for duplicate-content_id detection here
    and, at course-end, by the fallback sweep); ``precomputed_seen_paths``
    /``precomputed_seen_content_ids`` are a dry pre-scan of the WHOLE
    course tree computed once, before any DB
    write this run, and drive the stale-unit sweep at the end of this
    function — deciding staleness against "will this identity be seen
    ANYWHERE in the course this run" rather than this one directory's own
    listing closes the directory-walk-order race where a unit mid-move
    to a different module could otherwise be deleted (cascading its
    ``UserCourseProgress``) before its new module had a chance to
    reparent it, regardless of which module directory is processed
    first. A ``None`` is tolerated (falls back to a call-local dict,
    which disables the stale sweep and duplicate-content_id detection)
    only so this function stays independently callable/testable; every
    real caller passes the shared course-wide state.
    """
    from content.models import Unit, UserCourseProgress
    from content.utils.code_annotations import parse_course_unit_body
    from content.utils.md_links import rewrite_md_links

    course_ignore_patterns = course_ignore_patterns or []
    module_ignore_patterns = module_ignore_patterns or []
    # course_dir defaults to module_dir's parent when not supplied so callers
    # that pre-date this signature still work (course-level patterns become
    # no-ops in that case because course_ignore_patterns is empty).
    if course_dir is None:
        course_dir = os.path.dirname(module_dir)

    if unit_sync_state is None:
        # No course-wide precompute available (standalone/test call) —
        # ``None`` sentinels below disable the stale-unit sweep entirely
        # rather than risk deleting units this call never saw, since an
        # empty precompute set would otherwise look like "nothing is
        # live anywhere" and sweep everything.
        unit_sync_state = {
            'seen_paths': set(),
            'content_id_sources': {},
            'failed_content_ids': set(),
            'new_hashes': {},
            'precomputed_seen_paths': None,
            'precomputed_seen_content_ids': None,
            'errors_at_walk_start': len(stats.get('errors', [])),
        }
    seen_unit_paths = unit_sync_state['seen_paths']
    content_id_sources = unit_sync_state['content_id_sources']
    failed_unit_content_ids = unit_sync_state['failed_content_ids']
    # Track newly created units with their hashes for rename detection
    new_unit_hashes = unit_sync_state['new_hashes']

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
    for name in checkout_listdir(module_dir):
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
                    parent_module_slug=(
                        module.parent.slug if module.parent_id else None
                    ),
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
            raise_if_checkout_error(e)
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

    for filename in checkout_listdir(module_dir):
        if not filename.endswith('.md') or filename.upper() == 'README.MD':
            continue

        # Respect course- and module-level ignore globs.
        if _is_ignored(filename):
            continue

        filepath = os.path.join(module_dir, filename)
        rel_path = os.path.relpath(filepath, repo_dir)
        unit_content_id = None

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

            # Issue #1681: a genuine duplicate content_id (two live files,
            # one id) is an authoring mistake, not a move — never let it
            # reach the model layer as a bare IntegrityError. Track which
            # content_ids have already been claimed by an earlier file
            # THIS run, course-wide; a second file claiming an
            # already-consumed content_id is skipped with a named error
            # (naming both files) instead of attempting the DB write.
            other_rel_path = content_id_sources.get(unit_content_id)
            if other_rel_path is not None and other_rel_path != rel_path:
                msg = (
                    f'Duplicate content_id {unit_content_id!r}: already '
                    f'synced from {other_rel_path!r} this run, also found '
                    f'in {rel_path!r}. Skipped {rel_path}.'
                )
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue
            content_id_sources[unit_content_id] = rel_path

            seen_unit_paths.add(rel_path)

            # Issue #1674: ``kind:`` (lesson/homework/event, case-insensitive).
            # ``is_homework: true`` keeps working as a legacy alias — when
            # ``kind:`` is absent it sets ``kind='homework'`` too. If both
            # are set and disagree, ``kind:`` wins and an info-level note is
            # recorded (same pattern as the existing access/is_preview
            # redundancy note below).
            kind_raw = metadata.get('kind')
            is_homework_flag = bool(metadata.get('is_homework', False))
            if kind_raw is not None:
                kind_key = str(kind_raw).strip().lower()
                if kind_key not in ('lesson', 'homework', 'event'):
                    raise GitHubSyncError(
                        f'Invalid kind in {rel_path}: {kind_raw!r} '
                        "(expected 'lesson', 'homework', or 'event')"
                    )
            else:
                kind_key = 'homework' if is_homework_flag else 'lesson'

            if (
                kind_raw is not None
                and is_homework_flag
                and kind_key != 'homework'
            ):
                stats['errors'].append({
                    'file': rel_path,
                    'severity': 'info',
                    'error': (
                        'Both kind: and is_homework: set on unit and '
                        'disagree; kind: wins. Drop is_homework to keep '
                        'YAML clean.'
                    ),
                })

            # ``is_homework`` continues to mean "route body into
            # Unit.homework instead of Unit.body" — now derived from
            # ``kind`` rather than the raw frontmatter key so ``kind:
            # homework`` alone (no ``is_homework:``) routes correctly too.
            is_homework = kind_key == 'homework'

            session_position_raw = metadata.get('session_position')
            unit_session_position = None
            if kind_key == 'event':
                if session_position_raw is None:
                    raise GitHubSyncError(
                        f'kind: event requires session_position in {rel_path}'
                    )
                try:
                    unit_session_position = int(session_position_raw)
                except (TypeError, ValueError):
                    unit_session_position = None
                if (
                    unit_session_position is None
                    or isinstance(session_position_raw, bool)
                    or unit_session_position < 1
                ):
                    raise GitHubSyncError(
                        f'Invalid session_position in {rel_path}: '
                        f'{session_position_raw!r} (expected a positive '
                        'integer)'
                    )

            unit_is_bonus = bool(metadata.get('is_bonus', False))

            # Validate structured lesson annotations before URL rewriting or
            # any Unit mutation. Keep the path in ``seen_unit_paths`` first:
            # an invalid replacement must preserve the previously published
            # Unit instead of being mistaken for a deleted source file.
            if not is_homework:
                parse_course_unit_body(body)

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
                    parent_module_slug=(
                        module.parent.slug if module.parent_id else None
                    ),
                )

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
                'kind': kind_key,
                'session_position': unit_session_position,
                'is_bonus': unit_is_bonus,
            }

            if is_homework:
                defaults['homework'] = body
            else:
                defaults['body'] = body

            # Issue #1681: content_id is globally unique
            # (SyncedContentIdentityMixin) — it is the unit's stable
            # identity, its current module is not. Resolve course-wide by
            # content_id FIRST so a lesson file that moved to a different
            # module directory (same content_id, new path) is reparented
            # onto the existing row instead of falling through to the
            # create branch and hitting a duplicate-content_id
            # IntegrityError. This is the same "stable id is the
            # identity, current location is not" precedent already used
            # for Course in ``_resolve_course_identity``. Handles a move
            # + rename in the same sync too, since it doesn't depend on
            # slug or path matching.
            unit = Unit.objects.filter(
                content_id=unit_content_id,
                module__course=module.course,
            ).first()
            # Issue #310/#311: fall back to the original module-scoped
            # lookups, unchanged, for legacy rows that predate content_id
            # and for a same-module rename that changes only the
            # filename/slug.
            if unit is None:
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
                try:
                    unit.full_clean()
                except ValidationError as exc:
                    raise GitHubSyncError(
                        f'Invalid unit in {rel_path}: {"; ".join(exc.messages)}'
                    ) from exc
                unit.save()
                created = True
                changed = True
            else:
                # ``unit.module_id != module.pk`` is the reparent case
                # (issue #1681): the unit was found course-wide under a
                # different module than the one being synced. The unit's
                # PK is unchanged either way, so ``UserCourseProgress.
                # unit_id`` FKs follow automatically — no explicit
                # repointing needed.
                identity_changed = (
                    unit.source_path != rel_path
                    or unit.slug != slug
                    or unit.module_id != module.pk
                )
                if identity_changed or _defaults_differ(unit, defaults):
                    unit.source_path = rel_path
                    unit.module = module
                    for k, v in defaults.items():
                        setattr(unit, k, v)
                    try:
                        unit.full_clean()
                    except ValidationError as exc:
                        raise GitHubSyncError(
                            f'Invalid unit in {rel_path}: {"; ".join(exc.messages)}'
                        ) from exc
                    unit.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            # Issue #1683: sync `questions:`/`due_date:` into Homework/
            # Question rows regardless of whether the Unit's own fields
            # changed -- the frontmatter can change (e.g. extending a
            # deadline) without the lesson body/title changing.
            if is_homework:
                sync_unit_homework(unit, module.course, metadata, rel_path, stats)

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
            raise_if_checkout_error(e)
            if unit_content_id:
                failed_unit_content_ids.add(unit_content_id)
            stats['errors'].append({
                'file': rel_path,
                'error': str(e),
            })

    # Remove stale units belonging to THIS module. Issue #1681: staleness
    # is decided against the course-wide precompute (every content_id/
    # source_path that will be seen ANYWHERE in the course this run,
    # computed before any DB write happened) rather than this one
    # directory's own listing — a unit mid-move to a different module
    # already shows up in the precompute from the start, so it's never
    # deleted here regardless of directory-walk order. This also matters
    # for a module transitioning from leaf (direct units) to parent
    # (submodules) in the same sync: its now-orphaned old units must be
    # gone by the time a new submodule's ``full_clean()`` validates
    # "parent has no direct units" a few lines below in
    # ``_sync_module_dir`` — an end-of-course-only sweep would still be
    # attached at that point and wrongly fail the submodule.
    #
    # ``None`` precompute (standalone/test call with no
    # ``unit_sync_state``, see above) skips the sweep outright.
    #
    # Issue #1721: also skip once any module/unit error has been recorded
    # anywhere in this course's walk so far (``walk_has_errors``,
    # evaluated fresh here rather than cached — a module processed
    # cleanly before the first error in the walk still swept normally at
    # its own point in time). An aborted directory walk means some unit
    # files were never read, so their identities never entered the
    # accumulators this sweep — and the course-end fallback sweep below —
    # reason from; sweeping on an error-degraded picture is exactly what
    # deleted 31 live units in the #1721 incident.
    walk_has_errors = _count_real_errors(
        stats['errors'], unit_sync_state['errors_at_walk_start'],
    ) > 0
    if unit_sync_state['precomputed_seen_paths'] is not None and not walk_has_errors:
        stale_units = Unit.objects.filter(
            module=module,
            source_repo=repo_name,
        ).exclude(
            source_path__in=unit_sync_state['precomputed_seen_paths'],
        ).exclude(
            content_id__in=unit_sync_state['precomputed_seen_content_ids'],
        ).exclude(
            content_id__in=failed_unit_content_ids,
        )
        for stale_unit in stale_units:
            # Legacy content-hash rename detection (Edge Case 1): a
            # stale unit with no matching content_id/source_path
            # anywhere, whose content_hash happens to match a unit
            # already created earlier THIS run (course-wide, not just
            # this module — issue #1681), gets its UserCourseProgress
            # migrated before the row is deleted below. If the matching
            # new unit hasn't been created yet (its module hasn't been
            # processed yet in the walk), ``_cleanup_stale_units_for_course``
            # gets a second chance at course-end.
            if (stale_unit.content_hash
                    and stale_unit.content_hash in new_unit_hashes):
                new_unit = new_unit_hashes[stale_unit.content_hash]
                migrated = UserCourseProgress.objects.filter(
                    unit=stale_unit,
                ).update(unit=new_unit)
                if migrated:
                    logger.warning(
                        'Unit appears to have been renamed: %s -> %s, '
                        'migrated %d completion records.',
                        stale_unit.source_path, new_unit.source_path,
                        migrated,
                    )
        deleted_count = stale_units.count()
        stale_units.delete()
        stats['deleted'] += deleted_count
