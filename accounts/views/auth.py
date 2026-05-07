"""Authentication views for email+password and OAuth login flows."""

import datetime
import json
import logging
import time

import jwt
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.models import User
from integrations.config import get_config, site_base_url

# Default grace period before an unverified email-signup account is
# hard-deleted by the daily purge task. Operators override per
# environment via the ``UNVERIFIED_USER_TTL_DAYS`` integration setting.
DEFAULT_UNVERIFIED_USER_TTL_DAYS = 7

logger = logging.getLogger(__name__)

# JWT algorithm
JWT_ALGORITHM = "HS256"
EMAIL_PASSWORD_AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"
EMAIL_PASSWORD_BACKEND = ModelBackend()
INVALID_LOGIN_ERROR = "Invalid email or password"


def _oauth_provider_context():
    configured_providers = set(
        SocialApp.objects.exclude(client_id='').values_list('provider', flat=True)
    )
    return {
        'oauth_google_enabled': 'google' in configured_providers,
        'oauth_github_enabled': 'github' in configured_providers,
        'oauth_slack_enabled': 'slack' in configured_providers,
    }


def _log_login_timing(outcome, started_at):
    """Log only coarse slow-login diagnostics; never credentials or tokens."""
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    slow_threshold_ms = getattr(settings, "LOGIN_API_SLOW_MS", 750)
    if elapsed_ms >= slow_threshold_ms:
        logger.warning(
            "Slow login_api attempt",
            extra={
                "login_outcome": outcome,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        )
    return elapsed_ms


@ensure_csrf_cookie
def login_view(request):
    """Render the login page with Google, GitHub and Slack OAuth buttons.

    Each "Sign in with X" button is gated on the matching ``SocialApp``
    having a non-empty ``client_id`` — otherwise the redirect would 500
    inside allauth. Studio settings clears credentials to disable a
    provider, so this gate is the operator's off switch (see issue #322).
    """
    if request.user.is_authenticated:
        return redirect("/")

    return render(request, "accounts/login.html", _oauth_provider_context())


@ensure_csrf_cookie
def register_view(request):
    """Render the registration page."""
    if request.user.is_authenticated:
        return redirect("/")
    return render(request, "accounts/register.html", _oauth_provider_context())


def logout_view(request):
    """Log out the user and redirect to homepage."""
    logout(request)
    return redirect("/")


def _generate_verification_token(user_id, expiry_hours=24):
    """Generate a JWT token for email verification.

    Args:
        user_id: The user's primary key.
        expiry_hours: How many hours until the token expires (default 24).

    Returns:
        str: The encoded JWT token.
    """
    import datetime

    payload = {
        "user_id": user_id,
        "action": "verify_email",
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=expiry_hours),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _generate_password_reset_token(user_id, expiry_hours=1):
    """Generate a JWT token for password reset.

    Args:
        user_id: The user's primary key.
        expiry_hours: How many hours until the token expires (default 1).

    Returns:
        str: The encoded JWT token.
    """
    import datetime

    payload = {
        "user_id": user_id,
        "action": "password_reset",
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=expiry_hours),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _resolve_unverified_ttl_days():
    """Resolve the unverified-account grace period from operator config.

    Reads ``UNVERIFIED_USER_TTL_DAYS`` (Studio > Settings > Auth) with
    a 7-day fallback. Non-positive or non-numeric values fall back to
    the default so a typo cannot disable the feature accidentally.
    """
    raw = get_config(
        "UNVERIFIED_USER_TTL_DAYS",
        str(DEFAULT_UNVERIFIED_USER_TTL_DAYS),
    )
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_UNVERIFIED_USER_TTL_DAYS
    if days <= 0:
        return DEFAULT_UNVERIFIED_USER_TTL_DAYS
    return days


def _send_verification_email(user):
    """Send a verification email to the user using EmailService.

    Args:
        user: User model instance.

    Returns:
        EmailLog instance when SES accepted the send; ``None`` on failure.
    """
    token = _generate_verification_token(user.pk)
    site_url = site_base_url()
    verify_url = f"{site_url}/api/verify-email?token={token}"

    try:
        from email_app.services.email_service import EmailService

        service = EmailService()
        return service.send(user, "email_verification", {"verify_url": verify_url})
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)
        return None


def _render_verify_email_result(request, *, success, message, status=200):
    if success:
        cta_url = "/account/" if request.user.is_authenticated else "/accounts/login/"
        cta_label = (
            "Continue to Account"
            if request.user.is_authenticated
            else "Sign In"
        )
    else:
        cta_url = "/accounts/login/"
        cta_label = "Sign In"

    return render(
        request,
        "email_app/verify_result.html",
        {
            "success": success,
            "message": message,
            "cta_url": cta_url,
            "cta_label": cta_label,
        },
        status=status,
    )


