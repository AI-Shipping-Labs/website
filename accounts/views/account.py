"""Account page view and email preferences API."""

import json
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

# Issue #448: per-user resend throttle for the account verification banner.
RESEND_VERIFICATION_THROTTLE_SECONDS = 60
RESEND_VERIFICATION_CACHE_KEY_TEMPLATE = "verify-email-resend:{user_id}"

# Matches AbstractUser.first_name / last_name max_length so an over-long
# value is rejected before Django's model-level validator complains. Keeping
# this in sync with AbstractUser is intentional -- the constraint is
# implicit in the column type, so we keep one named constant in the view.
_NAME_MAX_LENGTH = 150

logger = logging.getLogger(__name__)

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from content.access import get_active_override
from email_app.models import EmailLog
from integrations.config import get_config
from payments.models import Tier
from payments.tier_state import build_tier_state

# Issue #581: ``build_tier_state`` is shared with the pricing page where the
# steady-state badges and the duplicate "your plan changes" notes are useful.
# On ``/account/`` we only want the frame to fire when it carries information
# the user cannot already read off the rest of the membership card. The
# dedicated amber/red notices below already cover pending downgrade and
# pending cancellation, and the tier name itself already communicates the
# steady-state Free / Current-plan case.
#
# A (badge, note) pair is "steady-state" when it matches one of these exact
# tuples. Anything else (override-active note, stale-subscription warning,
# scheduled-change badge) carries new information and is rendered as-is.
_STEADY_STATE_PAIRS = frozenset({
    ("Current free plan", "You are on the free membership."),
    ("Current plan", ""),
})


def _suppress_steady_state_plan_state(
    state, *, is_pending_downgrade, is_pending_cancellation
):
    """Return ``{}`` for steady-state plan-state frames, else ``state``.

    The frame is suppressed when:

    - There is a pending downgrade or pending cancellation -- the
      dedicated amber/red notice below already shows the same message,
      so echoing it in the plan-state frame is duplicate noise.
    - The (badge, note) pair is one of the steady-state tuples in
      :data:`_STEADY_STATE_PAIRS`. The tier name in the Membership
      section already communicates this.

    Override messages (badge ``"Current plan"`` with a non-empty note
    such as ``"Base subscription. Temporary X access is active."``),
    stale-subscription warnings, and scheduled-future-change badges all
    fall outside the steady-state set, so they still pass through.
    """
    if not state:
        return state
    if is_pending_downgrade or is_pending_cancellation:
        return {}
    badge = state.get("badge", "")
    note = state.get("note", "")
    if (badge, note) in _STEADY_STATE_PAIRS:
        return {}
    return state


