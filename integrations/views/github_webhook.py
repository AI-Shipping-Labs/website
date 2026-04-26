"""GitHub webhook endpoint for receiving push events and triggering content sync.

Endpoint: POST /api/webhooks/github

When GitHub sends a push event to main:
1. Validates the X-Hub-Signature-256 header
2. Identifies the repo from the payload
3. Enqueues a background sync job for the repo
"""

import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from integrations.models import SyncLog, WebhookLog
from integrations.services.github import (
    SYNC_LOCK_TIMEOUT_MINUTES,
    find_content_source,
    sync_content_source,
    validate_webhook_signature,
)

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def github_webhook(request):
    """Handle incoming GitHub webhooks.

    Validates the signature against the matching ContentSource's webhook_secret,
    logs the webhook, and enqueues a sync job for push events to the main branch.

    Returns:
        200 on success
        400 on invalid signature or malformed payload
        404 if repo not found in content sources
    """
    # Parse the payload
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON payload'},
            status=400,
        )

    # Identify the repo from the payload
    repo_full_name = payload.get('repository', {}).get('full_name', '')
    if not repo_full_name:
        return JsonResponse(
            {'error': 'Missing repository information'},
            status=400,
        )

    # Find all content sources for this repo (monorepo may have multiple)
    sources = find_content_source(repo_full_name)
    if not sources.exists():
        logger.warning('GitHub webhook for unknown repo: %s', repo_full_name)
        return JsonResponse(
            {'error': 'Unknown repository'},
            status=404,
        )

    # Validate webhook signature using the first source's webhook_secret
    first_source = sources.first()
    if first_source.webhook_secret:
        if not validate_webhook_signature(request, first_source.webhook_secret):
            logger.warning(
                'Invalid GitHub webhook signature for repo %s', repo_full_name,
            )
            return JsonResponse(
                {'error': 'Invalid webhook signature'},
                status=400,
            )

    # Log the webhook
    event_type = request.headers.get('X-GitHub-Event', 'unknown')
    webhook_log = WebhookLog.objects.create(
        service='github',
        event_type=event_type,
        payload=payload,
        processed=False,
    )

    # Only process push events to the main/master branch
    ref = payload.get('ref', '')
    if event_type == 'push' and ref in ('refs/heads/main', 'refs/heads/master'):
        try:
            now = timezone.now()
            stale_threshold = now - timezone.timedelta(
                minutes=SYNC_LOCK_TIMEOUT_MINUTES,
            )

            for source in sources:
                # Edge Case 9: Update last_webhook_at on every webhook
                source.last_webhook_at = now
                source.save(update_fields=['last_webhook_at', 'updated_at'])

                # Edge Case 9: Webhook dedup - if sync is already running,
                # set sync_requested flag instead of enqueuing
                if (source.sync_locked_at
                        and source.sync_locked_at >= stale_threshold):
                    source.sync_requested = True
                    source.save(update_fields=['sync_requested', 'updated_at'])
                    logger.info(
                        'Sync already running for %s, setting sync_requested flag.',
                        source.repo_name,
                    )
                    continue

                # Try to enqueue as a background job.
                # ``force=True`` (issue #235): the webhook payload tells us
                # a new commit just landed, so we trust it without
                # re-checking via ``git ls-remote`` first.
                try:
                    from django_q.tasks import async_task
                    async_task(
                        'integrations.services.github.sync_content_source',
                        source,
                        force=True,
                        task_name=f'sync-{source.repo_name}',
                    )
                    # Issue #274: webhook-driven sync goes through the same
                    # queued-then-running pattern as the Studio trigger so
                    # the dashboard shows blue "queued" pills for in-flight
                    # webhook syncs (otherwise the dashboard would still
                    # say "success" for the old commit until pickup).
                    SyncLog.objects.create(source=source, status='queued')
                    source.last_sync_status = 'queued'
                    source.save(
                        update_fields=['last_sync_status', 'updated_at'],
                    )
                except ImportError:
                    # Django-Q not available, run synchronously
                    sync_content_source(source, force=True)

            webhook_log.processed = True
            webhook_log.save()
        except Exception as e:
            logger.exception(
                'Error processing GitHub webhook for %s', repo_full_name,
            )
            return JsonResponse(
                {'status': 'error', 'message': str(e)},
                status=200,
            )

    return JsonResponse({'status': 'ok'})
