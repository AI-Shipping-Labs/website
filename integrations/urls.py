from django.urls import path

from integrations.views.zoom_webhook import zoom_webhook

urlpatterns = [
    path('api/webhooks/zoom', zoom_webhook, name='zoom_webhook'),
]
