"""Newsletter subscribe, unsubscribe, and subscribe page views."""

import datetime
import json
import logging

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from accounts.return_context import sanitize_verification_return_path
from accounts.services.auth_throttle import SCOPE_SUBSCRIBE, consume_auth_throttle
from accounts.services.user_creation import create_user_conflict_safe
from accounts.services.verification import resolve_unverified_ttl_days
from accounts.utils.tokens import JWT_ALGORITHM, generate_user_action_token
from integrations.config import site_base_url

logger = logging.getLogger(__name__)

User = get_user_model()


def _generate_verification_token(user_id, redirect_to=None, expiry_hours=24):
    """Generate a JWT token for email verification.

    Args:
        user_id: The user's primary key.
        redirect_to: Optional URL to redirect to after verification (lead magnet).
        expiry_hours: Hours until the token expires (default 24).

    Returns:
        str: The encoded JWT token.
    """
    redirect_to = sanitize_verification_return_path(redirect_to, default="") or None
    return generate_user_action_token(
        user_id,
        "verify_email",
        expiry_hours=expiry_hours,
        redirect_to=redirect_to,
    )


def _generate_unsubscribe_token(user_id):
    """Generate a JWT token for unsubscribe (no expiry).

    Args:
        user_id: The user's primary key.

    Returns:
        str: The encoded JWT token.
    """
    return generate_user_action_token(user_id, "unsubscribe")


