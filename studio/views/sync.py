"""Studio views for content sync management.

Provides:
- /studio/sync/ - Unified sync dashboard with repo-level card and results
- /studio/sync/history/ - Aggregated sync history per batch
- /studio/sync/export/ - Download every ContentSource as a JSON file
- /studio/sync/import/ - Upload a previously-exported file and upsert rows
- /studio/sync/<source_id>/trigger/ - Trigger sync for a single source
- /studio/sync/<repo_name>/trigger-repo/ - Trigger sync for every source
  sharing one repo_name (fan-out under one button, see issue #232)
- /studio/sync/all/ - Trigger sync for all sources (with batch_id)
- /studio/sync/<source_id>/status/ - JSON endpoint for polling sync status
"""

import datetime
import json
import logging
import uuid
from collections import OrderedDict

from django.conf import settings
from django.contrib import messages
from django.db.models import Max, Min
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from integrations.models import ContentSource, SyncLog
from integrations.services import content_sync_queue
from integrations.services.content_sync_queue import (
    enqueue_content_sync,
    enqueue_content_syncs,
)
from studio.decorators import staff_required
from studio.services.content_sources_io import (
    ImportError as ContentSourcesImportError,
)
from studio.services.content_sources_io import (
    apply_import as apply_content_sources_import,
)
from studio.services.content_sources_io import (
    build_export as build_content_sources_export,
)
from studio.worker_health import get_worker_status

logger = logging.getLogger(__name__)


def _mark_source_queued(source, batch_id=None):
    """Compatibility wrapper; queue-state implementation lives in the service."""
    return content_sync_queue._mark_source_queued(source, batch_id=batch_id)


# Error messages used by the watchdog when it auto-fails a stuck SyncLog.
# Pinned as constants so tests can assert on them and operators can grep
# server logs / SyncLog rows for them. See issue #274.
WATCHDOG_QUEUED_ERROR = 'Worker did not pick up task within {minutes} minutes'
WATCHDOG_RUNNING_ERROR = (
    'Worker did not report completion within {minutes} minutes'
)


def _run_sync_watchdog():
    """Inline watchdog: flip stuck queued/running SyncLog rows to ``failed``.

    Called at the top of the dashboard view and the JSON status endpoint
    (issue #274). Runs cheaply: two filtered ``UPDATE``s scoped by status
    and started_at. Per ContentSource, also syncs ``last_sync_status`` so
    the dashboard pill matches the SyncLog row the operator clicks
    through to.

    Two distinct thresholds because the two states mean different things:
    ``queued`` should pick up in seconds (anything > 10min ⇒ broken
    worker); ``running`` is real work that can take 5min, so 30min is a
    generous safety net before we declare it dead.
    """
    now = timezone.now()
    queued_threshold_min = settings.SYNC_QUEUED_THRESHOLD_MINUTES
    running_threshold_min = settings.SYNC_RUNNING_THRESHOLD_MINUTES
    queued_cutoff = now - datetime.timedelta(minutes=queued_threshold_min)
    running_cutoff = now - datetime.timedelta(minutes=running_threshold_min)

    # Stuck queued rows
    stuck_queued = SyncLog.objects.filter(
        status='queued', started_at__lt=queued_cutoff,
    )
    queued_source_ids = list(stuck_queued.values_list('source_id', flat=True))
    if queued_source_ids:
        queued_error = WATCHDOG_QUEUED_ERROR.format(minutes=queued_threshold_min)
        for log in stuck_queued:
            log.status = 'failed'
            log.finished_at = now
            log.errors = (log.errors or []) + [
                {'file': '', 'error': queued_error},
            ]
            log.save(
                update_fields=['status', 'finished_at', 'errors'],
            )
        # Sync corresponding ContentSource.last_sync_status, but only if it
        # still says 'queued' — don't clobber a fresher state.
        ContentSource.objects.filter(
            pk__in=queued_source_ids, last_sync_status='queued',
        ).update(last_sync_status='failed', updated_at=now)

    # Stuck running rows
    stuck_running = SyncLog.objects.filter(
        status='running', started_at__lt=running_cutoff,
    )
    running_source_ids = list(stuck_running.values_list('source_id', flat=True))
    if running_source_ids:
        running_error = WATCHDOG_RUNNING_ERROR.format(
            minutes=running_threshold_min,
        )
        for log in stuck_running:
            log.status = 'failed'
            log.finished_at = now
            log.errors = (log.errors or []) + [
                {'file': '', 'error': running_error},
            ]
            log.save(
                update_fields=['status', 'finished_at', 'errors'],
            )
        ContentSource.objects.filter(
            pk__in=running_source_ids, last_sync_status='running',
        ).update(last_sync_status='failed', updated_at=now)


def _worker_warning_suffix():
    """Return ' — worker not running, sync will not start until you run `manage.py qcluster`.'

    or empty string if the worker is running. Appended to async-task success
    messages so users know the queue is filling up but nothing is processing.
    """
    info = get_worker_status()
    if info['expect_worker'] and not info['alive']:
        return (
            ' — worker is not running, sync will not start until you run '
            '`manage.py qcluster`.'
        )
    return ''


