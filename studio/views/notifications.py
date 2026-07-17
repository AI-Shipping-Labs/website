"""Studio views for notification log and notify/announce actions."""

import logging
from datetime import timedelta

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.models import Article, Course, Download, Workshop
from events.models import Event
from notifications.models import Notification
from notifications.services import (
    NotificationService,
    get_email_eligible_users,
    get_notification_eligible_user_count,
    post_slack_announcement,
)
from notifications.services.notification_service import (
    CONTENT_TYPE_CONFIG,
)
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


# Map content_type string to (model_class, id_kwarg)
CONTENT_TYPE_MAP = {
    'article': (Article, 'article_id'),
    'recording': (Event, 'recording_id'),
    'event': (Event, 'event_id'),
    'download': (Download, 'download_id'),
    'course': (Course, 'course_id'),
    'workshop': (Workshop, 'workshop_id'),
}


def notification_action_context(content_type, content, *, includes_slack=True):
    """Build operator-facing audience counts and irreversible-action copy."""
    in_app_count = get_notification_eligible_user_count(content_type, content)
    button_label = f'Notify {in_app_count} eligible members'
    if content_type == 'workshop':
        email_count = get_email_eligible_users(content_type, content).count()
        confirmation = (
            f'Notify {in_app_count} eligible members in app, email '
            f'{email_count} eligible workshop subscribers, and post to '
            '#announcements? This cannot be undone.'
        )
    elif includes_slack:
        email_count = 0
        confirmation = (
            f'Notify {in_app_count} eligible members in app and post to '
            '#announcements? This cannot be undone.'
        )
    else:
        email_count = 0
        confirmation = (
            f'Notify {in_app_count} eligible members in app? '
            'This cannot be undone.'
        )
    return {
        'notify_audience_count': in_app_count,
        'notify_email_audience_count': email_count,
        'notify_button_label': button_label,
        'notify_confirmation': confirmation,
        'slack_confirmation': (
            'Post this announcement to the configured #announcements '
            'channel? This cannot be undone.'
        ),
    }


def _was_recently_notified(content_type, content):
    """Check if notifications were sent for this content in the last 24 hours.

    Returns True if a Notification with the same title and url exists
    within the last 24 hours.
    """
    config = CONTENT_TYPE_CONFIG.get(content_type)
    if not config:
        return False

    title = config['title_template'].format(title=content.title)
    url = content.get_absolute_url() if hasattr(content, 'get_absolute_url') else ''
    cutoff = timezone.now() - timedelta(hours=24)

    return Notification.objects.filter(
        title=title,
        url=url,
        created_at__gte=cutoff,
    ).exists()


@staff_required
def notification_log(request):
    """Show a paginated, deduplicated log of recent notifications."""
    from django.db.models import Count, Min

    # Group notifications by (title, url, date) and count users
    notification_batches = (
        Notification.objects
        .filter(user__isnull=False)
        .values('title', 'url')
        .annotate(
            user_count=Count('id'),
            created_date=Min('created_at'),
        )
        .order_by('-created_date')
    )

    paginator = Paginator(list(notification_batches), 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'studio/notifications/list.html', {
        'page_obj': page_obj,
    })


def _notify_content(
    request,
    content_type,
    content_id,
    *,
    post_to_slack=True,
    include_emailed=True,
):
    """Handle the notify subscribers POST for any content type.

    Returns JSON ``{"notified": N, "emailed": M}`` (issue #655) unless
    ``include_emailed`` is false. ``M`` is always ``0`` for content types
    without an ``email_template`` so the response shape stays uniform
    across generic notification actions.
    """
    model_class = CONTENT_TYPE_MAP[content_type][0]
    content = get_object_or_404(model_class, pk=content_id)

    # Check if already notified in the last 24 hours
    if _was_recently_notified(content_type, content):
        return JsonResponse(
            {'error': 'Already notified in the last 24 hours'},
            status=409,
        )

    result = NotificationService.notify(
        content_type,
        content_id,
        post_to_slack=post_to_slack,
    )
    payload = {
        'notified': result.get('notified', 0),
    }
    if include_emailed:
        payload['emailed'] = result.get('emailed', 0)
    return JsonResponse(payload)


def _announce_slack(request, content_type, content_id):
    """Handle the Slack announce POST for any content type."""
    model_class = CONTENT_TYPE_MAP[content_type][0]
    content = get_object_or_404(model_class, pk=content_id)

    try:
        result = post_slack_announcement(content_type, content)
        if result:
            return JsonResponse({'posted': True})
        else:
            return JsonResponse(
                {'error': 'Slack not configured or post failed'},
                status=500,
            )
    except Exception as e:
        logger.exception('Failed to post Slack announcement for %s/%s', content_type, content_id)
        return JsonResponse({'error': str(e)}, status=500)


# --- Article endpoints ---

@staff_required
@require_POST
def article_notify(request, article_id):
    return _notify_content(request, 'article', article_id)


@staff_required
@require_POST
def article_announce_slack(request, article_id):
    return _announce_slack(request, 'article', article_id)


# --- Recording endpoints ---

@staff_required
@require_POST
def recording_notify(request, recording_id):
    return _notify_content(request, 'recording', recording_id)


@staff_required
@require_POST
def recording_announce_slack(request, recording_id):
    return _announce_slack(request, 'recording', recording_id)


# --- Event endpoints ---

@staff_required
@require_POST
def event_notify(request, event_id):
    return _notify_content(
        request,
        'event',
        event_id,
        post_to_slack=False,
        include_emailed=False,
    )


@staff_required
@require_POST
def event_announce_slack(request, event_id):
    return _announce_slack(request, 'event', event_id)


# --- Download endpoints ---

@staff_required
@require_POST
def download_notify(request, download_id):
    return _notify_content(request, 'download', download_id)


@staff_required
@require_POST
def download_announce_slack(request, download_id):
    return _announce_slack(request, 'download', download_id)


# --- Course endpoints ---

@staff_required
@require_POST
def course_notify(request, course_id):
    return _notify_content(request, 'course', course_id)


@staff_required
@require_POST
def course_announce_slack(request, course_id):
    return _announce_slack(request, 'course', course_id)


# --- Workshop endpoints ---

@staff_required
@require_POST
def workshop_notify(request, workshop_id):
    return _notify_content(request, 'workshop', workshop_id)


@staff_required
@require_POST
def workshop_announce_slack(request, workshop_id):
    return _announce_slack(request, 'workshop', workshop_id)
