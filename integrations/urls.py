from community_base.content_sync.urls import urlpatterns as cb_content_sync_urls
from community_base.content_sync.webhooks import github_webhook
from django.urls import include, path

from integrations.views.calendly_webhook import calendly_webhook
from integrations.views.maven_webhook import maven_webhook

urlpatterns = [
    path('api/webhooks/calendly', calendly_webhook, name='calendly_webhook'),
    # A2.3: the package handler serves the configured GitHub hook at the
    # legacy URL (no external reconfig).
    path('api/webhooks/github', github_webhook, name='github_webhook'),
    path('content-sync/', include(cb_content_sync_urls)),
    path('api/webhooks/maven', maven_webhook, name='maven_webhook'),
]