def _is_not_configured_error(errors):
    """Return True if all errors indicate a missing/not-configured source (e.g. no credentials)."""
    if not errors:
        return False
    not_configured_keywords = ['not configured', 'no credentials', 'not set up', 'missing']
    return all(
        any(kw in str(err).lower() for kw in not_configured_keywords)
        for err in errors
    )


_COURSE_LEVELS = (
    ('course', 'Courses'),
    ('module', 'Modules'),
    ('unit', 'Lessons (units)'),
)


def _build_course_level_breakdown(items_detail):
    """Group ``items_detail`` items by content_type for course-type sources.

    For course syncs the flat ``items_detail`` list mixes courses, modules,
    and units. The dashboard renders a per-level row + expandable list for
    each (issue #224), so we pre-compute the grouping in the aggregator
    rather than re-walking the list in the template.
    """
    levels = OrderedDict()
    for level_key, label in _COURSE_LEVELS:
        levels[level_key] = {
            'level': level_key,
            'label': label,
            'created': 0,
            'updated': 0,
            'deleted': 0,
            'items': [],
        }
    for item in items_detail or []:
        ct = item.get('content_type')
        if ct not in levels:
            continue
        action = item.get('action')
        if action == 'created':
            levels[ct]['created'] += 1
        elif action == 'updated':
            levels[ct]['updated'] += 1
        elif action == 'deleted':
            levels[ct]['deleted'] += 1
        levels[ct]['items'].append(item)
    return list(levels.values())


def _new_course_node(course_id=None, course_slug=None, title=None, slug=None):
    return {
        'course_id': course_id,
        'course_slug': course_slug,
        'title': title,
        'slug': slug,
        'action': None,
        'modules_count': 0,
        'lessons_count': 0,
        'modules': OrderedDict(),
    }


def _new_module_node(module_id=None, module_slug=None, course_id=None,
                     title=None, slug=None):
    return {
        'module_id': module_id,
        'module_slug': module_slug,
        'course_id': course_id,
        'title': title,
        'slug': slug,
        'action': None,
        'lessons_count': 0,
        'lessons': [],
    }


