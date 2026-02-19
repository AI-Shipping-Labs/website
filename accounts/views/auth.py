"""Authentication views for email+password and OAuth login flows."""

import json
import logging

import jwt
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User

logger = logging.getLogger(__name__)

# JWT algorithm
JWT_ALGORITHM = "HS256"


def login_view(request):
    """Render the login page with Google and GitHub OAuth buttons."""
    if request.user.is_authenticated:
        return redirect("/")
    return render(request, "accounts/login.html")


def register_view(request):
    """Render the registration page."""
    if request.user.is_authenticated:
        return redirect("/")
    return render(request, "accounts/register.html")


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


def _send_verification_email(user):
    """Send a verification email to the user using EmailService.

    Args:
        user: User model instance.
    """
    token = _generate_verification_token(user.pk)
    site_url = getattr(settings, "SITE_URL", "https://aishippinglabs.com")
    verify_url = f"{site_url}/api/verify-email?token={token}"

    try:
        from email_app.services.email_service import EmailService

        service = EmailService()
        service.send(user, "email_verification", {"verify_url": verify_url})
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)


def _send_password_reset_email(user):
    """Send a password reset email to the user using EmailService.

    Args:
        user: User model instance.
    """
    token = _generate_password_reset_token(user.pk)
    site_url = getattr(settings, "SITE_URL", "https://aishippinglabs.com")
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

    # Create user with email_verified=False and free tier
    user = User.objects.create_user(email=email, password=password)
    # email_verified defaults to False, tier defaults to free (in model save)

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
        return JsonResponse({"error": "Token is required"}, status=400)

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return JsonResponse({"error": "Token has expired"}, status=400)
    except jwt.InvalidTokenError:
        return JsonResponse({"error": "Invalid token"}, status=400)

    if payload.get("action") != "verify_email":
        return JsonResponse({"error": "Invalid token action"}, status=400)

    user_id = payload.get("user_id")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])

    # Check for redirect_to (lead magnet flow from newsletter subscribe)
    redirect_to = payload.get("redirect_to")
    if redirect_to:
        return redirect(redirect_to)

    return JsonResponse(
        {"status": "ok", "message": "Email verified successfully."}
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
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return JsonResponse(
            {"error": "Email and password are required"}, status=400
        )

    user = authenticate(request, email=email, password=password)
    if user is None:
        return JsonResponse(
            {"error": "Invalid email or password"}, status=401
        )

    login(request, user)
    return JsonResponse({"status": "ok"})


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