def _send_subscribe_verification_email(user, redirect_to=None):
    """Send a verification email for newsletter signup.

    If redirect_to is provided (lead magnet flow), the verification email
    includes a download link and the verify URL redirects to the download.

    Args:
        user: User model instance.
        redirect_to: Optional download URL for lead magnet flow.
    """
    redirect_to = sanitize_verification_return_path(
        redirect_to,
        default="",
    ) or None
    token = _generate_verification_token(user.pk, redirect_to=redirect_to)
    site_url = site_base_url()
    verify_url = f"{site_url}/api/verify-email?token={token}"
    ttl_days = resolve_unverified_ttl_days()

    from email_app.services.email_service import EmailService, EmailServiceError

    try:
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
                    "site_url": site_url,
                    "ttl_days": ttl_days,
                },
            )
        else:
            # Standard newsletter signup. Issue #513: pass ``ttl_days`` and
            # ``site_url`` so the verification template can disclose the
            # auto-deletion window for unverified accounts and link the
            # user to ``/accounts/login/``.
            service.send(
                user,
                "email_verification_subscribe",
                {
                    "verify_url": verify_url,
                    "site_url": site_url,
                    "ttl_days": ttl_days,
                },
            )
    except EmailServiceError:
        logger.exception(
            "Failed to send verification email to %s (user_id=%s, redirect_to=%s)",
            user.email,
            user.pk,
            redirect_to,
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
    redirect_to = sanitize_verification_return_path(
        data.get("redirect_to", ""),
        request=request,
        default="",
    )

    if not email:
        return JsonResponse({"error": "Email is required"}, status=400)

    # Basic email format validation
    if "@" not in email or "." not in email.split("@")[-1]:
        return JsonResponse({"error": "Invalid email address"}, status=400)

    throttled = consume_auth_throttle(request, email, SCOPE_SUBSCRIBE)
    if throttled is not None:
        return throttled

    # Keep the existing-user lookup as a fast path. The creation helper is the
    # authoritative collision guard when two requests miss this lookup.
    try:
        user = User.objects.get(email__iexact=email)
        created = False
    except User.DoesNotExist:
        # Issue #513: parity with ``register_api``. Set
        # ``verification_expires_at`` so the daily purge job (#452)
        # cleans up newsletter signups that never verify, just like it
        # already does for email+password signups. Without this the
        # subscribe endpoint silently creates immortal unverified rows.
        ttl_days = resolve_unverified_ttl_days()
        verification_expires_at = (
            timezone.now() + datetime.timedelta(days=ttl_days)
        )
        user, created = create_user_conflict_safe(
            email=email,
            verification_expires_at=verification_expires_at,
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
        )

    if created or not user.email_verified:
        # A collision loser follows the same resend semantics as the sequential
        # existing-user path. Never extend the winner's original purge window.
        _send_subscribe_verification_email(
            user, redirect_to=redirect_to or None
        )

    return JsonResponse(
        {
            "status": "ok",
            # Issue #513: the on-site message must mention "account" so
            # the user is not surprised when the verification email also
            # tells them an account was created.
            "message": (
                "Thanks! We've created a free account and emailed a "
                "verification link. Click it to confirm your subscription "
                "and activate your account."
            ),
        }
    )


@csrf_exempt
def unsubscribe_api(request):
    """Unsubscribe a user from the newsletter and marketing email via JWT token.

    GET /api/unsubscribe?token={jwt} renders the confirmation page.
    POST /api/unsubscribe?token={jwt} supports mailbox-provider one-click
    unsubscribe callbacks and returns a plain 200/400 response.
    """
    token = request.GET.get("token", "")
    if not token:
        message = "Token is required."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": message,
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
        message = "Invalid unsubscribe link."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": message,
            },
        )

    if payload.get("action") != "unsubscribe":
        message = "Invalid unsubscribe link."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": message,
            },
        )

    user_id = payload.get("user_id")
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        message = "User not found."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(
            request,
            "email_app/unsubscribe_result.html",
            {
                "success": False,
                "message": message,
            },
        )

    # Issue #1593: mirror the preference the same way ``email_preferences_view``
    # does. ``unsubscribed`` is the enforced flag, but ``email_preferences
    # ["newsletter"]`` is what the users API and the CRM export publish; now
    # that Maven writes ``newsletter: True`` at creation, leaving the mirror
    # stale would publish a contradiction. Only the newsletter key is touched —
    # scoped preferences such as ``maven_emails`` are the member's separate
    # choice and are left exactly as they are.
    preferences = dict(user.email_preferences or {})
    if not user.unsubscribed or preferences.get("newsletter") is not False:
        preferences["newsletter"] = False
        user.unsubscribed = True
        user.email_preferences = preferences
        user.save(update_fields=["unsubscribed", "email_preferences"])

    if request.method == "POST":
        return HttpResponse("Unsubscribed", content_type="text/plain")

    return render(
        request,
        "email_app/unsubscribe_result.html",
        {
            "success": True,
            # Issue #1593: the old copy claimed "all emails", which was never
            # true — the flag gates promotional sends only, and essential
            # account and course email keeps flowing. In a Maven welcome that
            # separately offers a course-email opt-out, the old claim actively
            # contradicted the email it came from.
            "message": (
                "You have been unsubscribed from our newsletter and other "
                "marketing emails. Essential account and course emails still "
                "reach you."
            ),
        },
    )