def _build_course_tree(items_detail):
    """Build a nested tree (courses → modules → lessons) from ``items_detail``.

    Issue #280: the flat per-level breakdown loses the parent/child
    relationships between course → module → unit. We rebuild that
    hierarchy here so the dashboard can show what really changed: every
    lesson nested under its module, every module nested under its course.

    Items in the batch may not include the parent (e.g. a unit was
    edited but neither its module nor course was touched). Those parents
    are resolved via FK lookups against the current DB so the tree is
    always rooted in real courses, never in orphan synthetic nodes.

    Returns a list of course-node dicts. Each course node has:
        - course_id, course_slug, title, slug, action (or None if not in batch)
        - modules_count, lessons_count
        - modules: list of module-node dicts (each with lessons list)

    Items whose course can't be resolved (deleted / missing FK) end up
    in a synthetic "Other" group so they're not silently dropped.
    """
    # Lazy-import to avoid an import cycle at app load.
    from content.models import Course, Module

    courses_in_batch = []
    modules_in_batch = []
    units_in_batch = []
    for item in items_detail or []:
        ct = item.get('content_type')
        if ct == 'course':
            courses_in_batch.append(item)
        elif ct == 'module':
            modules_in_batch.append(item)
        elif ct == 'unit':
            units_in_batch.append(item)

    # Resolve missing module → course parents via DB.
    module_ids_needing_course = {
        m.get('module_id') for m in modules_in_batch
        if m.get('module_id') and not m.get('course_id')
    }
    unit_module_ids = {
        u.get('module_id') for u in units_in_batch if u.get('module_id')
    }
    # We need module → course mapping for any unit whose module isn't
    # itself in the batch (or whose `course_id` is missing).
    module_ids_for_units = {
        u.get('module_id') for u in units_in_batch
        if u.get('module_id') and not u.get('course_id')
    }
    module_lookup_ids = (
        module_ids_needing_course | module_ids_for_units | unit_module_ids
    )
    module_lookup_ids.discard(None)
    module_to_course = {}
    module_meta = {}
    if module_lookup_ids:
        for m in Module.objects.filter(pk__in=module_lookup_ids).values(
            'pk', 'course_id', 'title', 'slug', 'course__slug',
        ):
            module_to_course[m['pk']] = m['course_id']
            module_meta[m['pk']] = {
                'title': m['title'],
                'slug': m['slug'],
                'course_id': m['course_id'],
                'course_slug': m['course__slug'],
            }

    # Resolve any course ids we still need metadata for (course rows that
    # weren't in the batch but were referenced by a module/unit).
    needed_course_ids = set()
    for c in courses_in_batch:
        if c.get('course_id'):
            needed_course_ids.add(c['course_id'])
    for m in modules_in_batch:
        cid = m.get('course_id') or module_to_course.get(m.get('module_id'))
        if cid:
            needed_course_ids.add(cid)
    for u in units_in_batch:
        cid = u.get('course_id') or module_to_course.get(u.get('module_id'))
        if cid:
            needed_course_ids.add(cid)
    needed_course_ids.discard(None)

    course_meta = {}
    if needed_course_ids:
        for c in Course.objects.filter(pk__in=needed_course_ids).values(
            'pk', 'title', 'slug',
        ):
            course_meta[c['pk']] = {'title': c['title'], 'slug': c['slug']}

    courses = OrderedDict()  # course_id -> course node
    orphan = _new_course_node(
        course_id=None, course_slug=None,
        title='Other (parent course not found)', slug='',
    )

    def _ensure_course(course_id, course_slug=None, title=None, slug=None):
        if course_id is None:
            return orphan
        node = courses.get(course_id)
        if node is None:
            meta = course_meta.get(course_id, {})
            node = _new_course_node(
                course_id=course_id,
                course_slug=course_slug or meta.get('slug') or '',
                title=title or meta.get('title') or f'Course #{course_id}',
                slug=slug or meta.get('slug') or '',
            )
            courses[course_id] = node
        else:
            # Fill in missing fields opportunistically.
            if not node['title'] and (title or course_meta.get(course_id)):
                node['title'] = title or course_meta[course_id]['title']
            if not node['course_slug']:
                node['course_slug'] = (
                    course_slug or course_meta.get(course_id, {}).get('slug', '')
                )
        return node

    def _ensure_module(course_node, module_id, module_slug=None,
                       title=None, slug=None):
        if module_id is None:
            # Synthetic per-course "loose lessons" bucket.
            key = '__loose__'
        else:
            key = module_id
        node = course_node['modules'].get(key)
        if node is None:
            meta = module_meta.get(module_id, {}) if module_id else {}
            node = _new_module_node(
                module_id=module_id,
                module_slug=module_slug or meta.get('slug') or '',
                course_id=course_node['course_id'],
                title=title or meta.get('title') or (
                    'Other lessons (parent module not in batch)'
                    if module_id is None else f'Module #{module_id}'
                ),
                slug=slug or meta.get('slug') or '',
            )
            course_node['modules'][key] = node
        else:
            if not node['title'] and title:
                node['title'] = title
            if not node['slug'] and slug:
                node['slug'] = slug
        return node

    # 1) Add course-level items (these set the course node's own action).
    for item in courses_in_batch:
        cid = item.get('course_id')
        node = _ensure_course(
            course_id=cid,
            course_slug=item.get('course_slug'),
            title=item.get('title'),
            slug=item.get('slug'),
        )
        node['action'] = item.get('action')

    # 2) Add module-level items.
    for item in modules_in_batch:
        mid = item.get('module_id')
        cid = item.get('course_id') or module_to_course.get(mid)
        course_node = _ensure_course(
            course_id=cid,
            course_slug=item.get('course_slug'),
        )
        mnode = _ensure_module(
            course_node,
            module_id=mid,
            module_slug=item.get('module_slug') or item.get('slug'),
            title=item.get('title'),
            slug=item.get('slug'),
        )
        mnode['action'] = item.get('action')
        course_node['modules_count'] += 1

    # 3) Add unit-level items, attaching to (and creating) module + course
    # placeholders as needed.
    for item in units_in_batch:
        mid = item.get('module_id')
        cid = item.get('course_id') or module_to_course.get(mid)
        course_node = _ensure_course(
            course_id=cid,
            course_slug=item.get('course_slug'),
        )
        # Module title/slug for the lesson's parent module — preferred from
        # the item itself, fall back to DB-resolved metadata.
        mmeta = module_meta.get(mid, {})
        mnode = _ensure_module(
            course_node,
            module_id=mid,
            module_slug=item.get('module_slug') or mmeta.get('slug'),
            title=mmeta.get('title'),
            slug=item.get('module_slug') or mmeta.get('slug'),
        )
        mnode['lessons'].append({
            'unit_id': item.get('unit_id'),
            'title': item.get('title'),
            'slug': item.get('slug'),
            'action': item.get('action'),
        })
        mnode['lessons_count'] += 1
        course_node['lessons_count'] += 1

    # Flatten OrderedDict children to lists for stable, deterministic
    # iteration in templates.
    result = []
    for course_node in courses.values():
        course_node['modules'] = list(course_node['modules'].values())
        result.append(course_node)
    if orphan['modules']:
        orphan['modules'] = list(orphan['modules'].values())
        result.append(orphan)
    return result


# Display labels for content_type values surfaced in ``items_detail``.
# Was previously sourced from ``ContentSource.get_content_type_display``,
# but ``content_type`` is no longer on ``ContentSource`` (issue #310). The
# per-type dispatch helpers in :mod:`integrations.services.github` write
# the content_type into each ``items_detail`` entry, so the dashboard
# aggregator can group on those keys directly.
_CONTENT_TYPE_DISPLAY = {
    'article': 'Article',
    'course': 'Course',
    'module': 'Module',
    'unit': 'Unit',
    'project': 'Project',
    'resource': 'Resource',
    'event': 'Event',
    'workshop': 'Workshop',
    'workshop_page': 'Workshop Page',
    'instructor': 'Instructor',
    'interview_question': 'Interview Question',
}


