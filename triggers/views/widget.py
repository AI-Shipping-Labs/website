"""Per-user hydration + claim endpoints for the event widget (issue #1070).

The markdown shortcode expands to a user-agnostic
``<div data-event-widget="slug">`` placeholder. ``static/js/event-widget.js``
GETs ``/widgets/<slug>/state`` to learn which state to render, then POSTs
``/widgets/<slug>/claim`` to claim. Django is ALWAYS the trust boundary:
``min_level`` is re-checked server-side on claim, the dedup is enforced at
the DB layer, and the widget never talks to the Lambda directly.

States returned by ``/state``:

- ``unavailable``     — no active widget for that slug.
- ``paused``          — TRIGGERS_ENABLED is off.
- ``signin_required`` — anonymous visitor.
- ``under_level``     — authenticated but below ``min_level``.
- ``claimed``         — the user already has an emission for this event.
- ``claimable``       — eligible and not yet claimed.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from content.access import LEVEL_BASIC, LEVEL_REGISTERED, get_user_level
from integrations.config import is_enabled
from triggers.dispatch import emit_event
from triggers.models import EventEmission, EventWidget


def _active_widget(slug):
    return EventWidget.objects.filter(slug=slug, is_active=True).first()


def is_eligible(user, widget):
    """Server-side eligibility check honouring the LEVEL_REGISTERED sentinel.

    ``LEVEL_REGISTERED`` (5) is a content-side sentinel, NOT a real tier
    level, so a free verified member never has ``get_user_level == 5``.
    Mirror the ``content.access.can_access`` rule: for a registered-wall
    widget, any signed-in member whose email is verified (or who is
    Basic+) is eligible. For a paid-tier widget, fall back to the numeric
    level comparison.
    """
    if user is None or not user.is_authenticated:
        return False
    if widget.min_level == LEVEL_REGISTERED:
        if get_user_level(user) >= LEVEL_BASIC:
            return True
        return bool(user.email_verified)
    return get_user_level(user) >= widget.min_level


def _has_claimed(user, widget):
    if user is None or not user.is_authenticated:
        return False
    return EventEmission.objects.filter(
        user=user, event_name=widget.event_name,
    ).exists()


def _build_state(request, widget):
    """Return ``(state, payload)`` for the given widget and request user."""
    user = request.user

    if not is_enabled("TRIGGERS_ENABLED"):
        return "paused", {}

    if user is None or not user.is_authenticated:
        return "signin_required", {
            "signin_cta": widget.signin_cta,
            "login_url": f"/accounts/login/?next={request.GET.get('next', '/')}",
        }

    if _has_claimed(user, widget):
        return "claimed", {"claimed_label": widget.claimed_label}

    if not is_eligible(user, widget):
        return "under_level", {"pricing_url": "/pricing"}

    return "claimable", {
        "claim_label": widget.claim_label,
        "claim_body": widget.claim_body,
    }


def _serialize_state(widget, state, payload):
    return {
        "slug": widget.slug,
        "state": state,
        **payload,
    }


@require_GET
def widget_state(request, slug):
    """Return the per-user widget state as JSON (hydration endpoint)."""
    widget = _active_widget(slug)
    if widget is None:
        return JsonResponse({"slug": slug, "state": "unavailable"}, status=200)

    state, payload = _build_state(request, widget)
    return JsonResponse(_serialize_state(widget, state, payload), status=200)


@require_POST
def widget_claim(request, slug):
    """Claim the widget for the authenticated user (server-enforced gate).

    CSRF is enforced (no ``csrf_exempt``); anonymous → 401; under-level →
    403; flag off → ``paused`` state; success → records an emission and
    dispatches matching subscriptions. A duplicate claim is a no-op that
    returns the ``claimed`` state (not an error).
    """
    widget = _active_widget(slug)
    if widget is None:
        return JsonResponse({"slug": slug, "state": "unavailable"}, status=404)

    if not is_enabled("TRIGGERS_ENABLED"):
        return JsonResponse({"slug": slug, "state": "paused"}, status=200)

    user = request.user
    if user is None or not user.is_authenticated:
        return JsonResponse(
            {
                "slug": slug,
                "state": "signin_required",
                "error": "Sign in to claim.",
                "login_url": "/accounts/login/",
            },
            status=401,
        )

    if not is_eligible(user, widget):
        return JsonResponse(
            {
                "slug": slug,
                "state": "under_level",
                "error": "Your membership level is not eligible for this claim.",
                "pricing_url": "/pricing",
            },
            status=403,
        )

    emit_event(
        widget.event_name,
        user,
        {"name": widget.event_name},
        min_level=widget.min_level,
    )

    return JsonResponse(
        {
            "slug": slug,
            "state": "claimed",
            "claimed_label": widget.claimed_label,
        },
        status=200,
    )
