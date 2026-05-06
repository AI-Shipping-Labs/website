"""Account page view and email preferences API."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

# Issue #448: per-user resend throttle for the account verification banner.
RESEND_VERIFICATION_THROTTLE_SECONDS = 60
RESEND_VERIFICATION_CACHE_KEY_TEMPLATE = "verify-email-resend:{user_id}"

# Matches AbstractUser.first_name / last_name max_length so an over-long
# value is rejected before Django's model-level validator complains. Keeping
# this in sync with AbstractUser is intentional -- the constraint is
# implicit in the column type, so we keep one named constant in the view.
_NAME_MAX_LENGTH = 150

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from content.access import get_active_override
from integrations.config import get_config, is_enabled
from payments.models import Tier
from payments.tier_state import build_tier_state
from plans.dashboard import build_sprint_plan_card_context


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

    stripe_checkout_enabled = is_enabled("STRIPE_CHECKOUT_ENABLED")
    stripe_customer_portal_url = get_config("STRIPE_CUSTOMER_PORTAL_URL", "")
    has_stale_subscription = has_subscription and is_free

    # Get available tiers for upgrade/downgrade options
    all_tiers = list(Tier.objects.exclude(slug="free").order_by("level"))
    free_tier = Tier.objects.filter(slug="free").first()

    # Determine upgrade tiers from the base subscription tier. Temporary
    # overrides grant access, but they do not change subscription actions.
    current_level = tier.level if tier else 0
    upgrade_tiers = [t for t in all_tiers if t.level > current_level]
    downgrade_tiers = [t for t in all_tiers if 0 < t.level < current_level]

    # Get current tier's feature list for the cancel confirmation modal
    tier_features = tier.features if tier and tier.features else []

    state_tier = tier or free_tier
    account_plan_state = (
        build_tier_state(state_tier, user, active_override)
        if state_tier else {}
    )

    portal_available = bool(stripe_customer_portal_url)
    show_manage_subscription = has_subscription and portal_available and (
        not stripe_checkout_enabled
        or is_pending_cancellation
        or is_pending_downgrade
        or has_stale_subscription
        or active_override is not None
    )
    show_upgrade_action = (
        not has_stale_subscription
        and not is_pending_cancellation
        and not is_pending_downgrade
        and active_override is None
        and (
            is_free
            or (
                stripe_checkout_enabled
                and bool(upgrade_tiers)
            )
        )
    )
    show_downgrade_action = (
        stripe_checkout_enabled
        and not is_basic
        and not is_free
        and not is_pending_downgrade
        and not is_pending_cancellation
        and bool(downgrade_tiers)
    )
    show_cancel_action = (
        stripe_checkout_enabled
        and has_subscription
        and not has_stale_subscription
        and not is_pending_cancellation
        and not is_free
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
        "upgrade_tiers": upgrade_tiers,
        "downgrade_tiers": downgrade_tiers,
        "email_preferences": user.email_preferences,
        "newsletter_subscribed": not user.unsubscribed,
        "timezone_options": build_timezone_options(),
        "preferred_timezone_label": get_timezone_label(user.preferred_timezone),
        "tier_features": tier_features,
        "active_override": active_override,
        "account_plan_state": account_plan_state,
        "show_manage_subscription": show_manage_subscription,
        "show_upgrade_action": show_upgrade_action,
        "show_downgrade_action": show_downgrade_action,
        "show_cancel_action": show_cancel_action,
        "stripe_checkout_enabled": stripe_checkout_enabled,
        "stripe_customer_portal_url": stripe_customer_portal_url,
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

    # Sprint plan card (issue #442). The helper handles the empty case
    # (no plan -> ``plan`` is ``None`` and the template omits the card).
    context.update(build_sprint_plan_card_context(user))

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
def cancel_subscription_view(request):
    """Cancel subscription at period end and set pending_tier to free.

    This wraps the payments service cancel_subscription and also sets
    the pending_tier to free so the account page can show the cancellation
    status without querying Stripe.
    """
    from payments.services import cancel_subscription

    user = request.user

    if not user.subscription_id:
        return JsonResponse({"error": "No active subscription"}, status=400)

    try:
        updated_subscription = cancel_subscription(user)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:
        return JsonResponse(
            {"error": "Failed to cancel subscription"}, status=500
        )

    # Set pending_tier to free to indicate cancellation at period end
    free_tier = Tier.objects.filter(slug="free").first()
    update_fields = []
    if free_tier:
        user.pending_tier = free_tier
        update_fields.append("pending_tier")

    # Update billing_period_end from the Stripe subscription response
    current_period_end = getattr(updated_subscription, "current_period_end", None)
    if current_period_end:
        user.billing_period_end = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        )
        update_fields.append("billing_period_end")

    if update_fields:
        user.save(update_fields=update_fields)

    return JsonResponse({"status": "ok"})


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


# Reserved Token name for the member-facing plan editor (issue #444).
# Parallel to ``studio-plan-editor`` for staff. Mints at most one row
# per (member, name) so reloading the editor never spams tokens.
MEMBER_EDITOR_TOKEN_NAME = "member-plan-editor"


@login_required
def member_plan_edit(request, plan_id):
    """Member-facing plan editor at ``/account/plan/<id>/edit/``.

    Issue #444. Renders the SAME drag-drop authoring UI staff use in
    Studio. Owner-only: a logged-in user who is NOT the plan's
    ``member`` gets a 404 (not 403) to avoid leaking that the plan
    exists at all (visibility-leak prevention per #440).

    The editor body comes from ``templates/studio/plans/_editor_body.html``
    -- the SAME partial the Studio editor at ``/studio/plans/<id>/edit/``
    uses. The shell here extends the public ``base.html`` (member chrome)
    rather than the Studio chrome. Writes flow through the existing
    plans API; the API queryset gate
    (``api/views/_permissions.py::visible_plans_for``) restricts a
    non-staff bearer to ``member=user`` so writes to other members'
    plans are impossible at the queryset boundary.
    """
    # Local imports keep the heavy plans app off the import path of
    # the account index page.
    from plans.models import Plan
    from studio.services.plan_editor import build_plan_editor_context

    # Owner-only ``Plan.objects.get`` -- explicit ``member=request.user``
    # so an attacker cannot enumerate plan ids. Use ``Http404`` (not
    # 403) so existence is not leaked.
    plan = (
        Plan.objects
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "resources",
            "deliverables",
            "next_steps",
            "interview_notes",
        )
        .filter(pk=plan_id, member=request.user)
        .first()
    )
    if plan is None:
        raise Http404("Plan not found")

    context = build_plan_editor_context(
        plan,
        viewer=request.user,
        token_name=MEMBER_EDITOR_TOKEN_NAME,
    )
    return render(request, "account/plan_edit.html", context)


@login_required
@require_POST
def resend_verification_view(request):
    """Resend the email-verification message to the current user."""
    user = request.user

    if user.email_verified:
        messages.info(request, "Your email is already verified.")
        return redirect("account")

    cache_key = RESEND_VERIFICATION_CACHE_KEY_TEMPLATE.format(user_id=user.id)
    if not cache.add(cache_key, "1", RESEND_VERIFICATION_THROTTLE_SECONDS):
        messages.warning(
            request,
            "We just sent a verification email -- check your inbox. "
            "You can request another in a minute.",
        )
        return redirect("account")

    from accounts.views.auth import _send_verification_email

    _send_verification_email(user)
    messages.success(request, "Verification email sent. Check your inbox.")
    return redirect("account")
