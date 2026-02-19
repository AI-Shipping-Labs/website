from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

from accounts.urls import account_urlpatterns, auth_api_urlpatterns
from content.sitemaps import sitemaps
from email_app.urls import api_urlpatterns as email_api_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('account/', include(account_urlpatterns)),
    path('api/', include(auth_api_urlpatterns)),
    path('api/', include(email_api_urlpatterns)),
    path('register', RedirectView.as_view(url='/accounts/register/', permanent=False), name='register_shortcut'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('', include('payments.urls')),
    path('', include('content.urls')),
    path('', include('events.urls')),
    path('', include('integrations.urls')),
    path('', include('voting.urls')),
    path('', include('email_app.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
