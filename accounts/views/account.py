"""Account page view and email preferences API."""

import json
import logging
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db.models.functions import Now
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.gating import is_newsletter_only_user
from accounts.models import MemberAPIKey, PrivacyRequestLog
from accounts.return_context import sanitize_verification_return_path
from accounts.services.email_change import (
    EmailChangeError,
    active_email_change_request_for_user,
    confirm_email_change,
    request_email_change,
)
from accounts.services.privacy import (
    build_user_data_export,
    delete_account_for_privacy,
    log_blocked_privacy_delete,
    request_context_from_request,
    write_privacy_export_log,
)

# Issue #448/#449: per-user resend throttle for every verification CTA.
RESEND_VERIFICATION_THROTTLE_SECONDS = 60

# Matches AbstractUser.first_name / last_name max_length so an over-long
# value is rejected before Django's model-level validator complains. Keeping
# this in sync with AbstractUser is intentional -- the constraint is
# implicit in the column type, so we keep one named constant in the view.
_NAME_MAX_LENGTH = 150

logger = logging.getLogger(__name__)


def _claim_verification_resend(user_id):
    """Atomically claim one cross-worker resend window.

    Uses a single conditional UPDATE and the database clock, so concurrent
    Gunicorn workers cannot both win and host clock skew cannot shorten the
    60-second window. Returns the opaque claim token, or ``None`` when the
    user is verified/missing or the existing claim is still fresh.
    """
    from accounts.models import User

    token = uuid.uuid4()
    cutoff = Now() - timedelta(seconds=RESEND_VERIFICATION_THROTTLE_SECONDS)
    claimed = (
        User.objects
        .filter(pk=user_id, email_verified=False)
        .filter(
            Q(verification_resend_claimed_at__isnull=True)
            | Q(verification_resend_claimed_at__lte=cutoff)
        )
        .update(
            verification_resend_claimed_at=Now(),
            verification_resend_claim_token=token,
        )
    )
    return token if claimed == 1 else None


def _release_verification_resend(user_id, token):
    """Release only ``token``'s failed-send claim, never a newer claim."""
    from accounts.models import User

    User.objects.filter(
        pk=user_id,
        verification_resend_claim_token=token,
    ).update(
        verification_resend_claimed_at=None,
        verification_resend_claim_token=None,
    )

from accounts.services.timezones import (
    build_timezone_options,
    get_timezone_label,
    is_valid_timezone,
)
from community.services.slack_links import build_slack_profile_url
from content.access import LEVEL_MAIN, get_active_override, get_user_level
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

_ACCOUNT_TIER_BENEFIT_FALLBACKS = {
    "free": [
        "Newsletter updates",
        "Open articles, recordings, projects, tutorials, and events",
        "Account dashboard and email preferences",
    ],
    "basic": [
        "Everything in Free",
        "Exclusive articles, tutorials with code, AI tool breakdowns, and research notes",
        "Basic gated curated links and downloads",
    ],
    "main": [
        "Everything in Basic",
        "Slack community, group coding sessions, and guided project work",
        "Accountability through sprints, topic voting, and community activities",
    ],
    "premium": [
        "Everything in Main",
        "All mini-courses and course-topic voting",
        "Resume, LinkedIn, and GitHub teardowns",
    ],
}

_ACCOUNT_NEXT_TIER_UPSELLS = {
    "basic": {
        "target_slug": "main",
        "target_name": "Main",
        "title": "Main adds the community layer",
        "description": (
            "Join the private community, live and group work, accountability "
            "sprints, and topic voting."
        ),
        "cta_label": "Compare Main",
        "url": "/pricing",
    },
    "main": {
        "target_slug": "premium",
        "target_name": "Premium",
        "title": "Premium adds deeper career support",
        "description": (
            "Unlock mini-courses, course-topic voting, and resume, LinkedIn, "
            "and GitHub teardowns."
        ),
        "cta_label": "Compare Premium",
        "url": "/pricing",
    },
}


def _account_tier_benefits(tier):
    """Return a concise account-card benefit summary for ``tier``."""
    slug = tier.slug if tier else "free"
    synced_features = []
    if tier and isinstance(tier.features, list):
        synced_features = [
            str(feature).strip()
            for feature in tier.features
            if str(feature).strip()
        ]
    return synced_features[:4] or _ACCOUNT_TIER_BENEFIT_FALLBACKS.get(
        slug,
        _ACCOUNT_TIER_BENEFIT_FALLBACKS["free"],
    )


