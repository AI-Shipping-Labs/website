from django.urls import include, path

from accounts.views.account import (
    account_profile_post_view,
    account_view,
    cancel_subscription_view,
    email_preferences_view,
    member_plan_edit,
    resend_verification_view,
    theme_preference_view,
    timezone_preference_view,
)
from accounts.views.auth import (
    change_password_api,
    login_api,
    login_view,
    logout_view,
    password_reset_api,
    password_reset_request_api,
    register_api,
    register_view,
    signup_redirect_view,
    verify_email_api,
)

urlpatterns = [
    path('login/', login_view, name='account_login'),
    path('logout/', logout_view, name='account_logout'),
    path('register/', register_view, name='account_register'),
    path('signup/', signup_redirect_view, name='account_signup'),
    path('', include('allauth.urls')),
]

# API endpoints (mounted at /api/ in project urls.py)
auth_api_urlpatterns = [
    path('register', register_api, name='api_register'),
    path('login', login_api, name='api_login'),
    path('verify-email', verify_email_api, name='api_verify_email'),
    path('password-reset-request', password_reset_request_api, name='api_password_reset_request'),
    path('password-reset', password_reset_api, name='api_password_reset'),
    path('account/theme-preference', theme_preference_view, name='api_theme_preference'),
]

# Account page and API endpoints (mounted at /account/ in project urls.py)
account_urlpatterns = [
    path('', account_view, name='account'),
    path('profile', account_profile_post_view, name='account_profile'),
    # Member-facing plan editor (issue #444). Owner-only; renders the
    # SAME drag-drop editor partial staff see in Studio.
    path('plan/<int:plan_id>/edit/', member_plan_edit, name='account_plan_edit'),
    path('api/email-preferences', email_preferences_view, name='email_preferences'),
    path('api/timezone-preference', timezone_preference_view, name='timezone_preference'),
    path('api/cancel', cancel_subscription_view, name='account_cancel_subscription'),
    path('api/change-password', change_password_api, name='account_change_password'),
    path(
        'api/resend-verification',
        resend_verification_view,
        name='account_resend_verification',
    ),
]