def _probe_slack_membership_on_signup(user):
    """Synchronously probe Slack workspace membership during signup.

    Best-effort: any failure is logged at WARNING and swallowed so the
    signup path always reaches its 201 response. The 30-min periodic
    task ``refresh_slack_membership`` will pick up users left in
    ``slack_checked_at IS NULL`` state on the next cycle.
    """
    from django.utils import timezone

    try:
        from community.services import get_community_service

        service = get_community_service()
        outcome, uid = service.check_workspace_membership(user.email)
    except Exception:
        logger.warning(
            "Slack membership probe failed during signup for %s",
            user.email,
            exc_info=True,
        )
        return

    if outcome == "unknown":
        # Token unset, transient failure, or not configured —
        # leave fields untouched, periodic task will retry.
        return

    update_fields = ["slack_member", "slack_checked_at"]
    user.slack_checked_at = timezone.now()

    if outcome == "member":
        user.slack_member = True
        if uid and not user.slack_user_id:
            user.slack_user_id = uid
            update_fields.append("slack_user_id")
    else:
        user.slack_member = False

    try:
        user.save(update_fields=update_fields)
    except Exception:
        logger.warning(
            "Failed to persist Slack membership probe result for %s",
            user.email,
            exc_info=True,
        )


def _send_password_reset_email(user):
    """Send a password reset email to the user using EmailService.

    Args:
        user: User model instance.
    """
    token = _generate_password_reset_token(user.pk)
    site_url = site_base_url()
    reset_url = f"{site_url}/api/password-reset?token={token}"

    try:
        from email_app.services.email_service import EmailService

        service = EmailService()
        service.send(user, "password_reset", {"reset_url": reset_url})
    except Exception:
        logger.exception("Failed to send password reset email to %s", user.email)


@require_POST
def register_api(request):
    """Register a new user with email and password.

    Expects JSON body with:
        email: str - The user's email address.
        password: str - The user's password.

    Returns:
        201 with {"status": "ok", "message": "..."} on success.
        400 with {"error": "..."} on validation failure.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email:
        return JsonResponse({"error": "Email is required"}, status=400)
    if not password:
        return JsonResponse({"error": "Password is required"}, status=400)
    if len(password) < 8:
        return JsonResponse(
            {"error": "Password must be at least 8 characters"}, status=400
        )

    # Check if user already exists
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse(
            {"error": "A user with this email already exists"}, status=400
        )

    # Create user with email_verified=False and free tier. Issue #452:
    # set ``verification_expires_at`` so the daily purge job can clean
    # up email-only signups that never verify. Social signups go
    # through allauth and never hit this path, so the field stays NULL
    # for them — see ``accounts/signals.py``.
    ttl_days = _resolve_unverified_ttl_days()
    verification_expires_at = timezone.now() + datetime.timedelta(days=ttl_days)
    user = User.objects.create_user(
        email=email,
        password=password,
        verification_expires_at=verification_expires_at,
    )
    # email_verified defaults to False, tier defaults to free (in model save)

    # Best-effort Slack workspace membership probe. If the email is
    # already in Slack (rare on signup but possible: someone joined
    # the public Slack first, then signs up on the website later) we
    # set ``slack_member=True`` immediately so the dashboard skips the
    # "Join Slack" CTA on the very first dashboard render. Must NEVER
    # block signup — wrapped in try/except, ``unknown`` is a no-op.
    _probe_slack_membership_on_signup(user)

    # Send verification email
    _send_verification_email(user)

    return JsonResponse(
        {
            "status": "ok",
            "message": "Account created. Check your email to verify your address.",
        },
        status=201,
    )


def verify_email_api(request):
    """Verify a user's email address via JWT token.

    GET /api/verify-email?token={jwt}
    Sets email_verified=True if the token is valid.
    If the token contains a redirect_to URL (lead magnet flow),
    redirects to that URL after verification.
    """
    token = request.GET.get("token", "")
    if not token:
        return _render_verify_email_result(
            request,
            success=False,
            message="This verification link is incomplete. Please sign in to request a new verification email.",
            status=400,
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return _render_verify_email_result(
            request,
            success=False,
            message="This verification link has expired. Please sign in to request a new verification email.",
            status=400,
        )
    except jwt.InvalidTokenError:
        return _render_verify_email_result(
            request,
            success=False,
            message="This verification link is invalid. Please sign in to request a new verification email.",
            status=400,
        )

    if payload.get("action") != "verify_email":
        return _render_verify_email_result(
            request,
            success=False,
            message="This verification link is invalid. Please sign in to request a new verification email.",
            status=400,
        )

    user_id = payload.get("user_id")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return _render_verify_email_result(
            request,
            success=False,
            message="We could not find an account for this verification link. Please sign in or create a new account.",
            status=404,
        )

    if not user.email_verified:
        user.email_verified = True
        # Issue #452: a successful verification cancels the auto-purge
        # window. Clear ``verification_expires_at`` so the daily job
        # leaves this user alone forever.
        user.verification_expires_at = None
        user.save(
            update_fields=["email_verified", "verification_expires_at"],
        )
    elif user.verification_expires_at is not None:
        # Defensive: if a user is already verified but still has an
        # expiry hanging around (e.g. legacy data), clear it.
        user.verification_expires_at = None
        user.save(update_fields=["verification_expires_at"])

    # Check for redirect_to (lead magnet flow from newsletter subscribe)
    redirect_to = payload.get("redirect_to")
    if redirect_to:
        return redirect(redirect_to)

    return _render_verify_email_result(
        request,
        success=True,
        message="Your email address is verified. You can continue to your account.",
    )


@require_POST
def login_api(request):
    """Authenticate a user with email and password.

    Expects JSON body with:
        email: str - The user's email address.
        password: str - The user's password.

    Returns:
        200 with {"status": "ok"} on success.
        401 with {"error": "..."} on failure.
    """
    started_at = time.perf_counter()
    outcome = "unknown"
    try:
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            outcome = "invalid_json"
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not email or not password:
            outcome = "missing_fields"
            return JsonResponse(
                {"error": "Email and password are required"}, status=400
            )

        # Before issue #371, authenticate() fell through every configured
        # backend. The plain email/password endpoint only needs ModelBackend;
        # calling it directly preserves Django's password hash and dummy-hash
        # protections while avoiding duplicate allauth backend work.
        user = EMAIL_PASSWORD_BACKEND.authenticate(
            request,
            username=email,
            password=password,
        )
        if user is None:
            outcome = "invalid_credentials"
            return JsonResponse({"error": INVALID_LOGIN_ERROR}, status=401)

        login(request, user, backend=EMAIL_PASSWORD_AUTH_BACKEND)
        outcome = "success"
        response_data = {"status": "ok"}
        # Push server-side theme preference to client on login
        if user.theme_preference:
            response_data["theme_preference"] = user.theme_preference
        return JsonResponse(response_data)
    finally:
        _log_login_timing(outcome, started_at)


@require_POST
def password_reset_request_api(request):
    """Request a password reset email.

    Expects JSON body with:
        email: str - The user's email address.

    Always returns 200 to not reveal whether the email exists.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    email = data.get("email", "").strip().lower()
    if not email:
        return JsonResponse({"error": "Email is required"}, status=400)

    # Always return success to not reveal whether user exists
    try:
        user = User.objects.get(email__iexact=email)
        _send_password_reset_email(user)
    except User.DoesNotExist:
        pass

    return JsonResponse(
        {
            "status": "ok",
            "message": "If an account exists with that email, a password reset link has been sent.",
        }
    )


