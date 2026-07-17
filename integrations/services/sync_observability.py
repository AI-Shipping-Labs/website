"""Shared read-only observability for content sync runs.

This module owns the definitions consumed by Studio, the operator API, and
the CLI-facing API.  It deliberately does not enqueue work or mutate sync
records.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import timedelta
from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import Case, Count, F, IntegerField, Max, Min, Q, Value, When, Window
from django.db.models.functions import Coalesce
from django.db.models.functions.window import RowNumber
from django.urls import reverse
from django.utils import timezone

from integrations.models import SyncLog

SYNC_STALE_AFTER_DAYS = 7
SYNC_HISTORY_STATUSES = ('queued', 'running', 'failed', 'partial', 'success', 'skipped')

_STATUS_PRIORITY = {
    'running': 6,
    'queued': 5,
    'failed': 4,
    'partial': 3,
    'success': 2,
    'skipped': 1,
}
_STATUS_BY_PRIORITY = {value: key for key, value in _STATUS_PRIORITY.items()}
_SLUG_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_-])([a-z0-9]+(?:[-_][a-z0-9]+)*)(?![A-Za-z0-9_-])')


def status_label(status):
    """Return the canonical machine/UI label for a logical status."""
    return 'Completed with errors' if status == 'partial' else status


def logical_status(logs):
    """Compute lifecycle status for one logical batch."""
    statuses = {getattr(log, 'status', log) for log in logs}
    for status in ('running', 'queued', 'failed', 'partial'):
        if status in statuses:
            return status
    if statuses and statuses <= {'success', 'skipped'} and 'success' in statuses:
        return 'success'
    if statuses == {'skipped'}:
        return 'skipped'
    return 'success'


def history_id_for_log(log):
    return str(log.batch_id or log.pk)


def _normalise_error(value):
    value = value if isinstance(value, dict) else {'error': value}
    file_name = str(value.get('file') or '').strip()
    message = str(value.get('error') or value.get('message') or '').strip()
    return file_name, message


def _candidate_slugs(line):
    return set(_SLUG_TOKEN_RE.findall(line.lower()))


def structure_errors(raw_errors, *, resolve_targets=False):
    """Normalise and exactly deduplicate stored errors in first-seen order.

    Target resolution always uses at most one query for each supported model,
    irrespective of the number of errors.
    """
    grouped = OrderedDict()
    total_count = 0
    for raw in raw_errors or []:
        pair = _normalise_error(raw)
        total_count += 1
        if pair not in grouped:
            grouped[pair] = {'file': pair[0], 'message': pair[1], 'count': 0, 'target': None}
        grouped[pair]['count'] += 1

    items = list(grouped.values())
    if resolve_targets and items:
        from content.models import Article, Course, Workshop

        item_tokens = [_candidate_slugs(f"{item['file']} {item['message']}") for item in items]
        candidates = set().union(*item_tokens)
        matches = {}
        model_specs = (
            ('article', Article, 'studio_article_edit', 'article_id'),
            ('course', Course, 'studio_course_edit', 'course_id'),
            ('workshop', Workshop, 'studio_workshop_detail', 'workshop_id'),
        )
        for target_type, model, route, kwarg in model_specs:
            for obj in model.objects.filter(slug__in=candidates).only('id', 'slug'):
                target = {
                    'type': target_type,
                    'id': str(obj.pk),
                    'slug': obj.slug,
                    'studio_url': reverse(route, kwargs={kwarg: obj.pk}),
                }
                matches.setdefault(obj.slug.lower(), []).append(target)

        for item, tokens in zip(items, item_tokens):
            resolved = [target for token in tokens for target in matches.get(token, [])]
            if len(resolved) == 1:
                item['target'] = resolved[0]

    return {
        'total_count': total_count,
        'unique_count': len(items),
        'items': items,
    }


def structure_error_groups(raw_error_groups):
    """Structure several error groups with one bounded target lookup pass."""
    groups = list(raw_error_groups)
    resolved = structure_errors(
        [error for group in groups for error in (group or [])],
        resolve_targets=True,
    )
    targets = {
        (item['file'], item['message']): item['target']
        for item in resolved['items']
    }
    result = []
    for group in groups:
        structured = structure_errors(group)
        for item in structured['items']:
            item['target'] = targets.get((item['file'], item['message']))
        result.append(structured)
    return result


def _is_metadata_only_skip(log):
    return (
        log.status == 'skipped'
        and not (log.items_detail or [])
        and not log.items_created
        and not log.items_updated
        and not log.items_unchanged
        and not log.items_deleted
    )


def latest_meaningful_log(logs):
    """Select the same latest useful result used by the Studio dashboard."""
    logs = list(logs)
    if not logs:
        return None
    for log in logs:
        if not _is_metadata_only_skip(log):
            return log
    return logs[0]


def source_health(source, *, now=None, result_log=None, latest_log=None):
    """Derive freshness and latest meaningful errors from a source snapshot."""
    now = now or timezone.now()
    fresh_at = source.last_synced_at
    stale = fresh_at is None or fresh_at < now - timedelta(days=SYNC_STALE_AFTER_DAYS)
    age = max(0, int((now - fresh_at).total_seconds())) if fresh_at else None
    structured = structure_errors((result_log.errors if result_log else []) or [])
    return {
        'status': source.last_sync_status or 'never',
        'status_label': status_label(source.last_sync_status or 'never'),
        'content_fresh_at': fresh_at,
        'content_age_seconds': age,
        'stale': stale,
        'stale_after_days': SYNC_STALE_AFTER_DAYS,
        'latest_history_id': history_id_for_log(latest_log) if latest_log else None,
        'errors_total': structured['total_count'],
        'errors_unique': structured['unique_count'],
    }


def enrich_sources_with_health(sources, *, now=None):
    """Return ``[(source, health, result_log)]`` with bounded bulk queries."""
    now = now or timezone.now()
    sources = list(sources)
    if not sources:
        return []
    source_ids = [source.pk for source in sources]
    terminal_logs = list(
        SyncLog.objects.filter(source_id__in=source_ids)
        .exclude(status__in=('queued', 'running'))
        .annotate(
            source_row=Window(
                expression=RowNumber(),
                partition_by=[F('source_id')],
                order_by=F('started_at').desc(),
            ),
        )
        .filter(source_row__lte=50)
        .select_related('source')
        .order_by('source_id', '-started_at')
    )
    latest_logs = list(
        SyncLog.objects.filter(source_id__in=source_ids)
        .annotate(
            source_row=Window(
                expression=RowNumber(),
                partition_by=[F('source_id')],
                order_by=F('started_at').desc(),
            ),
        )
        .filter(source_row=1)
        .select_related('source')
    )
    by_source = {}
    for log in terminal_logs:
        by_source.setdefault(log.source_id, []).append(log)
    latest_by_source = {log.source_id: log for log in latest_logs}
    enriched = []
    for source in sources:
        result = latest_meaningful_log(by_source.get(source.pk, []))
        latest = latest_by_source.get(source.pk)
        enriched.append((source, source_health(source, now=now, result_log=result, latest_log=latest), result))
    return enriched


def _logical_keys(source=None):
    base = SyncLog.objects.all()
    if source is not None:
        base = base.filter(source=source)
    priority = Case(
        *[When(status=status, then=Value(rank)) for status, rank in _STATUS_PRIORITY.items()],
        default=Value(0),
        output_field=IntegerField(),
    )
    return (
        base.annotate(history_id=Coalesce('batch_id', 'id'))
        .values('history_id')
        .annotate(
            started_at=Min('started_at'),
            finished_at=Max('finished_at'),
            status_priority=Max(priority),
            log_count=Count('id'),
        )
        .order_by('-started_at', '-history_id')
    )


def logical_history_page(*, source=None, status=None, page=1, page_size=50):
    """Page logical keys in SQL, then fetch only logs belonging to that page."""
    keys = _logical_keys(source=source)
    if status:
        keys = keys.filter(status_priority=_STATUS_PRIORITY[status])
    paginator = Paginator(keys, page_size)
    page_obj = paginator.get_page(page)
    rows = list(page_obj.object_list)
    ids = [row['history_id'] for row in rows]
    logs_q = Q(batch_id__in=ids) | Q(batch_id__isnull=True, id__in=ids)
    logs = SyncLog.objects.filter(logs_q)
    if source is not None:
        logs = logs.filter(source=source)
    logs = list(logs.select_related('source').order_by('started_at', 'id'))
    grouped = {str(history_id): [] for history_id in ids}
    for log in logs:
        grouped[str(log.batch_id or log.pk)].append(log)
    return page_obj, [(row, grouped[str(row['history_id'])]) for row in rows]


def logs_for_history_id(history_id, *, source=None):
    """Resolve a batch UUID or singleton log UUID without conflating the two."""
    history_id = UUID(str(history_id))
    batch_logs = SyncLog.objects.filter(batch_id=history_id)
    if source is not None:
        batch_logs = batch_logs.filter(source=source)
    logs = list(batch_logs.select_related('source').order_by('started_at', 'id'))
    if logs:
        return logs
    singleton = SyncLog.objects.filter(pk=history_id, batch_id__isnull=True)
    if source is not None:
        singleton = singleton.filter(source=source)
    return list(singleton.select_related('source'))


def compact_summary(logs, *, include_errors=False, resolve_targets=False):
    """Serialize one logical history item for operator API consumers."""
    logs = list(logs)
    raw_errors = [error for log in logs for error in (log.errors or [])]
    errors = structure_errors(raw_errors, resolve_targets=resolve_targets)
    commits = []
    for log in logs:
        if log.commit_sha and log.commit_sha not in commits:
            commits.append(log.commit_sha)
    source_ids = list(dict.fromkeys(str(log.source_id) for log in logs))
    repo_names = list(dict.fromkeys(log.source.repo_name for log in logs))
    summary = {
        'history_id': history_id_for_log(logs[0]),
        'batch_id': str(logs[0].batch_id) if logs[0].batch_id else None,
        'source_ids': source_ids,
        'repo_names': repo_names,
        'started_at': min(log.started_at for log in logs),
        'finished_at': max((log.finished_at for log in logs if log.finished_at), default=None),
        'status': logical_status(logs),
        'status_label': status_label(logical_status(logs)),
        'log_count': len(logs),
        'commits': commits,
        'counts': {
            'created': sum(log.items_created for log in logs),
            'updated': sum(log.items_updated for log in logs),
            'unchanged': sum(log.items_unchanged for log in logs),
            'deleted': sum(log.items_deleted for log in logs),
        },
        'tiers': {
            'synced': any(log.tiers_synced for log in logs),
            'count': max((log.tiers_count for log in logs), default=0),
        },
        'errors_total': errors['total_count'],
        'errors_unique': errors['unique_count'],
    }
    if include_errors:
        summary['errors'] = errors['items']
        per_type = OrderedDict()
        for log in logs:
            for item in log.items_detail or []:
                content_type = item.get('content_type') or 'other'
                entry = per_type.setdefault(content_type, {
                    'content_type': content_type,
                    'source_ids': [],
                    'status': log.status,
                    'items': [],
                    'counts': {'created': 0, 'updated': 0, 'deleted': 0},
                })
                source_id = str(log.source_id)
                if source_id not in entry['source_ids']:
                    entry['source_ids'].append(source_id)
                entry['status'] = logical_status([entry['status'], log.status])
                entry['items'].append(item)
                action = item.get('action')
                if action in entry['counts']:
                    entry['counts'][action] += 1
        summary['per_type'] = list(per_type.values())
    return summary
