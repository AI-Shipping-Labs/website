from django.urls import path

from integrations.views.ses_webhook import ses_webhook
from integrations.views.zoom_webhook import zoom_webhook

urlpatterns = [
    path('api/webhooks/zoom', zoom_webhook, name='zoom_webhook'),
    path('api/webhooks/ses', ses_webhook, name='ses_webhook'),
]
