"""Legacy-row mirror (A2.3 compatibility facade).

The retired tables (``integrations.ContentSource`` / ``SyncLog`` /
``WebhookLog``) are retained for rollback, and a large body of site code and
tests still creates rows through them. Every legacy insert/update is
mirrored into the package tables so the ``community_base.content_sync``
engine and its operator surfaces see one consistent world. The P6 data
migration (``integrations.0032``) applies the same mapping to existing rows.

Direction is strictly legacy -> package: the engine itself only writes
package rows. The mirror is the documented compatibility seam and is
removed together with the legacy tables in the follow-up retirement issue.
"""

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from community_base.content_sync.models import (
    SyncLog as PackageSyncLog,
)
from community_base.content_sync.models import (
    WebhookLog as PackageWebhookLog,
)
from django.db import transaction
from django.db.models.signals import post_save
from django.utils.text import slugify

from integrations.models import ContentSource, SyncLog, WebhookLog


def derive_slug(repo_name, row_id=None):
    """Deterministic package slug (mirrors the P6 migration mapping)."""
    base = slugify((repo_name or '').rsplit('/', 1)[-1]) or 'source'
    return base


def mirror_source(instance, *, created):
    secret = (instance.webhook_secret or '').strip()
    defaults = {
        'slug': instance.content_sync_slug or derive_slug(instance.repo_name),
        'repo_name': instance.repo_name,
        'webhook_secret': secret,
        'is_private': instance.is_private,
        # Mirror rule matches the P6 mapping: no legacy enabled flag.
        'is_enabled': bool(secret),
        'last_synced_at': instance.last_synced_at,
        'last_sync_status': instance.last_sync_status or '',
        'last_sync_log': instance.last_sync_log or '',
        'sync_locked_at': instance.sync_locked_at,
        'sync_requested': instance.sync_requested,
        'last_webhook_at': instance.last_webhook_at,
        'last_synced_commit': instance.last_synced_commit or '',
        'max_files': instance.max_files,
    }
    try:
        with transaction.atomic():
            PackageContentSource.objects.update_or_create(
                id=instance.id, defaults=defaults,
            )
            PackageContentSource.objects.filter(id=instance.id).update(
                created_at=instance.created_at,
                updated_at=instance.updated_at,
            )
    except Exception:
        # The mirror must never break the legacy write path.
        pass


def mirror_sync_log(instance, *, created):
    warnings = []
    if instance.tiers_synced or instance.tiers_count:
        warnings.append({
            'asl_compat': {
                'tiers_synced': bool(instance.tiers_synced),
                'tiers_count': int(instance.tiers_count or 0),
            },
        })
    try:
        with transaction.atomic():
            PackageSyncLog.objects.update_or_create(
                id=instance.id,
                defaults={
                    'source_id': instance.source_id,
                    'batch_id': instance.batch_id,
                    'started_at': instance.started_at,
                    'finished_at': instance.finished_at,
                    'status': instance.status,
                    'items_created': instance.items_created,
                    'items_updated': instance.items_updated,
                    'items_unchanged': instance.items_unchanged,
                    'items_deleted': instance.items_deleted,
                    'items_detail': instance.items_detail or [],
                    'commit_sha': instance.commit_sha or '',
                    'errors': instance.errors or [],
                    'warnings': warnings,
                },
            )
            PackageSyncLog.objects.filter(id=instance.id).update(
                started_at=instance.started_at,
            )
    except Exception:
        pass


def mirror_webhook_log(instance, *, created):
    if instance.service != 'github':
        return
    payload = dict(instance.payload or {})
    correlation = {}
    if getattr(instance, 'calendly_event_uri', ''):
        correlation['calendly_event_uri'] = instance.calendly_event_uri
    if getattr(instance, 'calendly_invitee_uri', ''):
        correlation['calendly_invitee_uri'] = instance.calendly_invitee_uri
    if correlation:
        payload['asl_compat'] = correlation
    try:
        with transaction.atomic():
            PackageWebhookLog.objects.update_or_create(
                id=instance.id,
                defaults={
                    'service': instance.service,
                    'event_type': instance.event_type or '',
                    'payload': payload,
                    'received_at': instance.received_at,
                    'processed': instance.processed,
                    'deduplication_key': instance.deduplication_key,
                    'attempts': instance.attempts,
                    'error_message': instance.error_message or '',
                    'processed_at': instance.processed_at,
                },
            )
            PackageWebhookLog.objects.filter(id=instance.id).update(
                received_at=instance.received_at,
            )
    except Exception:
        pass


def register_mirror():
    post_save.connect(
        lambda sender, instance, created, **kw: mirror_source(
            instance, created=created,
        ),
        sender=ContentSource, weak=False, dispatch_uid='a23_mirror_source',
    )
    post_save.connect(
        lambda sender, instance, created, **kw: mirror_sync_log(
            instance, created=created,
        ),
        sender=SyncLog, weak=False, dispatch_uid='a23_mirror_sync_log',
    )
    post_save.connect(
        lambda sender, instance, created, **kw: mirror_webhook_log(
            instance, created=created,
        ),
        sender=WebhookLog, weak=False, dispatch_uid='a23_mirror_webhook_log',
    )
