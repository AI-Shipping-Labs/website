"""
Notification page views:
- GET /notifications — full paginated list of notifications
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from notifications.models import Notification


@login_required
def notification_list_page(request):
    """Full page list of the user's notifications, paginated."""
    notification_filter = request.GET.get('filter', 'unread')
    if notification_filter not in {'unread', 'all'}:
        notification_filter = 'unread'

    qs = Notification.objects.filter(
        user=request.user,
    ).order_by('-created_at')
    if notification_filter == 'unread':
        qs = qs.filter(read=False)

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    unread_count = Notification.objects.filter(
        user=request.user,
        read=False,
    ).count()

    return render(request, 'notifications/notification_list.html', {
        'active_filter': notification_filter,
        'all_filter_url': '?filter=all',
        'unread_count': unread_count,
        'page_obj': page_obj,
        'pagination_filter_query': f'filter={notification_filter}&',
        'notifications': page_obj.object_list,
    })
