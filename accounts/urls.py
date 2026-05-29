from django.urls import include, path

from accounts.views.account import (
    account_profile_post_view,
    account_view,
    email_preferences_view,
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
    password_reset_request_view,
    register_api,
    register_view,
    signup_redirect_view,
    verify_email_api,
)
from accounts.views.onboarding import (
    onboarding_fill,
    onboarding_identify,
    onboarding_start,
    onboarding_submit,
)

urlpatterns = [
    path('login/', login_view, name='account_login'),
    path('logout/', logout_view, name='account_logout'),
    path('register/', register_view, name='account_register'),
    path(
        'password-reset-request',
        password_reset_request_view,
        name='account_password_reset_request',
    ),
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

# Member onboarding flow (issue #802), mounted at /onboarding/ in
# project urls.py. ``/onboarding/`` is added to
# ``RemoveTrailingSlashMiddleware.SKIP_PREFIXES`` so the landing page
# keeps its trailing slash like ``/account/``; the action verbs
# (``identify`` / ``submit``) use no trailing slash, matching the
# accounts convention.
onboarding_urlpatterns = [
    path('', onboarding_start, name='onboarding_start'),
    path('identify', onboarding_identify, name='onboarding_identify'),
    path('<int:response_id>', onboarding_fill, name='onboarding_fill'),
    path('<int:response_id>/submit', onboarding_submit, name='onboarding_submit'),
]

# Account page and API endpoints (mounted at /account/ in project urls.py)
account_urlpatterns = [
    path('', account_view, name='account'),
    path('profile', account_profile_post_view, name='account_profile'),
    path('api/email-preferences', email_preferences_view, name='email_preferences'),
    path('api/timezone-preference', timezone_preference_view, name='timezone_preference'),
    path('api/change-password', change_password_api, name='account_change_password'),
    path(
        'api/resend-verification',
        resend_verification_view,
        name='account_resend_verification',
    ),
]
