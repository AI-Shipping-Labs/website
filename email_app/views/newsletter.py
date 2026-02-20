"""Newsletter subscribe, unsubscribe, and subscribe page views."""

import json
import logging

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

User = get_user_model()

JWT_ALGORITHM = "HS256"


def _generate_verification_token(user_id, redirect_to=None, expiry_hours=24):
    """Generate a JWT token for email verification.

    Args:
        user_id: The user's primary key.
        redirect_to: Optional URL to redirect to after verification (lead magnet).
        expiry_hours: Hours until the token expires (default 24).

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
    if redirect_to:
        payload["redirect_to"] = redirect_to
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _generate_unsubscribe_token(user_id):
    """Generate a JWT token for unsubscribe (no expiry).

    Args:
        user_id: The user's primary key.

    Returns:
        str: The encoded JWT token.
    """
    payload = {
        "user_id": user_id,
        "action": "unsubscribe",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _send_subscribe_verification_email(user, redirect_to=None):
    """Send a verification email for newsletter signup.

    If redirect_to is provided (lead magnet flow), the verification email
    includes a download link and the verify URL redirects to the download.

    Args:
        user: User model instance.
        redirect_to: Optional download URL for lead magnet flow.
    """
    token = _generate_verification_token(user.pk, redirect_to=redirect_to)
    site_url = getattr(settings, "SITE_URL", "https://aishippinglabs.com")
    verify_url = f"{site_url}/api/verify-email?token={token}"

    try:
        from email_app.services.email_service import EmailService

        service = EmailService()

        if redirect_to:
            # Lead magnet flow: send the lead magnet delivery template
            # with both verify URL and download URL
            service.send(
                user,
                "lead_magnet_delivery",
                {
                    "verify_url": verify_url,
                    "download_url": verify_url,
                    "resource_title": "your resource",
                },
            )
        else:
            # Standard newsletter signup
            service.send(
                user,
                "email_verification",
                {"verify_url": verify_url},
            )
    except Exception:
        logger.exception(
            "Failed to send verification email to %s", user.email
        )


@require_POST
def subscribe_api(request):
    """Subscribe to the newsletter.

    POST /api/subscribe with JSON body: {"email": "..."}
    Optionally include "redirect_to" for lead magnet flow.

    If new email: creates user with tier=free, sends verification email.
    If existing email: returns 200 with same message (no information leak).
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    email = data.get("email", "").strip().lower()
    redirect_to = data.get("redirect_to", "").strip()

    if not email:
        return JsonResponse({"error": "Email is required"}, status=400)

    # Basic email format validation
    if "@" not in email or "." not in email.split("@")[-1]:
        return JsonResponse({"error": "Invalid email address"}, status=400)

    # Check if user already exists
    try:
        existing_user = User.objects.get(email__iexact=email)
        # Idempotent: if the user exists but is not verified, re-send verification
        if not existing_user.email_verified:
            _send_subscribe_verification_email(
                existing_user, redirect_to=redirect_to or None
            )
        # Return same success message regardless (no information leak)
    except User.DoesNotExist:
        # Create new user with free tier
        user = User.objects.create_user(email=email)
        _send_subscribe_verification_email(
            user, redirect_to=redirect_to or None
        )

    return JsonResponse(
        {
            "status": "ok",
            "message": "Check your email to confirm your subscription.",
        }
    )


def unsubscribe_api(request):
    """Unsubscribe a user from all emails via JWT token.

    GET /api/unsubscribe?token={jwt}
    Sets unsubscribed=True. Token does not expire.
    """
    token = request.GET.get("token", "")
    if not token:
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": "Token is required.",
            },
        )

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": "Invalid unsubscribe link.",
            },
        )

    if payload.get("action") != "unsubscribe":
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": "Invalid unsubscribe link.",
            },
        )

    user_id = payload.get("user_id")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": "User not found.",
            },
        )

    if not user.unsubscribed:
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])

    return render(
        request,
        "email_app/unsubscribe_result.html",
        {
            "success": True,
            "message": "You have been unsubscribed from all emails.",
        },
    )


def subscribe_page(request):
    """Render the dedicated /subscribe page with the subscribe form."""
    return render(request, "email_app/subscribe.html")
