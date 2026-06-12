"""Calendly webhook endpoint for booked-call capture (issue #884).

Endpoint: POST /api/webhooks/calendly

When Calendly sends ``invitee.created`` / ``invitee.canceled``:
1. Verifies the ``Calendly-Webhook-Signature`` header (when validation
   is enabled and a signing key is configured).
2. Logs the webhook to ``WebhookLog``.
3. Records / cancels a ``BookedCall`` and adjusts the host's
   ``current_load`` so ``/request-a-call`` availability stays accurate.

Handling is best-effort: a processing error logs and still returns 200
so Calendly does not retry forever, and capacity is only mutated inside
the same transaction that records or cancels the booking — a failure can
never leave CRM data corrupted.
"""

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from community.services.calendly import process_webhook, verify_signature
from integrations.models import WebhookLog

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def calendly_webhook(request):
    """Handle incoming Calendly webhooks.

    Returns:
        200 on success or best-effort processing error (avoids retries).
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
    webhook_log = WebhookLog.objects.create(
        service='calendly',
        event_type=event_type,
        payload=payload,
        processed=False,
    )

    try:
        process_webhook(payload)
    except Exception:
        logger.exception('Error processing Calendly webhook %s', event_type)
        return JsonResponse({'status': 'error'}, status=200)

    webhook_log.processed = True
    webhook_log.save(update_fields=['processed'])
    return JsonResponse({'status': 'ok'})