def _aggregate_batch(logs):
    """Aggregate a queryset of SyncLog entries into a batch summary dict.

    The aggregate dict surfaces ``errors_count`` on every per-type row and at
    the batch level so the sync-status pill (template tag
    ``sync_status_pill``) can render ``Completed with N errors`` for partial
    syncs without dipping back into the SyncLog. See issue #245.

    For ``content_type='course'`` rows, the aggregator also attaches a
    ``course_breakdown`` list — one entry per level (courses, modules,
    units) with its own counts and item list — so the dashboard can render
    the per-level breakdown and expandable changed-pages list (issue #224).

    Issue #310: per-content-type breakdown is now driven by
    ``items_detail.content_type`` rather than ``log.source.content_type``,
    since one SyncLog can now contain items of many types.
    """
    total_created = 0
    total_updated = 0
    total_unchanged = 0
    total_deleted = 0
    all_errors = []
    per_type = OrderedDict()
    tiers_synced = False
    tiers_count = 0
    # Track the overall status. ``None`` means we haven't seen any logs
    # yet; once we have, ``skipped``/``success`` get demoted by a worse
    # status (failed > partial > success/skipped).
    overall_status = None
    seen_non_skipped = False

    def _ensure_entry(content_type, log):
        display = _CONTENT_TYPE_DISPLAY.get(
            content_type, content_type.replace('_', ' ').title(),
        )
        if display in per_type:
            return per_type[display]
        per_type[display] = {
            'content_type': content_type,
            'display_name': display,
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'deleted': 0,
            'status': log.status,
            'errors_count': 0,
            'items_detail': [],
            'commit_sha': log.commit_sha or '',
            'short_commit_sha': log.short_commit_sha,
            'commit_url': log.commit_url,
            'is_skipped': log.status == 'skipped',
        }
        return per_type[display]

    for log in logs:
        # Group items by their content_type field. Each item bumps the
        # appropriate per-type bucket; items without a content_type fall
        # under "Other" so nothing is silently dropped.
        items_by_type = OrderedDict()
        for item in log.items_detail or []:
            ct = item.get('content_type') or 'other'
            items_by_type.setdefault(ct, []).append(item)

        # If the log produced no items at all (skipped, all-unchanged,
        # error before any work), still surface a stub row so the
        # dashboard / history doesn't render a blank table.
        if not items_by_type:
            entry = _ensure_entry('other', log)
            entry['unchanged'] += log.items_unchanged

        for ct, items in items_by_type.items():
            entry = _ensure_entry(ct, log)
            for item in items:
                action = item.get('action')
                if action == 'created':
                    entry['created'] += 1
                elif action == 'updated':
                    entry['updated'] += 1
                elif action == 'deleted':
                    entry['deleted'] += 1
            entry['items_detail'].extend(items)
            if log.status != 'skipped':
                # Errors are recorded at the log level, not per-item;
                # distribute them evenly. Easier: count them once at the
                # batch level (handled below) and on each per-type row.
                pass

        # Per-log error count distributed to every per-type row that saw
        # work in this log. With one source per repo this is fine —
        # before the consolidation each log mapped 1:1 to a content type
        # and the count was simply per-row. Now we apportion to every
        # row that received items, OR to a synthetic 'other' row if the
        # log didn't produce items but did produce errors.
        if log.status != 'skipped' and log.errors:
            err_count = len(log.errors)
            if items_by_type:
                for ct in items_by_type:
                    display = _CONTENT_TYPE_DISPLAY.get(
                        ct, ct.replace('_', ' ').title(),
                    )
                    if display in per_type:
                        per_type[display]['errors_count'] += err_count
            else:
                entry = _ensure_entry('other', log)
                entry['errors_count'] += err_count

        if log.status == 'failed':
            for entry in per_type.values():
                entry['status'] = 'failed'
        elif log.status == 'partial':
            for entry in per_type.values():
                if entry['status'] != 'failed':
                    entry['status'] = 'partial'

        total_created += log.items_created
        total_updated += log.items_updated
        total_unchanged += log.items_unchanged
        total_deleted += log.items_deleted
        if log.status != 'skipped':
            all_errors.extend(log.errors or [])

        if log.tiers_synced:
            tiers_synced = True
            tiers_count = log.tiers_count

        if log.status == 'failed':
            overall_status = 'failed'
        elif log.status == 'partial' and overall_status != 'failed':
            overall_status = 'partial'
        elif log.status == 'success' and overall_status not in (
            'failed', 'partial',
        ):
            overall_status = 'success'
            seen_non_skipped = True
        elif log.status == 'skipped' and overall_status is None:
            # Stays as 'skipped' unless a later (worse) log overrides.
            overall_status = 'skipped'

    # If every log in the batch was a skip, surface that to the pill;
    # otherwise default to success when nothing worse happened.
    if overall_status is None:
        overall_status = 'success'
    elif overall_status == 'skipped' and seen_non_skipped:
        overall_status = 'success'

    # Compute course-level breakdown (issue #224) and tree (issue #280)
    # for course-type sources. The tree shows what really changed by
    # nesting modules under their course and lessons under their module,
    # while the legacy flat breakdown stays for callers/tests that still
    # expect it.
    #
    # Issue #310: ``items_detail`` for course/module/unit entries lives
    # in separate per_type rows (one per content_type) since the
    # aggregator now buckets by content_type. Combine them all into the
    # ``Course`` row (creating it if missing) so the dashboard's course
    # tree still finds every node.
    has_course_data = any(
        per_type[d]['items_detail']
        for d in (
            _CONTENT_TYPE_DISPLAY['course'],
            _CONTENT_TYPE_DISPLAY['module'],
            _CONTENT_TYPE_DISPLAY['unit'],
        )
        if d in per_type
    )
    if has_course_data:
        course_display = _CONTENT_TYPE_DISPLAY['course']
        if course_display not in per_type:
            # Synthesize a course entry so the tree has a host.
            from types import SimpleNamespace
            stub = SimpleNamespace(
                status='success', commit_sha='', short_commit_sha='',
                commit_url='',
            )
            _ensure_entry('course', stub)
        # Move the course entry to the front of per_type so the tree
        # is index 0 — tests rely on this position.
        course_entry = per_type.pop(course_display)
        new_per_type = OrderedDict()
        new_per_type[course_display] = course_entry
        for k, v in per_type.items():
            new_per_type[k] = v
        per_type.clear()
        per_type.update(new_per_type)

    for entry in per_type.values():
        if entry['content_type'] == 'course':
            combined = list(entry['items_detail'])
            for ct_key in ('module', 'unit'):
                display = _CONTENT_TYPE_DISPLAY[ct_key]
                if display in per_type:
                    combined.extend(per_type[display]['items_detail'])
            entry['course_breakdown'] = _build_course_level_breakdown(combined)
            entry['course_tree'] = _build_course_tree(combined)

    return {
        'repo_names': [log.source.repo_name for log in logs],
        'total_created': total_created,
        'total_updated': total_updated,
        'total_unchanged': total_unchanged,
        'total_deleted': total_deleted,
        'errors': all_errors,
        'errors_count': len(all_errors),
        'per_type': list(per_type.values()),
        'tiers_synced': tiers_synced,
        'tiers_count': tiers_count,
        'overall_status': overall_status,
    }


