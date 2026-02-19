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
    qs = Notification.objects.filter(
        user=request.user,
    ).order_by('-created_at')

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'notifications/notification_list.html', {
        'page_obj': page_obj,
        'notifications': page_obj.object_list,
    })