@csrf_exempt
def verify_and_subscribe_api(request):
    """Verify the address AND subscribe to the newsletter, in one click.

    Issue #1593: the Maven welcome email says "if you want to hear from us,
    verify your email". Verification and subscription are separate fields, so
    honouring that sentence means one action has to do both — otherwise the
    email promises something the system does not deliver, which is worse than
    saying nothing.

    This is a SIBLING of ``/api/verify-email``, not an intent flag on it. The
    token's action name is the consent record: a ``verify_email`` token can
    never subscribe anyone, no matter how the endpoint is later refactored or
    how the query string is tampered with. The inverse also holds by design —
    signing in with OAuth or setting a password verifies the address and
    deliberately does NOT subscribe, because proving you own a mailbox is not
    the same as asking to be marketed to.
    """
    token = request.GET.get("token", "")
    if not token:
        return _opt_in_failure(request, "This link is incomplete.")

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        return _opt_in_failure(
            request,
            "This subscribe link has expired.",
        )
    except jwt.InvalidTokenError:
        return _opt_in_failure(request, "This subscribe link is invalid.")

    if payload.get("action") != "verify_and_subscribe":
        return _opt_in_failure(request, "This subscribe link is invalid.")

    try:
        user = User.objects.get(pk=payload.get("user_id"))
    except User.DoesNotExist:
        return _opt_in_failure(request, "User not found.")

    preferences = dict(user.email_preferences or {})
    preferences["newsletter"] = True
    user.email_preferences = preferences
    user.unsubscribed = False
    user.email_verified = True
    # A verified address is never auto-purged: issue #452's window only
    # applies to accounts that never confirmed. Clearing it here matches
    # ``verify_email_api``.
    user.verification_expires_at = None
    user.save(
        update_fields=[
            "email_preferences",
            "unsubscribed",
            "email_verified",
            "verification_expires_at",
        ]
    )

    message = (
        "Your email is verified and you're subscribed to the AI Shipping Labs "
        "newsletter — community news, new workshops, and events. You can "
        "unsubscribe at any time."
    )
    if request.method == "POST":
        return HttpResponse(message, content_type="text/plain")
    return render(
        request,
        "email_app/unsubscribe_result.html",
        {
            "success": True,
            "message": message,
            "result_heading": "You're subscribed",
            # The exit is on the same page as the opt-in. Consent that is
            # hard to withdraw is not really consent, and this reader has no
            # password yet, so it must be the no-login token link.
            "unsubscribe_url": (
                "/api/unsubscribe?token="
                f'{generate_user_action_token(user.pk, "unsubscribe")}'
            ),
        },
    )


def _opt_in_failure(request, message):
    """Render the shared token-result page in its failure state.

    Issue #1593: someone who clicked "verify your email" asked for something,
    and a dead end is the wrong answer to a deliberate act. An expired or
    malformed link therefore names the same outcome they wanted and points at
    the signed-in route to it, mirroring how ``verify_email_api`` sends an
    unusable link back to a usable path. No new email send exists here — the
    account email preferences already own this setting, so recovery costs the
    member one sign-in and nothing else.
    """
    recovery = (
        " You can still turn the newsletter on yourself from your account"
        " email preferences."
    )
    if request.method == "POST":
        return HttpResponse(message, status=400, content_type="text/plain")
    return render(
        request,
        "email_app/unsubscribe_result.html",
        {
            "success": False,
            "message": message + recovery,
            "error_heading": "Subscribe failed",
            "recovery_url": "/account/",
            "recovery_label": "Go to email preferences",
        },
        status=400,
    )


@csrf_exempt
def maven_email_opt_out(request):
    """Disable only Maven course emails; membership and other mail stay intact."""
    token = request.GET.get("token", "")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        payload = {}
    if payload.get("action") != "maven_email_opt_out":
        message = "Invalid Maven email preference link."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(request, "email_app/unsubscribe_result.html", {"success": False, "message": message})
    try:
        user = User.objects.get(pk=payload.get("user_id"))
    except User.DoesNotExist:
        message = "User not found."
        if request.method == "POST":
            return HttpResponse(message, status=400, content_type="text/plain")
        return render(request, "email_app/unsubscribe_result.html", {"success": False, "message": message})
    preferences = dict(user.email_preferences or {})
    preferences["maven_emails"] = False
    user.email_preferences = preferences
    user.save(update_fields=["email_preferences"])
    message = "Maven course emails are off. Your course and community access are unchanged. You can turn them on again from Account."
    if request.method == "POST":
        return HttpResponse(message, content_type="text/plain")
    return render(request, "email_app/unsubscribe_result.html", {"success": True, "message": message})


@ensure_csrf_cookie
def subscribe_page(request):
    """Render the dedicated /subscribe page with the subscribe form."""
    return render(
        request,
        "email_app/subscribe.html",
        {"hide_footer_newsletter": True},
    )
