"""Reconcile Stripe status contact tags from webhook truth (issue #969).

The ``stripe:active`` / ``stripe:churned`` / ``stripe:plan-<slug>`` contact
tags used to be written ONLY once, at the initial Stripe customer import
(``import_stripe._row_for_customer``), and were never re-synced when the
subscription state later changed via webhooks. They drifted out of sync with
Stripe truth — a churned user who re-subscribed kept a stale ``stripe:churned``
tag (this happened live to ``kkrotov.kir@gmail.com`` and had to be fixed by
hand).

``reconcile_stripe_status_tags`` recomputes the user's ``stripe:*`` status tags
from the post-update truth (the same active-vs-churned / tier decision the
webhook handler just made) and writes the tags to match. It is called from the
three handlers that change subscription state — ``handle_checkout_completed``,
``handle_subscription_updated``, and ``handle_subscription_deleted``.

``invoice.payment_failed`` deliberately does NOT call this: a failed payment is
not a churn and does not revoke the tier today, so the status tags stay as-is.
Do not "fix" that — it is intentional and out of scope.

``stripe:imported`` is a historical "this account originated from the Stripe
import" marker, NOT a status. It is never added or removed here.
"""

from accounts.utils.tags import add_tag, normalize_tag, remove_tag

PLAN_PREFIX = "stripe:plan-"


def reconcile_stripe_status_tags(user, *, active, tier):
    """Recompute a user's ``stripe:*`` status tags from subscription truth.

    Args:
        user: The ``User`` whose status tags should be reconciled.
        active: ``True`` when the user currently has an active (including
            ``cancel_at_period_end``) Stripe subscription; ``False`` when they
            have churned / cancelled / reverted to free.
        tier: The ``Tier`` the active subscription maps to (used for the
            ``stripe:plan-<slug>`` tag), or ``None`` when there is no plan
            (always ``None`` when ``active`` is ``False``).

    Behavior (idempotent on replay):

    - When ``active`` is ``True``: add ``stripe:active``, remove
      ``stripe:churned``, and — if ``tier`` is set — ensure exactly one
      ``stripe:plan-<tier.slug>`` tag (any other ``stripe:plan-*`` tag is
      removed).
    - When ``active`` is ``False``: remove ``stripe:active``, add
      ``stripe:churned``, and remove every ``stripe:plan-*`` tag.
    - ``stripe:imported`` is never touched.
    - Non-``stripe:`` tags are never touched.

    Uses the existing ``add_tag`` / ``remove_tag`` helpers, which each persist
    with ``save(update_fields=['tags'])`` and normalize their input.
    """
    if active:
        add_tag(user, "stripe:active")
        remove_tag(user, "stripe:churned")
        if tier is not None:
            target = normalize_tag(f"{PLAN_PREFIX}{tier.slug}")
            # Drop any OTHER plan tag first so exactly one survives.
            for existing in _other_plan_tags(user, keep=target):
                remove_tag(user, existing)
            add_tag(user, target)
    else:
        remove_tag(user, "stripe:active")
        add_tag(user, "stripe:churned")
        for existing in _other_plan_tags(user, keep=None):
            remove_tag(user, existing)


def _other_plan_tags(user, *, keep):
    """Return ``stripe:plan-*`` tags on ``user`` other than ``keep``.

    Reads ``user.tags`` once and snapshots into a list so callers can mutate
    the user inside the loop without iterating a changing collection. When
    ``keep`` is ``None`` every ``stripe:plan-*`` tag is returned.
    """
    return [
        tag
        for tag in list(user.tags or [])
        if tag.startswith(PLAN_PREFIX) and tag != keep
    ]