def _render_account_page(
    request,
    *,
    profile_error="",
    profile_form_first_name=None,
    profile_form_last_name=None,
    status=200,
):
    """Build the ``/account/`` context and render ``accounts/account.html``.

    Extracted so both ``account_view`` (GET) and the
    ``account_profile_post_view`` overflow path (400) can share the
    full account-page render. The profile-form context keys default to
    the saved ``user.first_name`` / ``user.last_name`` so a normal GET
    pre-fills the inputs; the overflow path passes the rejected typed
    values through so the user keeps their input.
    """
    user = request.user
    tier = user.tier
    pending_tier = user.pending_tier

    # Determine tier level for conditional display
    is_free = tier is None or tier.level == 0
    is_premium = tier is not None and tier.slug == "premium"
    is_basic = tier is not None and tier.slug == "basic"
    has_subscription = bool(user.subscription_id)

    # Determine display states
    # pending_tier.slug == "free" means cancellation is scheduled at period end.
    is_pending_downgrade = (
        pending_tier is not None
        and pending_tier.slug != "free"
    )
    is_pending_cancellation = (
        pending_tier is not None
        and pending_tier.slug == "free"
    )

    # Check for active tier override
    active_override = get_active_override(user)

    stripe_customer_portal_url = get_config("STRIPE_CUSTOMER_PORTAL_URL", "")
    has_stale_subscription = has_subscription and is_free

    free_tier = Tier.objects.filter(slug="free").first()
    latest_verification_email = None
    if not user.email_verified:
        latest_verification_email = (
            EmailLog.objects
            .filter(user=user, email_type="email_verification")
            .order_by("-sent_at")
            .first()
        )

    state_tier = tier or free_tier
    account_plan_state = (
        build_tier_state(state_tier, user, active_override)
        if state_tier else {}
    )
    # Issue #581: drop the frame for steady-state Free / Current-plan
    # users and for pending downgrade / pending cancellation cases (the
    # dedicated amber/red notice already carries that message).
    account_plan_state = _suppress_steady_state_plan_state(
        account_plan_state,
        is_pending_downgrade=is_pending_downgrade,
        is_pending_cancellation=is_pending_cancellation,
    )

    portal_available = bool(stripe_customer_portal_url)
    show_manage_subscription = has_subscription and portal_available
    show_upgrade_action = (
        is_free
        and not has_stale_subscription
        and not is_pending_cancellation
        and not is_pending_downgrade
        and active_override is None
    )

    context = {
        "tier": tier,
        "pending_tier": pending_tier,
        "is_free": is_free,
        "is_premium": is_premium,
        "is_basic": is_basic,
        "has_subscription": has_subscription,
        "is_pending_downgrade": is_pending_downgrade,
        "is_pending_cancellation": is_pending_cancellation,
        "billing_period_end": user.billing_period_end,
        "email_preferences": user.email_preferences,
        "newsletter_subscribed": not user.unsubscribed,
        "timezone_options": build_timezone_options(),
        "preferred_timezone_label": get_timezone_label(user.preferred_timezone),
        "active_override": active_override,
        "account_plan_state": account_plan_state,
        "show_manage_subscription": show_manage_subscription,
        "show_upgrade_action": show_upgrade_action,
        "stripe_customer_portal_url": stripe_customer_portal_url,
        "latest_verification_email": latest_verification_email,
        # Profile name form (consolidated onto /account/, issue #447). The
        # form posts to ``account_profile``; this view renders it inline.
        "profile_error": profile_error,
        "profile_form_first_name": (
            user.first_name
            if profile_form_first_name is None
            else profile_form_first_name
        ),
        "profile_form_last_name": (
            user.last_name
            if profile_form_last_name is None
            else profile_form_last_name
        ),
    }

    # Issue #581: the Sprint plan card was removed from /account/. The
    # plan stays reachable from the dashboard and ``/sprints/``; the
    # helper still backs the dashboard surface (``plans.dashboard``).

    return render(request, "accounts/account.html", context, status=status)


@login_required
def account_view(request):
    """Render the account page showing tier, billing info, and actions."""
    return _render_account_page(request)


@login_required
@require_POST
def email_preferences_view(request):
    """Update email preferences (newsletter subscribe/unsubscribe).

    Expects JSON body with:
        newsletter: bool - True to subscribe, False to unsubscribe
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    newsletter = data.get("newsletter")
    if newsletter is None:
        return JsonResponse({"error": "newsletter field is required"}, status=400)

    user = request.user
    user.unsubscribed = not newsletter
    user.email_preferences["newsletter"] = newsletter
    user.save(update_fields=["unsubscribed", "email_preferences"])

    return JsonResponse({
        "status": "ok",
        "newsletter": newsletter,
    })


@login_required
@require_POST
def timezone_preference_view(request):
    """Save or clear the user's preferred event display timezone."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    timezone_name = data.get("timezone")
    if timezone_name is None:
        return JsonResponse({"error": "timezone field is required"}, status=400)
    if not isinstance(timezone_name, str):
        return JsonResponse({"error": "timezone must be a string"}, status=400)

    timezone_name = timezone_name.strip()
    if timezone_name and not is_valid_timezone(timezone_name):
        return JsonResponse({"error": "Invalid timezone"}, status=400)

    user = request.user
    user.preferred_timezone = timezone_name
    user.save(update_fields=["preferred_timezone"])

    return JsonResponse({
        "status": "ok",
        "timezone": user.preferred_timezone,
        "label": get_timezone_label(user.preferred_timezone),
    })