def _is_metadata_only_skip(log):
    """Return True for HEAD-unchanged skips that contain no content results."""
    return (
        log.status == 'skipped'
        and not (log.items_detail or [])
        and not log.items_created
        and not log.items_updated
        and not log.items_unchanged
        and not log.items_deleted
    )


def _latest_result_log_for_dashboard(latest_logs):
    """Pick the newest log with content details for the dashboard table.

    HEAD-unchanged skips only prove the repo is current; they do not carry
    content-type detail rows. Keep the current skipped status on the card, but
    render the most recent real sync results instead of a synthetic "Other" row.
    """
    logs = list(latest_logs[:50])
    if not logs:
        return None

    newest = logs[0]
    if not _is_metadata_only_skip(newest):
        return newest

    for log in logs[1:]:
        if not _is_metadata_only_skip(log):
            return log
    return newest


def _build_repos_context():
    """Build the per-repo dashboard payload (cards + last batches).

    Issue #310: with one ContentSource per repo, the per-source loop is
    trivial — each card maps 1:1 to a single source.
    """
    sources = ContentSource.objects.all().order_by('repo_name')

    repos = OrderedDict()
    for source in sources:
        repo = {
            'repo_name': source.repo_name,
            'source': source,
            'is_private': source.is_private,
            'last_synced_at': source.last_synced_at,
            'any_running': source.last_sync_status in ('running', 'queued'),
            'overall_status': None,
            'last_synced_commit': source.last_synced_commit or '',
            'short_synced_commit': source.short_synced_commit,
            'synced_commit_url': source.synced_commit_url,
        }
        if source.last_sync_status == 'failed':
            last_log = (
                SyncLog.objects.filter(source=source)
                .order_by('-started_at').first()
            )
            log_errors = (last_log.errors if last_log else []) or []
            if not _is_not_configured_error(log_errors):
                repo['overall_status'] = 'failed'
            else:
                repo['overall_status'] = source.last_sync_status
        elif source.last_sync_status:
            repo['overall_status'] = source.last_sync_status
        repos[source.repo_name] = repo

    # Get the most recent batch of sync logs for each repo (one source per repo).
    for repo in repos.values():
        source = repo['source']
        latest_logs = SyncLog.objects.filter(
            source=source,
        ).exclude(status__in=['running', 'queued']).order_by('-started_at')

        newest = _latest_result_log_for_dashboard(latest_logs)
        if newest:
            if newest.batch_id:
                batch_logs = SyncLog.objects.filter(
                    batch_id=newest.batch_id,
                    source=source,
                )
            else:
                # Single log per source for non-batch syncs.
                batch_logs = SyncLog.objects.filter(pk=newest.pk)
            repo['last_batch'] = _aggregate_batch(batch_logs)
        else:
            repo['last_batch'] = None

        repo['overall_errors_count'] = (
            repo['last_batch']['errors_count'] if repo['last_batch'] else 0
        )

    repos_list = list(repos.values())
    return {
        'repos': repos_list,
        'sources': sources,
        'any_running': any(r['any_running'] for r in repos_list),
    }


