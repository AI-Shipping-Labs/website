"""A2.3 (P6): copy content sync rows into the package tables.

Copies integrations.ContentSource, integrations.SyncLog, and GitHub
integrations.WebhookLog rows into the community_base.content_sync tables.
Idempotent by preserved primary keys; reruns update the same package rows
and rewrite the legacy ``content_sync_slug`` mapping. Legacy rows are never
deleted (retained for rollback).

Mappings documented for the A2.3 pull request:

- slug: derived deterministically from the repository short name; collisions
  resolved by sorted source UUID (first keeps the base slug, later ones get
  ``-<uuid8>``), and written back to the legacy row.
- is_enabled: legacy rows have no enabled flag; a nonblank webhook secret
  migrates enabled, a blank secret disabled (reported at runtime by
  seed_content_sources, never printed here).
- tiers_synced / tiers_count: the package SyncLog has no tier columns; they
  are preserved in the namespaced warnings entry
  ``{"asl_compat": {"tiers_synced": bool, "tiers_count": int}}`` consumed by
  the Studio/API history adapters.
- Calendly correlation columns do not exist on the package WebhookLog; only
  GitHub rows are copied and any legacy correlation values ride in the
  namespaced payload extension ``payload["asl_compat"]``.
"""

from collections import Counter

from django.db import migrations
from django.utils.text import slugify


def _slug_base(repo_name):
    return slugify(repo_name.rsplit('/', 1)[-1]) or 'source'


def copy_sources(apps):
    LegacySource = apps.get_model('integrations', 'ContentSource')
    Source = apps.get_model('cb_content_sync', 'ContentSource')

    rows = list(LegacySource.objects.all().order_by('id'))
    counters = Counter(_slug_base(row.repo_name) for row in rows)
    seen = Counter()
    for row in rows:
        base = _slug_base(row.repo_name)
        if counters[base] > 1:
            seen[base] += 1
            slug = base if seen[base] == 1 else f'{base}-{str(row.id)[:8]}'
        else:
            slug = base

        secret = (row.webhook_secret or '').strip()
        Source.objects.update_or_create(
            id=row.id,
            defaults={
                'slug': slug,
                'repo_name': row.repo_name,
                'webhook_secret': secret,
                'is_private': row.is_private,
                # No legacy enabled flag: secret presence decides.
                'is_enabled': bool(secret),
                'last_synced_at': row.last_synced_at,
                'last_sync_status': row.last_sync_status or '',
                'last_sync_log': row.last_sync_log or '',
                'sync_locked_at': row.sync_locked_at,
                'sync_requested': row.sync_requested,
                'last_webhook_at': row.last_webhook_at,
                'last_synced_commit': row.last_synced_commit or '',
                'max_files': row.max_files,
            },
        )
        # auto_now/auto_now_add fields: force the historical values back.
        Source.objects.filter(id=row.id).update(
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        LegacySource.objects.filter(pk=row.pk).update(content_sync_slug=slug)


def copy_sync_logs(apps):
    LegacySyncLog = apps.get_model('integrations', 'SyncLog')
    SyncLog = apps.get_model('cb_content_sync', 'SyncLog')

    for log in LegacySyncLog.objects.all().order_by('started_at', 'id'):
        warnings = []
        if log.tiers_synced or log.tiers_count:
            warnings.append({
                'asl_compat': {
                    'tiers_synced': bool(log.tiers_synced),
                    'tiers_count': int(log.tiers_count or 0),
                },
            })
        SyncLog.objects.update_or_create(
            id=log.id,
            defaults={
                'source_id': log.source_id,
                'batch_id': log.batch_id,
                'started_at': log.started_at,
                'finished_at': log.finished_at,
                'status': log.status,
                'items_created': log.items_created,
                'items_updated': log.items_updated,
                'items_unchanged': log.items_unchanged,
                'items_deleted': log.items_deleted,
                'items_detail': log.items_detail or [],
                'commit_sha': log.commit_sha or '',
                'errors': log.errors or [],
                'warnings': warnings,
            },
        )
        SyncLog.objects.filter(id=log.id).update(started_at=log.started_at)


def copy_webhook_logs(apps):
    LegacyWebhookLog = apps.get_model('integrations', 'WebhookLog')
    WebhookLog = apps.get_model('cb_content_sync', 'WebhookLog')

    for log in LegacyWebhookLog.objects.filter(service='github').order_by('received_at', 'id'):
        payload = dict(log.payload or {})
        correlation = {}
        if getattr(log, 'calendly_event_uri', ''):
            correlation['calendly_event_uri'] = log.calendly_event_uri
        if getattr(log, 'calendly_invitee_uri', ''):
            correlation['calendly_invitee_uri'] = log.calendly_invitee_uri
        if correlation:
            payload['asl_compat'] = correlation
        WebhookLog.objects.update_or_create(
            id=log.id,
            defaults={
                'service': log.service,
                'event_type': log.event_type or '',
                'payload': payload,
                'received_at': log.received_at,
                'processed': log.processed,
                'deduplication_key': log.deduplication_key,
                'attempts': log.attempts,
                'error_message': log.error_message or '',
                'processed_at': log.processed_at,
            },
        )
        WebhookLog.objects.filter(id=log.id).update(received_at=log.received_at)


def build_report(apps):
    LegacySource = apps.get_model('integrations', 'ContentSource')
    LegacySyncLog = apps.get_model('integrations', 'SyncLog')
    LegacyWebhookLog = apps.get_model('integrations', 'WebhookLog')
    Source = apps.get_model('cb_content_sync', 'ContentSource')
    SyncLog = apps.get_model('cb_content_sync', 'SyncLog')
    WebhookLog = apps.get_model('cb_content_sync', 'WebhookLog')
    return (
        'content sync copy report: '
        f"sources pre={LegacySource.objects.count()} post={Source.objects.count()}; "
        f"sync logs pre={LegacySyncLog.objects.count()} post={SyncLog.objects.count()}; "
        f"github webhooks pre={LegacyWebhookLog.objects.filter(service='github').count()} "
        f"post={WebhookLog.objects.filter(service='github').count()}"
    )


def forwards(apps, schema_editor):
    copy_sources(apps)
    copy_sync_logs(apps)
    copy_webhook_logs(apps)
    # The pre/post identity report lands in the migration log output.
    print(build_report(apps), flush=True)


def backwards(apps, schema_editor):
    # Legacy tables are retained; nothing to undo.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('integrations', '0031_contentsource_content_sync_slug'),
        ('cb_content_sync', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
