"""Shared enqueue service for GitHub content sync tasks (A2.3).

Queues through the package durable dispatcher
(``cb_content_sync.sync_source`` via ``dispatch_after_commit``) and writes
the queued marker row into the package ``SyncLog`` table so operator
surfaces keep showing the queued state. No second engine exists.
"""

from dataclasses import dataclass

from community_base.content_sync.models import ContentSource as PackageContentSource
from community_base.content_sync.models import SyncLog
from community_base.content_sync.queue import queue_source_sync as package_queue_source_sync
from django.db import transaction

SYNC_TASK_PATH = 'integrations.services.github.sync_content_source'


@dataclass(frozen=True)
class ContentSyncQueueResult:
    """Structured outcome from enqueueing or running a content sync."""

    ok: bool
    queued: bool
    ran_inline: bool
    source: object
    batch_id: object = None
    message: str = ''
    error: str = ''
    task_id: object = None


def _package_source(source):
    """Resolve a legacy or package source row to the package row.

    The P6 migration preserves primary keys, so a pk lookup covers both
    the legacy ``integrations.ContentSource`` rows and package rows.
    """
    if isinstance(source, PackageContentSource):
        return source
    return PackageContentSource.objects.get(pk=source.pk)


def _mark_source_queued(package_source, batch_id=None):
    """Create the queued SyncLog row and mark the source as queued."""
    queued_log = SyncLog.objects.create(
        source=package_source,
        batch_id=batch_id,
        status='queued',
    )
    PackageContentSource.objects.filter(pk=package_source.pk).update(
        last_sync_status='queued',
    )
    return queued_log


def enqueue_content_sync(
    source,
    batch_id=None,
    force=False,
    mark_queued=True,
    task_name=None,
    task_source='content sync queue',
):
    """Queue one content source sync through the package durable dispatcher.

    ``task_name`` and ``task_source`` are accepted for signature
    compatibility with legacy callers; the package dispatcher owns task
    naming and deduplication.
    """
    del task_name, task_source

    try:
        package_source = _package_source(source)
    except PackageContentSource.DoesNotExist as exc:
        return ContentSyncQueueResult(
            ok=False,
            queued=False,
            ran_inline=False,
            source=source,
            batch_id=batch_id,
            message=f'No package content source row for {source.repo_name}',
            error=str(exc),
        )

    queued_marker = None
    try:
        with transaction.atomic():
            if mark_queued:
                queued_marker = _mark_source_queued(
                    package_source, batch_id=batch_id,
                )
            key = (
                f'queue:{queued_marker.pk}'
                if queued_marker is not None
                else 'queue:inline'
            )
            intent, _created = package_queue_source_sync(
                package_source,
                key=key,
                batch_id=batch_id,
                force=force,
            )
    except Exception as exc:
        return ContentSyncQueueResult(
            ok=False,
            queued=False,
            ran_inline=False,
            source=source,
            batch_id=batch_id,
            message=f'Sync queue failed for {source.repo_name}: {exc}',
            error=str(exc),
        )

    return ContentSyncQueueResult(
        ok=True,
        queued=True,
        ran_inline=False,
        source=source,
        batch_id=batch_id,
        message=f'Sync queued for {source.repo_name}',
        task_id=intent.pk if intent is not None else None,
    )


def enqueue_content_syncs(
    sources,
    batch_id=None,
    force=False,
    mark_queued=True,
    task_source='content sync queue',
):
    """Queue multiple content sources, returning one result per source."""
    return [
        enqueue_content_sync(
            source,
            batch_id=batch_id,
            force=force,
            mark_queued=mark_queued,
            task_source=task_source,
        )
        for source in sources
    ]