@staff_required
def sync_dashboard(request):
    """Display unified sync dashboard with one card per repo.

    Supports a ``?fragment=status`` query param that returns just the
    per-repo cards partial. Used by the lightweight JS poller (issue #243)
    so a row that finishes syncing flips from ``running`` to its final
    status without the operator having to refresh the page.

    Runs the inline watchdog (issue #274) at the top of every render so
    rows stuck in ``queued`` (worker never picked up) or ``running``
    (worker died mid-sync) get flipped to ``failed`` rather than
    silently lying about being in flight.
    """
    _run_sync_watchdog()
    context = _build_repos_context()

    if request.GET.get('fragment') == 'status':
        # Auto-refresh endpoint: just the cards section, no chrome.
        return render(request, 'studio/sync/_repos_section.html', context)

    return render(request, 'studio/sync/dashboard.html', context)


@staff_required
def sync_history(request, source_id=None):
    """Display aggregated sync history per batch."""
    sources = ContentSource.objects.all()
    source_ids = [s.pk for s in sources]

    # Get all sync logs, grouped by batch
    all_logs = SyncLog.objects.filter(
        source_id__in=source_ids,
    ).select_related('source').order_by('-started_at')[:200]

    # Group by batch_id or by timestamp proximity
    batches = []
    seen_batch_ids = set()
    seen_log_ids = set()

    for log in all_logs:
        if log.pk in seen_log_ids:
            continue

        if log.batch_id and log.batch_id not in seen_batch_ids:
            seen_batch_ids.add(log.batch_id)
            batch_logs = SyncLog.objects.filter(
                batch_id=log.batch_id,
            ).select_related('source')
            for bl in batch_logs:
                seen_log_ids.add(bl.pk)
            agg = _aggregate_batch(batch_logs)
            agg['started_at'] = batch_logs.aggregate(
                min_start=Min('started_at'),
            )['min_start']
            agg['finished_at'] = batch_logs.aggregate(
                max_finish=Max('finished_at'),
            )['max_finish']
            agg['batch_id'] = str(log.batch_id)
            agg['log_count'] = batch_logs.count()
            batches.append(agg)
        elif not log.batch_id:
            seen_log_ids.add(log.pk)
            agg = _aggregate_batch([log])
            agg['started_at'] = log.started_at
            agg['finished_at'] = log.finished_at
            agg['batch_id'] = None
            agg['log_count'] = 1
            batches.append(agg)

    return render(request, 'studio/sync/history.html', {
        'batches': batches[:50],
    })


def _force_flag(request):
    """Read the ``force`` flag from a Studio sync POST.

    Accepts truthy strings (``"1"``, ``"true"``, ``"on"``) so the form can
    use either a hidden input or a checkbox. The default is False so the
    HEAD-SHA skip check (issue #235) stays opt-out.
    """
    raw = (request.POST.get('force') or '').strip().lower()
    return raw in ('1', 'true', 'on', 'yes')


@staff_required
@require_POST
def sync_trigger(request, source_id):
    """Trigger a sync for a single content source.

    Redirects back to ``/studio/sync/`` so the operator stays on the sync
    dashboard and can see the inline indicator update. The flash message
    includes a link to ``/studio/worker/`` for operators who want to watch
    the job land in the queue. See issue #239.

    If the POST includes ``force=1`` (issue #235's "Force resync" button),
    the sync bypasses the HEAD-SHA skip check.
    """
    source = get_object_or_404(ContentSource, pk=source_id)
    force = _force_flag(request)

    result = enqueue_content_sync(source, force=force)
    if result.ok and result.queued:
        warning = _worker_warning_suffix()
        verb = 'Force resync queued' if force else 'Sync queued'
        base_msg = format_html(
            '{verb} for {label}. You can see the status '
            '<a href="/studio/worker/" class="underline">here</a>{warning}',
            verb=verb,
            label=source.repo_name,
            warning=warning,
        )
        if warning:
            messages.warning(request, base_msg)
        else:
            messages.success(request, base_msg)
    elif result.ok and result.ran_inline:
        messages.success(
            request,
            f'Sync completed for {source.repo_name}',
        )
    else:
        logger.error(
            'Error triggering sync for %s: %s',
            source.repo_name,
            result.error,
        )
        messages.error(
            request,
            result.message,
        )

    return redirect('studio_sync_dashboard')


