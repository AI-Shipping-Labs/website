from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from accounts.urls import account_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('account/', include(account_urlpatterns)),
    path('', include('payments.urls')),
    path('', include('content.urls')),
    path('', include('events.urls')),
    path('', include('integrations.urls')),
    path('', include('voting.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
