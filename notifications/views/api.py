"""
Notification API endpoints:
- GET /api/notifications — list user's notifications (paginated, 20/page)
- GET /api/notifications/unread-count — unread count for badge
- POST /api/notifications/{id}/read — mark single notification as read
- POST /api/notifications/read-all — mark all as read
"""

import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required

from notifications.models import Notification


@login_required
@require_GET
def api_notification_list(request):
    """Return the current user's notifications, paginated (20/page).

    Query params:
        page: Page number (default 1).

    Returns JSON:
        {
            "notifications": [...],
            "page": 1,
            "has_next": true,
            "total": 42
        }
    """
    page = int(request.GET.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page

    qs = Notification.objects.filter(
        user=request.user,
    ).order_by('-created_at')

    total = qs.count()
    notifications = qs[offset:offset + per_page]

    data = {
        'notifications': [
            {
                'id': n.pk,
                'title': n.title,
                'body': n.body[:80] if n.body else '',
                'url': n.url,
                'notification_type': n.notification_type,
                'read': n.read,
                'created_at': n.created_at.isoformat(),
            }
            for n in notifications
        ],
        'page': page,
        'has_next': (offset + per_page) < total,
        'total': total,
    }

    return JsonResponse(data)


@login_required
@require_GET
def api_unread_count(request):
    """Return the unread notification count for the badge.

    Returns JSON:
        {"count": 5}
    """
    count = Notification.objects.filter(
        user=request.user,
        read=False,
    ).count()

    return JsonResponse({'count': count})


@login_required
@require_POST
def api_mark_read(request, notification_id):
    """Mark a single notification as read.

    Returns JSON:
        {"ok": true}
    """
    updated = Notification.objects.filter(
        pk=notification_id,
        user=request.user,
    ).update(read=True)

    if updated == 0:
        return JsonResponse({'ok': False, 'error': 'not_found'}, status=404)

    return JsonResponse({'ok': True})


@login_required
@require_POST
def api_mark_all_read(request):
    """Mark all of the user's notifications as read.

    Returns JSON:
        {"ok": true, "count": 10}
    """
    count = Notification.objects.filter(
        user=request.user,
        read=False,
    ).update(read=True)

    return JsonResponse({'ok': True, 'count': count})
