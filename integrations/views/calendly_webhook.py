"""Calendly webhook endpoint for booked-call capture (issue #884).

Endpoint: POST /api/webhooks/calendly

When Calendly sends ``invitee.created`` / ``invitee.canceled``:
1. Verifies the ``Calendly-Webhook-Signature`` header (when validation
   is enabled and a signing key is configured).
2. Logs the webhook to ``WebhookLog``.
3. Records / cancels a ``BookedCall`` and adjusts the host's
   ``current_load`` so ``/request-a-call`` availability stays accurate.

Handling is durable: a processing error is persisted and returns 500 so
Calendly retries, and capacity is only mutated inside
the same transaction that records or cancels the booking — a failure can
never leave CRM data corrupted.
"""

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from community.services.calendly import delivery_fingerprint, verify_signature
from integrations.models import WebhookLog
from integrations.services.calendly_delivery import (
    process_calendly_delivery,
    safe_error_category,
)

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def calendly_webhook(request):
    """Handle incoming Calendly webhooks.

    Returns:
        200 on success/already processed; 500 on retryable processing error.
        400 on invalid signature or malformed JSON.
    """
    if not verify_signature(request):
        logger.warning('Invalid Calendly webhook signature')
        return JsonResponse({'error': 'Invalid webhook signature'}, status=400)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)

    event_type = payload.get('event', '')
    webhook_log, created = WebhookLog.objects.get_or_create(
        deduplication_key=delivery_fingerprint(request),
        defaults={
            'service': 'calendly',
            'event_type': event_type,
            'payload': payload,
            'processed': False,
        },
    )
    if not created and webhook_log.processed:
        return JsonResponse({'status': 'already_processed'})

    try:
        process_calendly_delivery(webhook_log.pk)
    except Exception as exc:
        logger.error(
            'Calendly webhook processing failed: event=%s category=%s',
            event_type,
            safe_error_category(exc),
        )
        # Retryable: Calendly may redeliver, and operators can replay the
        # durable failed row from the command/admin surface.
        return JsonResponse({'status': 'error'}, status=500)
    return JsonResponse({'status': 'ok'})