@staff_required
@require_POST
def sync_repo_trigger(request, repo_name):
    """Trigger sync for every ContentSource sharing one ``repo_name``.

    The dashboard renders one ``Sync now`` button per repo card. Clicking it
    fans out an ``async_task`` for every ContentSource with that repo name,
    all sharing one ``batch_id`` so the batch shows up as a single row in
    history and the per-card ``last_batch`` aggregator finds them together.
    See issue #232.
    """
    sources = list(ContentSource.objects.filter(repo_name=repo_name))
    if not sources:
        messages.error(request, f'No content sources configured for {repo_name}.')
        return redirect('studio_sync_dashboard')

    batch_id = uuid.uuid4()
    count = len(sources)
    force = _force_flag(request)

    results = enqueue_content_syncs(
        sources,
        batch_id=batch_id,
        force=force,
    )
    for result in results:
        if not result.ok:
            logger.error(
                'Error triggering sync for %s: %s',
                result.source.repo_name,
                result.error,
            )

    warning = _worker_warning_suffix()
    verb = 'Force resync queued' if force else 'Sync queued'
    base_msg = format_html(
        '{verb} for {repo_name} ({count} source{plural}). You can see '
        'the status <a href="/studio/worker/" class="underline">here</a>'
        '{warning}',
        verb=verb,
        repo_name=repo_name,
        count=count,
        plural='' if count == 1 else 's',
        warning=warning,
    )
    if warning:
        messages.warning(request, base_msg)
    else:
        messages.success(request, base_msg)

    return redirect('studio_sync_dashboard')


@staff_required
@require_POST
def sync_all(request):
    """Trigger sync for all content sources with a shared batch_id.

    Redirects back to ``/studio/sync/`` so the operator stays on the sync
    dashboard and watches every per-source row update in place. The flash
    message includes a link to ``/studio/worker/`` for operators who want
    to watch the batch flow through the queue. See issue #239.
    """
    sources = ContentSource.objects.all()
    count = sources.count()
    batch_id = uuid.uuid4()
    force = _force_flag(request)

    results = enqueue_content_syncs(
        sources,
        batch_id=batch_id,
        force=force,
    )
    for result in results:
        if not result.ok:
            logger.error(
                'Error triggering sync for %s: %s',
                result.source.repo_name,
                result.error,
            )

    warning = _worker_warning_suffix()
    verb = 'Force resync queued' if force else 'Sync queued'
    base_msg = format_html(
        '{verb} for {count} source{plural}. You can see the status '
        '<a href="/studio/worker/" class="underline">here</a>{warning}',
        verb=verb,
        count=count,
        plural='' if count == 1 else 's',
        warning=warning,
    )
    if warning:
        messages.warning(request, base_msg)
    else:
        messages.success(request, base_msg)
    return redirect('studio_sync_dashboard')


@staff_required
def sync_status(request, source_id):
    """JSON endpoint returning current sync status for a source (for polling).

    Runs the watchdog (issue #274) at the top so JS pollers — even on
    surfaces other than the dashboard fragment — see ``failed`` for stuck
    rows instead of stale ``queued``/``running``.
    """
    _run_sync_watchdog()
    source = get_object_or_404(ContentSource, pk=source_id)
    # Re-fetch in case the watchdog just flipped it.
    source.refresh_from_db()
    return JsonResponse({
        'id': str(source.pk),
        'last_sync_status': source.last_sync_status,
        'last_synced_at': source.last_synced_at.isoformat() if source.last_synced_at else None,
    })


# Allowlist of model_name URL parameters mapped to (app_label, ModelClassName).
# The Re-sync source button on Studio detail pages POSTs to a URL containing
# the model_name. We resolve the model via Django's app registry rather than
# importing every model up-front — keeps studio/views/sync.py free of cross-app
# imports. Synced models only; the workshop_page entry exists because workshop
# pages now carry source metadata directly, while older rows can still be
# driven from the parent workshop surface. Issue #281/#388.
_OBJECT_TRIGGER_MODEL_ALLOWLIST = {
    'article': ('content', 'Article'),
    'course': ('content', 'Course'),
    'module': ('content', 'Module'),
    'unit': ('content', 'Unit'),
    'project': ('content', 'Project'),
    'download': ('content', 'Download'),
    'curatedlink': ('content', 'CuratedLink'),
    'event': ('events', 'Event'),
    'recording': ('events', 'Event'),
    'interviewcategory': ('content', 'InterviewCategory'),
    'workshop': ('content', 'Workshop'),
    'workshoppage': ('content', 'WorkshopPage'),
}


def _safe_redirect_target(request, fallback_url):
    """Return a same-host HTTP_REFERER if present, otherwise ``fallback_url``.

    Mirrors the standard "redirect back to where the user came from" pattern
    but defends against open-redirect by only honouring referers from the
    same host. ``fallback_url`` is an already-resolved URL string.
    """
    referer = request.META.get('HTTP_REFERER', '')
    if not referer:
        return fallback_url
    try:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
    except Exception:
        return fallback_url
    # Empty netloc means a relative URL — same-host by definition.
    if not parsed.netloc:
        return referer
    request_host = request.get_host()
    if parsed.netloc == request_host:
        return referer
    return fallback_url


