from django.urls import path, include

from accounts.views import login_view, logout_view
from accounts.views.account import (
    account_view,
    cancel_subscription_view,
    email_preferences_view,
)

urlpatterns = [
    path('login/', login_view, name='account_login'),
    path('logout/', logout_view, name='account_logout'),
    path('', include('allauth.urls')),
]

# Account page and API endpoints (mounted at /account/ in project urls.py)
account_urlpatterns = [
    path('', account_view, name='account'),
    path('api/email-preferences', email_preferences_view, name='email_preferences'),
    path('api/cancel', cancel_subscription_view, name='account_cancel_subscription'),
]
