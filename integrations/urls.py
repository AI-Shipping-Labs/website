from django.urls import path

from integrations.views.calendly_webhook import calendly_webhook
from integrations.views.github_webhook import github_webhook
from integrations.views.maven_webhook import maven_webhook
from integrations.views.zoom_webhook import zoom_webhook

urlpatterns = [
    path('api/webhooks/zoom', zoom_webhook, name='zoom_webhook'),
    path('api/webhooks/calendly', calendly_webhook, name='calendly_webhook'),
    path('api/webhooks/github', github_webhook, name='github_webhook'),
    path('api/webhooks/maven', maven_webhook, name='maven_webhook'),
]
