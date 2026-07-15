"""Authentication views for email+password and OAuth login flows."""

import datetime
import json
import logging
import time
from urllib.parse import urlencode

import jwt
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
from accounts.models.user import SIGNUP_SOURCE_SIGNUP
from accounts.oauth_context import get_oauth_provider_context
from accounts.return_context import (
    append_next,
    get_next_url,
    sanitize_next_url,
    sanitize_verification_return_path,
    should_skip_logout_redirect,
)
from accounts.services.email_resolution import resolve_user_by_email
from accounts.services.free_welcome import send_free_welcome_email
from accounts.services.verification import resolve_unverified_ttl_days
from accounts.utils.tokens import JWT_ALGORITHM, generate_user_action_token
from integrations.config import site_base_url

logger = logging.getLogger(__name__)

EMAIL_PASSWORD_AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"
EMAIL_PASSWORD_BACKEND = ModelBackend()
INVALID_LOGIN_ERROR = "Invalid email or password"


def _private_no_store(response):
    """Keep verification tokens and continuations out of shared caches."""
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    return response


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
    next_url = get_next_url(request, default="/")
    if request.user.is_authenticated:
        return redirect(next_url)

    context = get_oauth_provider_context()
    context["next_url"] = next_url if next_url != "/" else ""
    context["hide_footer_newsletter"] = True
    return render(request, "accounts/login.html", context)


@ensure_csrf_cookie
def register_view(request):
    """Render the registration page."""
    next_url = get_next_url(request, default="/")
    if request.user.is_authenticated:
        return redirect(next_url)
    context = get_oauth_provider_context()
    context["next_url"] = next_url if next_url != "/" else ""
    context["hide_footer_newsletter"] = True
    return render(request, "accounts/register.html", context)


@ensure_csrf_cookie
def password_reset_request_view(request):
    """Render the public password-reset request page.

    Issue #769: the newsletter-only ``/account/`` CTA links here with
    ``?email=<user.email>`` so the reset form is pre-populated. We keep
    that behaviour available even for already-authenticated users (the
    redirect to ``/account/`` only fires when no ``email`` querystring
    is present) so the trimmed CTA flow works without forcing a logout.
    """
    prefill_email = (request.GET.get("email") or "").strip()
    if request.user.is_authenticated and not prefill_email:
        return redirect("/account/")
    return _private_no_store(render(
        request,
        "accounts/password_reset_request.html",
        {
            "prefill_email": prefill_email,
            "hide_footer_newsletter": True,
        },
    ))


def signup_redirect_view(request):
    """Redirect legacy signup/register shortcuts while preserving ``next``."""
    return redirect(append_next("/accounts/register/", get_next_url(request, default="")))


def logout_view(request):
    """Log out the user and redirect.

    Honours a sanitized ``?next=`` query parameter so users who sign out
    from a public detail page (event/course/workshop/blog/etc.) stay on
    the same page and can inspect the anonymous view. When ``next`` is
    missing, malformed, off-site, or points at a member-only/admin
    surface — ``/account``, ``/accounts``, ``/studio``, ``/admin``,
    ``/notifications`` — the user is sent to ``/`` instead. Issue #519.
    """
    logout(request)
    next_url = get_next_url(request, default="/")
    if next_url == "/" or should_skip_logout_redirect(next_url):
        return redirect("/")
    return redirect(next_url)


def _generate_verification_token(user_id, expiry_hours=24, return_path=None):
    """Generate a JWT token for email verification.

    Args:
        user_id: The user's primary key.
        expiry_hours: How many hours until the token expires (default 24).
        return_path: Optional safe same-site path to redirect to after
            successful verification.

    Returns:
        str: The encoded JWT token.
    """
    safe_return_path = sanitize_verification_return_path(return_path, default="")
    return generate_user_action_token(
        user_id,
        "verify_email",
        expiry_hours=expiry_hours,
        return_path=safe_return_path,
    )


def _generate_password_reset_token(user_id, expiry_hours=1):
    """Generate a JWT token for password reset.

    Args:
        user_id: The user's primary key.
        expiry_hours: How many hours until the token expires (default 1).

    Returns:
        str: The encoded JWT token.
    """
    return generate_user_action_token(
        user_id,
        "password_reset",
        expiry_hours=expiry_hours,
    )