def password_reset_api(request):
    """Handle password reset: GET renders form, POST resets password.

    GET /api/password-reset?token={jwt} - renders password reset form
    POST /api/password-reset with JSON {token, new_password} - resets password
    """
    if request.method == "GET":
        token = request.GET.get("token", "")
        if not token:
            return JsonResponse({"error": "Token is required"}, status=400)

        # Validate token before rendering form
        try:
            jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return render(
                request,
                "accounts/password_reset.html",
                {"error": "This password reset link has expired. Please request a new one."},
            )
        except jwt.InvalidTokenError:
            return render(
                request,
                "accounts/password_reset.html",
                {"error": "Invalid password reset link."},
            )

        return render(
            request, "accounts/password_reset.html", {"token": token}
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        token = data.get("token", "")
        new_password = data.get("new_password", "")

        if not token:
            return JsonResponse({"error": "Token is required"}, status=400)
        if not new_password:
            return JsonResponse(
                {"error": "New password is required"}, status=400
            )
        if len(new_password) < 8:
            return JsonResponse(
                {"error": "Password must be at least 8 characters"},
                status=400,
            )

        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
            )
        except jwt.ExpiredSignatureError:
            return JsonResponse({"error": "Token has expired"}, status=400)
        except jwt.InvalidTokenError:
            return JsonResponse({"error": "Invalid token"}, status=400)

        if payload.get("action") != "password_reset":
            return JsonResponse({"error": "Invalid token action"}, status=400)

        user_id = payload.get("user_id")
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=404)

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return JsonResponse(
            {"status": "ok", "message": "Password has been reset successfully."}
        )

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@require_POST
def change_password_api(request):
    """Change the authenticated user's password.

    Expects JSON body with:
        current_password: str - The user's current password.
        new_password: str - The new password.

    Returns:
        200 with {"status": "ok"} on success.
        400 with {"error": "..."} on failure.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")

    if not current_password:
        return JsonResponse(
            {"error": "Current password is required"}, status=400
        )
    if not new_password:
        return JsonResponse(
            {"error": "New password is required"}, status=400
        )
    if len(new_password) < 8:
        return JsonResponse(
            {"error": "New password must be at least 8 characters"},
            status=400,
        )

    user = request.user

    # For users who signed up via OAuth and don't have a usable password,
    # allow them to set a password without current_password check
    if user.has_usable_password():
        if not user.check_password(current_password):
            return JsonResponse(
                {"error": "Current password is incorrect"}, status=400
            )

    user.set_password(new_password)
    user.save(update_fields=["password"])

    # Re-login so the session is not invalidated
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    return JsonResponse(
        {"status": "ok", "message": "Password changed successfully."}
    )
