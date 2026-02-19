"""Zoom webhook endpoint for handling recording.completed events.

Endpoint: POST /api/webhooks/zoom

When Zoom sends a recording.completed webhook:
1. Validates the webhook signature
2. Matches the meeting_id to an Event record
3. Creates a Recording record with title/description/tags from the event
4. Links the Recording to the Event
5. Sets the Event status to 'completed'
"""

import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from content.models import Recording
from events.models import Event
from integrations.models import WebhookLog
from integrations.services.zoom import validate_webhook_signature

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def zoom_webhook(request):
    """Handle incoming Zoom webhooks.

    Validates the signature, logs the webhook, and processes
    recording.completed events.

    Returns:
        200 on success
        400 on invalid signature or malformed payload
    """
    # Validate webhook signature
    if not validate_webhook_signature(request):
        logger.warning('Invalid Zoom webhook signature')
        return JsonResponse(
            {'error': 'Invalid webhook signature'},
            status=400,
        )

    # Parse the payload
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON payload'},
            status=400,
        )

    event_type = payload.get('event', '')

    # Handle Zoom's URL validation challenge (endpoint validation)
    if event_type == 'endpoint.url_validation':
        plain_token = payload.get('payload', {}).get('plainToken', '')
        if plain_token:
            import hashlib
            import hmac
            from django.conf import settings
            secret = settings.ZOOM_WEBHOOK_SECRET_TOKEN
            encrypted_token = hmac.new(
                secret.encode('utf-8'),
                plain_token.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            return JsonResponse({
                'plainToken': plain_token,
                'encryptedToken': encrypted_token,
            })

    # Log the webhook
    webhook_log = WebhookLog.objects.create(
        service='zoom',
        event_type=event_type,
        payload=payload,
        processed=False,
    )

    # Process recording.completed
    if event_type == 'recording.completed':
        try:
            _handle_recording_completed(payload, webhook_log)
        except Exception as e:
            logger.exception('Error processing recording.completed webhook')
            return JsonResponse(
                {'status': 'error', 'message': str(e)},
                status=200,  # Return 200 to avoid Zoom retries
            )

    return JsonResponse({'status': 'ok'})


def _handle_recording_completed(payload, webhook_log):
    """Process a recording.completed webhook payload.

    Args:
        payload: The parsed JSON payload from Zoom.
        webhook_log: The WebhookLog instance for this webhook.
    """
    zoom_payload = payload.get('payload', {})
    object_data = zoom_payload.get('object', {})
    meeting_id = str(object_data.get('id', ''))

    if not meeting_id:
        logger.warning('recording.completed webhook missing meeting ID')
        return

    # Find the event with this zoom_meeting_id
    try:
        event = Event.objects.get(zoom_meeting_id=meeting_id)
    except Event.DoesNotExist:
        logger.warning(
            'No event found for Zoom meeting ID %s', meeting_id,
        )
        return

    # Extract recording URL from Zoom payload
    # Zoom provides recording files in the payload
    recording_files = object_data.get('recording_files', [])
    video_url = ''
    for rec_file in recording_files:
        # Prefer shared_screen_with_speaker_view or shared_screen
        if rec_file.get('recording_type') in (
            'shared_screen_with_speaker_view',
            'shared_screen',
            'active_speaker',
        ):
            video_url = rec_file.get('play_url', '') or rec_file.get('download_url', '')
            if video_url:
                break

    # Fallback: use the share_url from the object if available
    if not video_url:
        video_url = object_data.get('share_url', '')

    # Generate a unique slug from the event title
    base_slug = slugify(event.title)
    slug = base_slug
    counter = 1
    while Recording.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1

    # Create the Recording
    recording = Recording.objects.create(
        title=event.title,
        slug=slug,
        description=event.description,
        event=event,
        date=event.start_datetime.date(),
        tags=event.tags,
        youtube_url=video_url,
        required_level=event.required_level,
        published=False,  # Admin needs to review before publishing
    )

    # Link recording to event and mark event as completed
    event.recording = recording
    event.status = 'completed'
    event.save()

    # Mark webhook as processed
    webhook_log.processed = True
    webhook_log.save()

    logger.info(
        'Created Recording "%s" (slug=%s) from Zoom meeting %s for event "%s"',
        recording.title, recording.slug, meeting_id, event.title,
    )
