from django.urls import path

from integrations.views.admin_sync import (
    admin_sync_all,
    admin_sync_dashboard,
    admin_sync_history,
    admin_sync_trigger,
)
from integrations.views.github_webhook import github_webhook
from integrations.views.ses_webhook import ses_webhook
from integrations.views.zoom_webhook import zoom_webhook

urlpatterns = [
    path('api/webhooks/zoom', zoom_webhook, name='zoom_webhook'),
    path('api/webhooks/ses', ses_webhook, name='ses_webhook'),
    path('api/webhooks/github', github_webhook, name='github_webhook'),
    path('admin/sync/', admin_sync_dashboard, name='admin_sync_dashboard'),
    path('admin/sync/all/', admin_sync_all, name='admin_sync_all'),
    path('admin/sync/<uuid:source_id>/history/', admin_sync_history, name='admin_sync_history'),
    path('admin/sync/<uuid:source_id>/trigger/', admin_sync_trigger, name='admin_sync_trigger'),
]