@staff_required
@require_POST
def sync_object_trigger(request, model_name, object_id):
    """Trigger a re-sync from a Studio detail page's "Re-sync source" button.

    Resolves the matching ``ContentSource`` from the object's ``source_repo``
    and enqueues the same async task used by the dashboard ``Sync now``
    button. The button is rendered by origin components only when the
    object has a ``source_repo``, but the view still guards each
    precondition so a hand-crafted POST cannot crash the server. See
    issue #281.

    Redirects back to the page that triggered the click (same-host
    ``HTTP_REFERER``), falling back to ``/studio/sync/`` so the operator can
    watch progress on the dashboard.
    """
    from django.apps import apps

    fallback_url = redirect('studio_sync_dashboard').url

    key = (model_name or '').lower()
    entry = _OBJECT_TRIGGER_MODEL_ALLOWLIST.get(key)
    if entry is None:
        # Unknown / not-allowlisted model — 404 to mirror the
        # ``get_object_or_404`` behaviour for a missing object.
        from django.http import Http404
        raise Http404(f'Unknown model: {model_name!r}')

    app_label, class_name = entry
    try:
        Model = apps.get_model(app_label, class_name)
    except LookupError:
        from django.http import Http404
        raise Http404(f'Model not registered: {app_label}.{class_name}')

    obj = get_object_or_404(Model, pk=object_id)

    source_repo = getattr(obj, 'source_repo', '') or ''
    if not source_repo:
        messages.error(
            request,
            'This object has no source_repo set, so it cannot be re-synced. '
            'Manually-created content is not synced from GitHub.',
        )
        return redirect(_safe_redirect_target(request, fallback_url))

    try:
        source = ContentSource.objects.get(repo_name=source_repo)
    except ContentSource.DoesNotExist:
        messages.error(
            request,
            f'No content source is configured for {source_repo}. '
            'Add one under Sync Dashboard before re-syncing this object.',
        )
        return redirect(_safe_redirect_target(request, fallback_url))

    result = enqueue_content_sync(source)
    if result.ok and result.queued:
        warning = _worker_warning_suffix()
        base_msg = format_html(
            'Sync queued for {repo}. Watch progress at '
            '<a href="/studio/sync/" class="underline">/studio/sync/</a>'
            '{warning}',
            repo=source.repo_name,
            warning=warning,
        )
        if warning:
            messages.warning(request, base_msg)
        else:
            messages.success(request, base_msg)
    elif result.ok and result.ran_inline:
        messages.success(
            request,
            f'Sync completed for {source.repo_name}.',
        )
    else:
        logger.error(
            'Error triggering object re-sync for %s: %s',
            source.repo_name,
            result.error,
        )
        messages.error(
            request,
            f'Re-sync failed for {source.repo_name}: {result.error}',
        )

    return redirect(_safe_redirect_target(request, fallback_url))


@staff_required
def content_sources_export(request):
    """Download every ContentSource row as a JSON file (issue #436).

    Plaintext on purpose — the export contains webhook secrets so the
    operator can bootstrap a fresh environment with one upload. The view
    layer surfaces a sensitivity disclaimer to the operator both in-page
    and in the success flash. ``staff_required`` is the only gate; the
    operator is trusted to handle the file like a password manager export.
    """
    payload = build_content_sources_export()
    body = json.dumps(payload, indent=2, sort_keys=False)
    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = f'aishippinglabs-content-sources-{timestamp}.json'
    response = HttpResponse(body, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@staff_required
@require_POST
def content_sources_import(request):
    """Upsert ContentSource rows from a previously-exported JSON upload.

    Validation: malformed JSON and unknown ``format_version`` are rejected
    with a flash error and no DB writes. Entries with missing or invalid
    ``repo_name`` are skipped and surfaced as a warning so a partial file
    still bootstraps the rest. The import does not trigger a sync — the
    operator runs sync separately from the dashboard.

    The error-handling branches surface only the type of failure (e.g.
    ``'JSON parse error'``) through ``messages``; the payload contents
    are never logged.
    """
    upload = request.FILES.get('content_sources_file')
    if upload is None:
        messages.error(
            request,
            'No file uploaded. Pick a content sources JSON file and try again.',
        )
        return redirect('studio_sync_dashboard')

    try:
        raw = upload.read().decode('utf-8')
    except UnicodeDecodeError:
        messages.error(
            request,
            'Content sources file must be UTF-8 encoded JSON.',
        )
        return redirect('studio_sync_dashboard')

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        messages.error(
            request,
            f'Content sources file is not valid JSON: {exc.msg} (line {exc.lineno}).',
        )
        return redirect('studio_sync_dashboard')

    try:
        result = apply_content_sources_import(payload)
    except ContentSourcesImportError as exc:
        messages.error(request, str(exc))
        return redirect('studio_sync_dashboard')

    if result.created or result.updated:
        messages.success(
            request,
            f'Content sources imported ({result.created} created, '
            f'{result.updated} updated). Treat any exported file as '
            'sensitive — it contains webhook secrets.',
        )
    else:
        messages.info(
            request,
            'Content sources file contained no recognised entries.',
        )

    if result.skipped_repos:
        messages.warning(
            request,
            'Skipped entries with missing or invalid repo_name: '
            + ', '.join(result.skipped_repos)
            + '.',
        )

    return redirect('studio_sync_dashboard')
