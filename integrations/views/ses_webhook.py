"""SES webhook endpoint for handling bounce and complaint notifications.

Endpoint: POST /api/webhooks/ses

Amazon SES sends bounce/complaint notifications via SNS to this endpoint.
When a hard bounce or complaint is received, the affected user's
unsubscribed flag is set to True.

SNS notification flow:
1. SES detects bounce or complaint
2. SES publishes to an SNS topic
3. SNS sends HTTP POST to this endpoint
4. This endpoint validates the notification and processes it

SNS message types:
- SubscriptionConfirmation: auto-confirms the subscription
- Notification: contains SES event data (bounce/complaint)
"""

import json
import logging

import requests
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from integrations.models import WebhookLog
from integrations.services.ses import validate_sns_notification

logger = logging.getLogger(__name__)
User = get_user_model()


@csrf_exempt
@require_POST
def ses_webhook(request):
    """Handle incoming SES bounce/complaint notifications via SNS.

    Validates the SNS notification signature, logs the webhook,
    and processes bounce/complaint events.

    Returns:
        200 on success
        400 on invalid signature or malformed payload
    """
    # Parse the payload
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON payload'},
            status=400,
        )

    # Validate SNS notification signature
    if not validate_sns_notification(payload):
        logger.warning('Invalid SES/SNS notification signature')
        return JsonResponse(
            {'error': 'Invalid notification signature'},
            status=400,
        )

    message_type = payload.get('Type', '')

    # Handle SNS subscription confirmation
    if message_type == 'SubscriptionConfirmation':
        subscribe_url = payload.get('SubscribeURL', '')
        if subscribe_url:
            try:
                requests.get(subscribe_url, timeout=10)
                logger.info('Confirmed SNS subscription: %s', subscribe_url)
            except Exception:
                logger.exception('Failed to confirm SNS subscription')
        return JsonResponse({'status': 'subscription_confirmed'})

    # Handle notifications
    if message_type != 'Notification':
        logger.info('Ignoring SNS message type: %s', message_type)
        return JsonResponse({'status': 'ignored'})

    # Parse the SES notification from the SNS message
    try:
        message = json.loads(payload.get('Message', '{}'))
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid Message JSON'},
            status=400,
        )

    notification_type = message.get('notificationType', '')

    # Log the webhook
    webhook_log = WebhookLog.objects.create(
        service='ses',
        event_type=notification_type,
        payload=payload,
        processed=False,
    )

    # Process bounces and complaints
    if notification_type == 'Bounce':
        _handle_bounce(message, webhook_log)
    elif notification_type == 'Complaint':
        _handle_complaint(message, webhook_log)
    else:
        logger.info('Ignoring SES notification type: %s', notification_type)

    return JsonResponse({'status': 'ok'})


def _handle_bounce(message, webhook_log):
    """Process a bounce notification.

    Only hard bounces (permanent) trigger unsubscription.
    Soft bounces (transient) are logged but do not unsubscribe.

    Args:
        message: Parsed SES bounce notification dict.
        webhook_log: WebhookLog instance for this webhook.
    """
    bounce = message.get('bounce', {})
    bounce_type = bounce.get('bounceType', '')

    if bounce_type != 'Permanent':
        logger.info(
            'Ignoring non-permanent bounce type: %s', bounce_type,
        )
        return

    recipients = bounce.get('bouncedRecipients', [])
    for recipient in recipients:
        email = recipient.get('emailAddress', '')
        if email:
            _unsubscribe_user(email, 'hard_bounce')

    webhook_log.processed = True
    webhook_log.save()

    logger.info(
        'Processed hard bounce for %d recipients',
        len(recipients),
    )


def _handle_complaint(message, webhook_log):
    """Process a complaint notification.

    All complaints trigger unsubscription.

    Args:
        message: Parsed SES complaint notification dict.
        webhook_log: WebhookLog instance for this webhook.
    """
    complaint = message.get('complaint', {})
    recipients = complaint.get('complainedRecipients', [])

    for recipient in recipients:
        email = recipient.get('emailAddress', '')
        if email:
            _unsubscribe_user(email, 'complaint')

    webhook_log.processed = True
    webhook_log.save()

    logger.info(
        'Processed complaint for %d recipients',
        len(recipients),
    )


def _unsubscribe_user(email, reason):
    """Set unsubscribed=True for the user with the given email.

    Args:
        email: Email address of the user to unsubscribe.
        reason: Reason for unsubscription (e.g. 'hard_bounce', 'complaint').
    """
    try:
        user = User.objects.get(email=email)
        if not user.unsubscribed:
            user.unsubscribed = True
            user.save(update_fields=['unsubscribed'])
            logger.info(
                'Unsubscribed user %s due to %s', email, reason,
            )
    except User.DoesNotExist:
        logger.warning(
            'No user found for email %s during %s processing',
            email, reason,
        )