def _send_verification_email(user, return_path=None):
    """Send a verification email to the user using EmailService.

    Args:
        user: User model instance.
        return_path: Optional safe same-site path to include in the signed
            verification token.

    Returns:
        EmailLog instance when SES accepted the send; ``None`` on failure.
    """
    token = _generate_verification_token(user.pk, return_path=return_path)
    site_url = site_base_url()
    verify_url = f"{site_url}/api/verify-email?token={token}"
    ttl_days = resolve_unverified_ttl_days()

    from email_app.services.email_service import EmailService, EmailServiceError

    try:
        service = EmailService()
        return service.send(
            user,
            "email_verification_signup",
            {
                "verify_url": verify_url,
                "site_url": site_url,
                "ttl_days": ttl_days,
            },
        )
    except EmailServiceError:
        logger.exception(
            "Failed to send verification email to %s (user_id=%s)",
            user.email,
            user.pk,
        )
        return None


def _render_verify_email_result(request, *, success, message, status=200):
    verified_user = None
    if success and hasattr(request, "_verified_email_user"):
        verified_user = request._verified_email_user

    if (
        success
        and verified_user is not None
        and verified_user.signup_source == "newsletter"
        and not verified_user.account_activated
        and not verified_user.has_usable_password()
    ):
        cta_url = (
            "/accounts/password-reset-request?"
            + urlencode({"email": verified_user.email})
        )
        cta_label = "Set a password"
    elif success:
        cta_url = "/account/" if request.user.is_authenticated else "/accounts/login/"
        cta_label = (
            "Continue to Account"
            if request.user.is_authenticated
            else "Sign In"
        )
    else:
        cta_url = "/accounts/login/"
        cta_label = "Sign In"

    return _private_no_store(
        render(
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
        # Issue #709: backfill first_name/last_name from the Slack profile
        # in the same DB round-trip. This adds ONE extra Slack API call
        # (``users.lookupByEmail`` via ``lookup_user_profile_by_email``)
        # to the signup latency budget — only on the ``member`` branch
        # (``not_member`` / ``unknown`` skip it, zero extra cost there).
        # Typical Slack ``users.lookupByEmail`` latency is ~80-300 ms;
        # the signup path already pays one such call via
        # ``check_workspace_membership`` above, so this is a 2x of the
        # existing Slack budget on the ``member`` branch only. Helper
        # swallows its own exceptions (returns False on lookup failure),
        # so signup never fails because of the profile call.
        from community.tasks.slack_membership import _backfill_name_from_slack
        if _backfill_name_from_slack(service, user):
            update_fields.extend(["first_name", "last_name"])
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

    from email_app.services.email_service import EmailService, EmailServiceError

    try:
        service = EmailService()
        service.send(user, "password_reset", {"reset_url": reset_url})
    except EmailServiceError:
        logger.exception(
            "Failed to send password reset email to %s (user_id=%s)",
            user.email,
            user.pk,
        )


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
    next_url = sanitize_verification_return_path(
        data.get("next", ""), request=request, default=""
    )
    redirect_url = next_url or "/"

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
    ttl_days = resolve_unverified_ttl_days()
    verification_expires_at = timezone.now() + datetime.timedelta(days=ttl_days)
    user = User.objects.create_user(
        email=email,
        password=password,
        verification_expires_at=verification_expires_at,
        signup_source="signup",
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
    if next_url:
        _send_verification_email(user, return_path=next_url)
    else:
        _send_verification_email(user)

    login(request, user, backend=EMAIL_PASSWORD_AUTH_BACKEND)

    message = "Account created. Check your email to verify your address."
    if next_url:
        message = (
            "Account created. Check your email to verify your address. "
            "The verification link returns you to this content and unlocks it."
        )

    return JsonResponse(
        {
            "status": "ok",
            "message": message,
            "redirect_url": redirect_url,
            "return_url": next_url,
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
        # Issue #768: an email+password signup completing verification
        # is the canonical "first real platform action" — flip
        # ``account_activated``. Newsletter-only subscribers
        # (signup_source='newsletter') intentionally stay inactive
        # because they have never set a password and have not done
        # anything else.
        from accounts.utils.activation import mark_activated

        if user.signup_source == SIGNUP_SOURCE_SIGNUP:
            mark_activated(user)
    if user.signup_source == SIGNUP_SOURCE_SIGNUP:
        send_free_welcome_email(user)
    request._verified_email_user = user

    # Content gates sign a same-site return path into signup/resend
    # verification links. Newsletter lead-magnet links use the legacy
    # ``redirect_to`` payload field; sanitize both defensively before
    # redirecting because the token source was originally user input.
    return_path = sanitize_verification_return_path(
        payload.get("return_path"), request=request, default=""
    )
    if return_path:
        return _private_no_store(redirect(return_path))

    redirect_to = sanitize_verification_return_path(
        payload.get("redirect_to"), request=request, default=""
    )
    if redirect_to:
        return _private_no_store(redirect(redirect_to))

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

        # Resolve the typed email to the CANONICAL active account BEFORE
        # checking the password (#845). ``resolve_user_by_email`` is the single
        # source of truth: an active primary login wins, else the owner of a
        # matching EmailAlias (canonical), else None. This is why a merged-away
        # (aliased) email signs into the surviving canonical account, and why a
        # deactivated secondary is NEVER authenticated directly -- we only ever
        # check the password against the resolved canonical user.
        #
        # We do NOT delegate to ModelBackend.authenticate: it keys on the typed
        # email via get_by_natural_key and then drops inactive rows in
        # user_can_authenticate, so an alias email would only ever find the dead
        # secondary and fail. Resolving canonical-first is cleaner and never
        # re-opens the inactive-user gate.
        canonical = resolve_user_by_email(email)
        password_ok = (
            canonical is not None
            and canonical.has_usable_password()
            and canonical.check_password(password)
        )
        if not password_ok:
            # Constant-time guard: when no usable canonical password was
            # checked, run a throwaway hash so the unknown-email / inactive /
            # unusable-password branches take a comparable amount of time to a
            # wrong-password branch (mirrors ModelBackend.set_password timing
            # protection). No branch reveals the alias relationship.
            if canonical is None or not canonical.has_usable_password():
                User().set_password(password)
            outcome = "invalid_credentials"
            return JsonResponse({"error": INVALID_LOGIN_ERROR}, status=401)

        user = canonical
        login(request, user, backend=EMAIL_PASSWORD_AUTH_BACKEND)
        outcome = "success"
        response_data = {
            "status": "ok",
            "redirect_url": sanitize_next_url(data.get("next", ""), default="/"),
        }
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

    # Always return success to not reveal whether user exists. Resolve the
    # typed email to the CANONICAL account (#845): an alias email resolves to
    # canonical and the reset is sent to canonical's PRIMARY verified email
    # (EmailService.send targets user.email), never the typed alias address.
    user = resolve_user_by_email(email)
    if user is not None:
        _send_password_reset_email(user)

    return JsonResponse(
        {
            "status": "ok",
            "message": "If an account exists for that email, we’ll send password reset instructions shortly.",
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

        # Validate token before rendering form. The email is included as a
        # hidden username hint so browser password managers can associate the
        # generated password with the right account.
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM]
            )
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

        if payload.get("action") != "password_reset":
            return render(
                request,
                "accounts/password_reset.html",
                {"error": "Invalid password reset link."},
            )

        try:
            user = User.objects.get(pk=payload.get("user_id"))
        except User.DoesNotExist:
            return render(
                request,
                "accounts/password_reset.html",
                {"error": "Invalid password reset link."},
            )

        return render(
            request,
            "accounts/password_reset.html",
            {"token": token, "reset_email": user.email},
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

        # Issue #769: completing the password-reset flow IS the
        # activation path for newsletter-only subscribers — once they
        # have a password they're real platform users. ``mark_activated``
        # is idempotent so this is a no-op for users who were already
        # activated by some other trigger (#768).
        from accounts.utils.activation import mark_activated
        mark_activated(user)

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

    # Issue #769: changing a password is also an activation event so a
    # newsletter-only subscriber who lands here (after the
    # password-reset link auto-signed them in, for example) flips out
    # of the gated state. Idempotent for already-activated users.
    from accounts.utils.activation import mark_activated
    mark_activated(user)

    # Re-login so the session is not invalidated
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    return JsonResponse(
        {"status": "ok", "message": "Password changed successfully."}
    )