def _account_next_tier_upsell(effective_tier):
    if effective_tier is None:
        return None
    return _ACCOUNT_NEXT_TIER_UPSELLS.get(effective_tier.slug)


def _suppress_steady_state_plan_state(state, *, is_pending_cancellation):
    """Return ``{}`` for steady-state plan-state frames, else ``state``.

    The frame is suppressed when:

    - There is a pending cancellation -- the dedicated red notice below
      already shows the same message, so echoing it in the plan-state
      frame is duplicate noise.
    - The (badge, note) pair is one of the steady-state tuples in
      :data:`_STEADY_STATE_PAIRS`. The tier name in the Membership
      section already communicates this.

    Override messages (badge ``"Current plan"`` with a non-empty note
    such as ``"Base subscription. Temporary X access is active."``) and
    stale-subscription warnings fall outside the steady-state set, so
    they still pass through.
    """
    if not state:
        return state
    if is_pending_cancellation:
        return {}
    badge = state.get("badge", "")
    note = state.get("note", "")
    if (badge, note) in _STEADY_STATE_PAIRS:
        return {}
    return state


def _render_account_page(
    request,
    *,
    created_member_api_key="",
    member_api_key_name_error="",
    member_api_key_form_name="",
    profile_error="",
    profile_form_first_name=None,
    profile_form_last_name=None,
    privacy_delete_error="",
    privacy_blocker_reason="",
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
    is_pending_cancellation = (
        pending_tier is not None
        and pending_tier.slug == "free"
    )

    # Check for active tier override
    active_override = get_active_override(user)

    # Effective-tier resolution for the membership headline (issue #965).
    # The "Current Plan" headline reads the EFFECTIVE tier (override applied)
    # when an active override RAISES the tier above base; otherwise it shows
    # the base tier exactly as before. We resolve this here (not in the
    # template) so the headline never re-derives the ``max()`` rule inline.
    # The base level mirrors ``content.access.get_user_level`` — an override
    # only ever raises the reported tier, never lowers it.
    base_level = tier.level if tier else 0
    override_raises_tier = bool(
        active_override is not None
        and active_override.override_tier.level > base_level
    )
    if override_raises_tier:
        effective_tier = active_override.override_tier
        # Provenance line, e.g. "Main plan — tier override from Free until
        # 06/05/2026". Base tier name falls back to "Free" for a NULL base.
        # Zero-padded day to match the surrounding template's Django
        # ``date:"d/m/Y"`` format (issue #965) — no third date format.
        base_tier_name = tier.name if tier else "Free"
        override_provenance = (
            f"{effective_tier.name} plan — tier override from "
            f"{base_tier_name} until "
            f"{active_override.expires_at.strftime('%d/%m/%Y')}"
        )
    else:
        effective_tier = tier
        override_provenance = ""

    stripe_customer_portal_url = get_config("STRIPE_CUSTOMER_PORTAL_URL", "")
    has_stale_subscription = has_subscription and is_free

    free_tier = Tier.objects.filter(slug="free").first()

    state_tier = tier or free_tier
    account_plan_state = (
        build_tier_state(state_tier, user, active_override)
        if state_tier else {}
    )
    # Issue #581: drop the frame for steady-state Free / Current-plan
    # users and for the pending cancellation case (the dedicated red
    # notice already carries that message).
    account_plan_state = _suppress_steady_state_plan_state(
        account_plan_state,
        is_pending_cancellation=is_pending_cancellation,
    )

    portal_available = bool(stripe_customer_portal_url)
    show_manage_subscription = has_subscription and portal_available
    show_upgrade_action = (
        is_free
        and not has_stale_subscription
        and not is_pending_cancellation
        and active_override is None
    )
    membership_benefits = _account_tier_benefits(effective_tier)
    next_tier_upsell = _account_next_tier_upsell(effective_tier)
    is_effective_premium = (
        effective_tier is not None and effective_tier.slug == "premium"
    )
    show_paid_plan_pricing_action = bool(
        effective_tier is not None
        and effective_tier.slug != "free"
        and not has_subscription
        and not is_effective_premium
    )
    paid_without_subscription_note = ""
    if show_paid_plan_pricing_action:
        if active_override is not None:
            paid_without_subscription_note = (
                "No Stripe subscription is connected to this account. Use "
                "pricing to choose a paid plan or keep access after temporary "
                "access ends."
            )
        else:
            paid_without_subscription_note = (
                "No Stripe subscription is connected to this account. Use "
                "pricing to choose or upgrade a paid plan."
            )

    # Slack community card (issue #700). Issue #971: uses the effective
    # (override-aware) level via get_user_level — an active TierOverride
    # grants Slack/community access. Same rule as the dashboard and the
    # join redirect (community/views.py).
    # Issue #953: the CTA links to the gated /community/slack redirect, not
    # the raw invite URL — but the card is still only shown when an invite
    # URL is actually configured (nothing to redirect to otherwise).
    slack_invite_configured = bool(get_config("SLACK_INVITE_URL", ""))
    slack_join_url = reverse("community_slack_join")
    has_qualifying_slack_tier = get_user_level(user) >= LEVEL_MAIN
    show_slack_join = bool(
        slack_invite_configured
        and has_qualifying_slack_tier
        and not user.slack_member
    )
    slack_connected = bool(has_qualifying_slack_tier and user.slack_member)
    slack_user_id = user.slack_user_id or ""
    slack_profile_url = build_slack_profile_url(
        slack_user_id, get_config("SLACK_TEAM_ID", ""),
    )

    context = {
        "tier": tier,
        "pending_tier": pending_tier,
        "is_free": is_free,
        "is_premium": is_premium,
        "is_basic": is_basic,
        "has_subscription": has_subscription,
        "is_pending_cancellation": is_pending_cancellation,
        "billing_period_end": user.billing_period_end,
        "email_preferences": user.email_preferences,
        "newsletter_subscribed": not user.unsubscribed,
        # Issue #655: per-content-type opt-out. Default is ON (opted in)
        # when the key is missing from the JSONField -- new accounts and
        # any account that has never touched the toggle.
        "workshop_emails_enabled": user.email_preferences.get(
            "workshop_emails", True
        ),
        "sprint_cadence_emails_enabled": user.email_preferences.get(
            "sprint_cadence_emails", True
        ),
        "timezone_options": build_timezone_options(),
        "preferred_timezone_label": get_timezone_label(user.preferred_timezone),
        "active_override": active_override,
        # Issue #965: effective-tier headline + override provenance line.
        "effective_tier": effective_tier,
        "override_provenance": override_provenance,
        "account_plan_state": account_plan_state,
        "membership_benefits": membership_benefits,
        "next_tier_upsell": next_tier_upsell,
        "is_effective_premium": is_effective_premium,
        "show_paid_plan_pricing_action": show_paid_plan_pricing_action,
        "paid_without_subscription_note": paid_without_subscription_note,
        "show_manage_subscription": show_manage_subscription,
        "show_upgrade_action": show_upgrade_action,
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
        # Slack community card (issue #700). Consumed by the shared
        # ``includes/_slack_account_card.html`` partial.
        "show_slack_join": show_slack_join,
        "slack_connected": slack_connected,
        "slack_join_url": slack_join_url,
        "slack_user_id": slack_user_id,
        "slack_profile_url": slack_profile_url,
        "member_api_keys": MemberAPIKey.objects.filter(user=user),
        "created_member_api_key": created_member_api_key,
        "member_api_key_name_error": member_api_key_name_error,
        "member_api_key_form_name": member_api_key_form_name,
        "member_api_docs_url": "/member-api/docs",
        # Issue #1127: the "API usage guide" link points at the on-site
        # member API docs page (login-gated, and the account viewer is
        # already logged in) rather than the raw GitHub blob.
        "member_api_usage_guide_url": "/member-api/docs",
        "member_api_skill_url": (
            "https://github.com/AI-Shipping-Labs/website/tree/main/"
            "skills/ai-shipping-labs-plans-api"
        ),
        "pending_email_change": active_email_change_request_for_user(user),
        "email_change_requires_password": user.has_usable_password(),
        "privacy_requires_password": user.has_usable_password(),
        "privacy_delete_error": privacy_delete_error,
        "privacy_blocker_reason": privacy_blocker_reason,
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
@require_GET
def data_export_view(request):
    """Return the signed-in member's portable privacy export as JSON."""
    payload = build_user_data_export(request.user)
    write_privacy_export_log(
        request.user,
        request_context_from_request(request),
    )
    today = timezone.localdate().isoformat()
    response = HttpResponse(
        json.dumps(payload, indent=2, sort_keys=True),
        content_type="application/json",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="ai-shipping-labs-data-{today}.json"'
    )
    return response


def _delete_request_data(request):
    content_type = request.META.get("CONTENT_TYPE", "")
    if content_type.startswith("application/json"):
        try:
            data = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST


@login_required
@require_POST
def delete_account_view(request):
    """Delete the signed-in member's local platform account."""
    data = _delete_request_data(request)
    request_context = request_context_from_request(request)
    user = request.user
    confirm_email = data.get("confirm_email") or ""

    if confirm_email != user.email:
        log_blocked_privacy_delete(
            user,
            PrivacyRequestLog.BLOCKER_BAD_CONFIRMATION,
            request_context,
        )
        return _render_account_page(
            request,
            privacy_delete_error=(
                "We could not confirm this request. Check the email address "
                "and password, then try again."
            ),
            privacy_blocker_reason=PrivacyRequestLog.BLOCKER_BAD_CONFIRMATION,
            status=400,
        )

    if user.has_usable_password():
        current_password = data.get("current_password") or ""
        if not user.check_password(current_password):
            log_blocked_privacy_delete(
                user,
                PrivacyRequestLog.BLOCKER_BAD_PASSWORD,
                request_context,
            )
            return _render_account_page(
                request,
                privacy_delete_error=(
                    "We could not confirm this request. Check the email "
                    "address and password, then try again."
                ),
                privacy_blocker_reason=PrivacyRequestLog.BLOCKER_BAD_PASSWORD,
                status=400,
            )

    result = delete_account_for_privacy(user, request_context)
    if not result.success:
        if result.blocker_reason == PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION:
            error = (
                "Your account still has an active subscription. Manage or "
                "cancel the subscription first, then return here after the "
                "billing update is complete."
            )
        elif result.blocker_reason == PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT:
            error = (
                "Staff and admin accounts cannot be deleted from this "
                "self-service flow. Contact another operator for support."
            )
        else:
            error = "We could not delete this account from the self-service flow."
        return _render_account_page(
            request,
            privacy_delete_error=error,
            privacy_blocker_reason=result.blocker_reason,
            status=403,
        )

    logout(request)
    return redirect("account_deleted")


def account_deleted_view(request):
    """Public confirmation page after a member deletes their account."""
    return render(request, "accounts/account_deleted.html")


@login_required
@require_POST
def change_email_request_view(request):
    """Start a member-owned login email change request."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        request_obj, _token = request_email_change(
            request.user,
            data.get("new_email", ""),
            current_password=data.get("current_password", ""),
        )
    except EmailChangeError as exc:
        return JsonResponse(
            {"error": exc.message, "code": exc.code},
            status=exc.status,
        )
    except Exception:
        logger.exception(
            "Email change request failed for user_id=%s",
            request.user.pk,
        )
        return JsonResponse(
            {"error": "We couldn't send that verification link. Please try again."},
            status=500,
        )

    return JsonResponse({
        "status": "ok",
        "message": f"Verification link sent to {request_obj.new_email}.",
        "pending_new_email": request_obj.new_email,
        "current_email": request_obj.old_email,
    })


def change_email_confirm_view(request):
    """Public confirmation link for pending login email changes."""
    result = confirm_email_change(request.GET.get("token", ""))
    if result.success:
        cta_url = "/account/" if request.user.is_authenticated else "/accounts/login/"
        cta_label = (
            "Continue to Account"
            if request.user.is_authenticated
            else "Sign In"
        )
        status_code = 200
    else:
        cta_url = "/account/" if request.user.is_authenticated else "/accounts/login/"
        cta_label = (
            "Go to Account" if request.user.is_authenticated else "Sign In"
        )
        status_code = 400

    return render(
        request,
        "accounts/change_email_result.html",
        {
            "success": result.success,
            "message": result.message,
            "status": result.status,
            "cta_url": cta_url,
            "cta_label": cta_label,
        },
        status=status_code,
    )


@login_required
@require_POST
def member_api_key_create_view(request):
    """Create a member-owned API key and show the plaintext value once."""
    if is_newsletter_only_user(request.user):
        return HttpResponseForbidden("API keys are not available for this account.")

    name = (request.POST.get("name") or "").strip()
    if not name:
        return _render_account_page(
            request,
            member_api_key_name_error="Name is required.",
            status=400,
        )
    if len(name) > 100:
        return _render_account_page(
            request,
            member_api_key_name_error="Name must be 100 characters or fewer.",
            member_api_key_form_name=name,
            status=400,
        )

    _, plaintext_key = MemberAPIKey.create_for_user(
        user=request.user,
        name=name,
    )
    messages.success(request, "Member API key created.")
    return _render_account_page(
        request,
        created_member_api_key=plaintext_key,
        status=201,
    )


@login_required
@require_POST
def member_api_key_revoke_view(request, key_id):
    """Soft-revoke one of the signed-in member's own API keys."""
    if is_newsletter_only_user(request.user):
        return HttpResponseForbidden("API keys are not available for this account.")

    member_key = get_object_or_404(
        MemberAPIKey,
        pk=key_id,
        user=request.user,
    )
    member_key.revoke()
    messages.success(request, "Member API key revoked.")
    return redirect("/account/#api-keys")


@login_required
@require_POST
def member_api_key_delete_view(request, key_id):
    """Hard-delete one of the signed-in member's own revoked API keys.

    Issue #1127: deletion is a deliberate two-step (revoke -> delete)
    safety pattern, so an active key cannot be deleted directly. The row
    is scoped to the signed-in member (a cross-member id 404s) and only
    already-revoked keys are removed; posting delete for an active key
    leaves the row intact with an error flash.
    """
    if is_newsletter_only_user(request.user):
        return HttpResponseForbidden("API keys are not available for this account.")

    member_key = get_object_or_404(
        MemberAPIKey,
        pk=key_id,
        user=request.user,
    )
    if member_key.revoked_at is None:
        messages.error(
            request,
            "Revoke this API key before deleting it.",
        )
        return redirect("/account/#api-keys")

    member_key.delete()
    messages.success(request, "Member API key deleted.")
    return redirect("/account/#api-keys")


@login_required
@require_POST
def email_preferences_view(request):
    """Update email preferences.

    Issue #655: accepts ``newsletter`` and per-channel email booleans in a
    single JSON body. At least one known boolean key must be present,
    otherwise returns 400.

    The response echoes back only the fields that were updated.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    newsletter = data.get("newsletter")
    workshop_emails = data.get("workshop_emails")
    sprint_cadence_emails = data.get("sprint_cadence_emails")

    newsletter_provided = isinstance(newsletter, bool)
    workshop_emails_provided = isinstance(workshop_emails, bool)
    sprint_cadence_emails_provided = isinstance(sprint_cadence_emails, bool)

    if (
        not newsletter_provided
        and not workshop_emails_provided
        and not sprint_cadence_emails_provided
    ):
        return JsonResponse(
            {
                "error": (
                    "newsletter, workshop_emails, or sprint_cadence_emails "
                    "boolean field is required"
                ),
            },
            status=400,
        )

    user = request.user
    update_fields = ["email_preferences"]
    response = {"status": "ok"}

    if newsletter_provided:
        user.unsubscribed = not newsletter
        user.email_preferences["newsletter"] = newsletter
        update_fields.append("unsubscribed")
        response["newsletter"] = newsletter

    if workshop_emails_provided:
        user.email_preferences["workshop_emails"] = workshop_emails
        response["workshop_emails"] = workshop_emails

    if sprint_cadence_emails_provided:
        user.email_preferences["sprint_cadence_emails"] = sprint_cadence_emails
        response["sprint_cadence_emails"] = sprint_cadence_emails

    user.save(update_fields=update_fields)

    return JsonResponse(response)


# Allow-list of dismissable dashboard card keys (issue #1129). A dismiss
# POST is accepted only for these stable keys; anything else is a 400. The
# readers (content/views/home.py) branch on the same keys.
DISMISSABLE_DASHBOARD_CARDS = frozenset({"onboarding_prompt", "slack_join"})
PLAN_CARRY_OVER_DISMISS_PREFIX = "plan_carry_over_prompt:"


def _is_owned_plan_carry_over_dismissal(card, user):
    """Return True when ``card`` names one of ``user``'s sprint plans."""
    if not isinstance(card, str):
        return False
    if not card.startswith(PLAN_CARRY_OVER_DISMISS_PREFIX):
        return False

    raw_plan_id = card.removeprefix(PLAN_CARRY_OVER_DISMISS_PREFIX)
    if not raw_plan_id.isdigit():
        return False
    if raw_plan_id != str(int(raw_plan_id)):
        return False

    from plans.models import Plan

    return Plan.objects.filter(pk=int(raw_plan_id), member=user).exists()


def _is_valid_dashboard_dismissal(card, user):
    if not isinstance(card, str):
        return False
    if card in DISMISSABLE_DASHBOARD_CARDS:
        return True
    return _is_owned_plan_carry_over_dismissal(card, user)


@login_required
@require_POST
def dismiss_dashboard_card(request):
    """Persist a per-user dashboard card dismissal (issue #1129).

    POST /account/api/dismiss-card with JSON body ``{"card": "<key>"}``.

    Member self-service preference (each member dismisses their own
    cards), so it is stored on the user row rather than through the
    operator-facing ``IntegrationSetting`` framework. Modeled on
    ``email_preferences_view``:

    - Validates ``card`` against :data:`DISMISSABLE_DASHBOARD_CARDS`
      or the owner-scoped ``plan_carry_over_prompt:<plan_id>`` key;
      an unknown, missing, malformed, or non-owned key returns ``400``
      with an ``error`` and does not modify ``dashboard_dismissals``.
    - Idempotent: adds the key only if absent, so dismissing an
      already-dismissed card is a no-op that still returns ``200``.
    - Anonymous callers are redirected to login by ``@login_required``.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    card = data.get("card") if isinstance(data, dict) else None
    if not _is_valid_dashboard_dismissal(card, request.user):
        return JsonResponse({"error": "Unknown card"}, status=400)

    user = request.user
    if card not in user.dashboard_dismissals:
        user.dashboard_dismissals.append(card)
        user.save(update_fields=["dashboard_dismissals"])

    return JsonResponse({"status": "ok", "card": card})


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

    # Issue #961: the passive browser-tz backfill (fired automatically on
    # authenticated page load) must never overwrite a non-empty
    # ``preferred_timezone``. Account-settings choices and prior backfills
    # are canonical, mirroring the registration-form rule in
    # ``events/views/api.py``. The passive client marks the call with
    # ``passive: true``; deliberate manual saves/clears from Account
    # settings omit the flag and keep overwriting (including clearing).
    passive = bool(data.get("passive"))
    if passive and user.preferred_timezone:
        return JsonResponse({
            "status": "ok",
            "timezone": user.preferred_timezone,
            "label": get_timezone_label(user.preferred_timezone),
        })

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
    return_path = sanitize_verification_return_path(
        request.POST.get("next") or "",
        request=request,
        default="",
    )
    next_url = return_path or "account"

    if user.email_verified:
        messages.info(request, "Your email is already verified.")
        return redirect(next_url)

    claim_token = _claim_verification_resend(user.id)
    if claim_token is None:
        user.refresh_from_db(fields=["email_verified"])
        if user.email_verified:
            messages.info(request, "Your email is already verified.")
            return redirect(next_url)
        messages.warning(
            request,
            "We just sent a verification email -- check your inbox. "
            "You can request another in a minute.",
        )
        return redirect(next_url)

    from accounts.views.auth import _send_verification_email

    try:
        if return_path:
            email_log = _send_verification_email(user, return_path=return_path)
        else:
            email_log = _send_verification_email(user)
    except Exception:
        _release_verification_resend(user.id, claim_token)
        logger.exception(
            "Verification email resend failed for user_id=%s", user.id
        )
        messages.error(
            request,
            "We couldn't send the verification email. Please try again.",
        )
        return redirect(next_url)

    if email_log is None:
        _release_verification_resend(user.id, claim_token)
        messages.error(
            request,
            "We couldn't send the verification email. Please try again.",
        )
        return redirect(next_url)

    messages.success(request, "Verification email sent. Check your inbox.")
    return redirect(next_url)
