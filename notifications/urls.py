from django.urls import path

from notifications.views.api import (
    api_notification_list,
    api_unread_count,
    api_mark_read,
    api_mark_all_read,
)
from notifications.views.pages import notification_list_page

# API endpoints
api_urlpatterns = [
    path('notifications', api_notification_list, name='api_notification_list'),
    path('notifications/unread-count', api_unread_count, name='api_unread_count'),
    path('notifications/<int:notification_id>/read', api_mark_read, name='api_mark_read'),
    path('notifications/read-all', api_mark_all_read, name='api_mark_all_read'),
]

# Page URL patterns
page_urlpatterns = [
    path('notifications', notification_list_page, name='notification_list'),
]