@login_required
@require_POST
def theme_preference_view(request):
    """Save the user's theme preference (dark/light/empty).

    POST /api/account/theme-preference

    Expects JSON body with:
        theme: str - "dark", "light", or "" (follow system)

    Returns:
        200 with {"status": "ok"} on success.
        400 with {"error": "..."} on validation failure.
        401 for anonymous users (handled by @login_required).
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    theme = data.get("theme")
    if theme is None:
        return JsonResponse({"error": "theme field is required"}, status=400)

    if theme not in ("dark", "light", ""):
        return JsonResponse(
            {"error": "theme must be 'dark', 'light', or ''"}, status=400
        )

    user = request.user
    user.theme_preference = theme
    user.save(update_fields=["theme_preference"])

    return JsonResponse({"status": "ok"})


def account_profile_post_view(request):
    """Handle the consolidated ``/account/profile`` URL (issue #447).

    The Profile name form now lives inline on ``/account/``. The legacy
    ``/account/profile`` URL stays bound to the ``account_profile`` URL
    name so saved bookmarks, the ``{% url %}`` reverse lookup, and the
    form ``action`` keep working. Behaviour:

    - ``POST`` (authenticated): validate length, save on success and
      ``302`` redirect to ``/account/#profile`` with a success flash.
      On overflow, re-render ``/account/`` inline with the error in the
      Profile card (HTTP 400) and keep the rejected typed input in the
      fields. Anonymous POST is sent through allauth's login flow by
      ``@login_required`` (302 to ``/accounts/login/?next=...``) and no
      user row is mutated.
    - Any other method (``GET``, ``HEAD``): permanent ``301`` redirect
      to ``/account/``. The redirect is unconditional -- it fires for
      anonymous users too, since the form-rendering surface lives on
      ``/account/`` (which is itself ``@login_required``).
    """
    if request.method != "POST":
        return HttpResponsePermanentRedirect("/account/")

    if not request.user.is_authenticated:
        # Mirror ``@login_required`` for the POST path so an anonymous
        # caller is bounced to login WITHOUT writing to any user row.
        login_url = getattr(settings, "LOGIN_URL", "/accounts/login/")
        next_qs = urlencode({"next": "/account/profile"})
        return redirect(f"{login_url}?{next_qs}")

    user = request.user
    first_name = (request.POST.get("first_name") or "").strip()
    last_name = (request.POST.get("last_name") or "").strip()

    if (
        len(first_name) > _NAME_MAX_LENGTH
        or len(last_name) > _NAME_MAX_LENGTH
    ):
        error = (
            "Name is too long — keep it under "
            f"{_NAME_MAX_LENGTH} characters."
        )
        return _render_account_page(
            request,
            profile_error=error,
            profile_form_first_name=first_name,
            profile_form_last_name=last_name,
            status=400,
        )

    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=["first_name", "last_name"])

    messages.success(request, "Your profile has been updated.")
    # Redirect to /account/#profile so the page scrolls to the form.
    return redirect("/account/#profile")


@login_required
@require_POST
def resend_verification_view(request):
    """Resend the email-verification message to the current user."""
    user = request.user
    next_url = request.POST.get("next") or "account"
    if next_url != "account" and not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "account"

    if user.email_verified:
        messages.info(request, "Your email is already verified.")
        return redirect(next_url)

    cache_key = RESEND_VERIFICATION_CACHE_KEY_TEMPLATE.format(user_id=user.id)
    if not cache.add(cache_key, "1", RESEND_VERIFICATION_THROTTLE_SECONDS):
        messages.warning(
            request,
            "We just sent a verification email -- check your inbox. "
            "You can request another in a minute.",
        )
        return redirect(next_url)

    from accounts.views.auth import _send_verification_email

    try:
        email_log = _send_verification_email(user)
    except Exception:
        cache.delete(cache_key)
        logger.exception(
            "Verification email resend failed for user_id=%s", user.id
        )
        messages.error(
            request,
            "We couldn't send the verification email. Please try again.",
        )
        return redirect(next_url)

    if email_log is None:
        cache.delete(cache_key)
        messages.error(
            request,
            "We couldn't send the verification email. Please try again.",
        )
        return redirect(next_url)

    messages.success(request, "Verification email sent. Check your inbox.")
    return redirect(next_url)
