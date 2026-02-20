from django.urls import path, include

from accounts.views.auth import (
    login_view,
    logout_view,
    register_view,
    register_api,
    login_api,
    verify_email_api,
    password_reset_request_api,
    password_reset_api,
    change_password_api,
)
from accounts.views.account import (
    account_view,
    cancel_subscription_view,
    email_preferences_view,
)

urlpatterns = [
    path('login/', login_view, name='account_login'),
    path('logout/', logout_view, name='account_logout'),
    path('register/', register_view, name='account_register'),
    path('', include('allauth.urls')),
]

# API endpoints (mounted at /api/ in project urls.py)
auth_api_urlpatterns = [
    path('register', register_api, name='api_register'),
    path('login', login_api, name='api_login'),
    path('verify-email', verify_email_api, name='api_verify_email'),
    path('password-reset-request', password_reset_request_api, name='api_password_reset_request'),
    path('password-reset', password_reset_api, name='api_password_reset'),
]

# Account page and API endpoints (mounted at /account/ in project urls.py)
account_urlpatterns = [
    path('', account_view, name='account'),
    path('api/email-preferences', email_preferences_view, name='email_preferences'),
    path('api/cancel', cancel_subscription_view, name='account_cancel_subscription'),
    path('api/change-password', change_password_api, name='account_change_password'),
]
