"""Derived account-lifecycle reporting helpers.

The lifecycle is intentionally derived from existing account and signup
attribution fields. It is a reporting segmentation, not a persisted state.
"""

from django.db.models import Q

from accounts.models.user import (
    SIGNUP_SOURCE_NEWSLETTER,
    SIGNUP_SOURCE_OAUTH,
    SIGNUP_SOURCE_SIGNUP,
    SIGNUP_SOURCE_STAFF_CREATE,
)

ACCOUNT_LIFECYCLE_NEWSLETTER_ONLY = "newsletter_only"
ACCOUNT_LIFECYCLE_FULL_ACCOUNT = "full_account"
ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN = "imported_or_unknown"

ACCOUNT_LIFECYCLE_CHOICES = (
    (ACCOUNT_LIFECYCLE_NEWSLETTER_ONLY, "Newsletter-only"),
    (ACCOUNT_LIFECYCLE_FULL_ACCOUNT, "Full account"),
    (ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN, "Imported / unknown"),
)
ACCOUNT_LIFECYCLE_LABELS = dict(ACCOUNT_LIFECYCLE_CHOICES)
ACCOUNT_LIFECYCLE_VALUES = tuple(value for value, _label in ACCOUNT_LIFECYCLE_CHOICES)

ACCOUNT_CREATING_SIGNUP_SOURCES = (
    SIGNUP_SOURCE_SIGNUP,
    SIGNUP_SOURCE_OAUTH,
    SIGNUP_SOURCE_STAFF_CREATE,
)
ACCOUNT_CREATING_SIGNUP_PATHS = (
    "email_password",
    "google_oauth",
    "slack_oauth",
    "github_oauth",
    "stripe_checkout",
    "admin_created",
)


def normalize_account_lifecycle(value):
    """Return a valid lifecycle slug or an empty string for "all"."""
    value = (value or "").strip()
    if value in ACCOUNT_LIFECYCLE_VALUES:
        return value
    return ""


def lifecycle_label(value):
    """Return the operator-facing label for a lifecycle slug."""
    return ACCOUNT_LIFECYCLE_LABELS.get(
        value,
        ACCOUNT_LIFECYCLE_LABELS[ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN],
    )


def derive_account_lifecycle(user, *, signup_path=None):
    """Derive a user's account-lifecycle bucket."""
    if (
        user.signup_source == SIGNUP_SOURCE_NEWSLETTER
        and not user.account_activated
    ):
        return ACCOUNT_LIFECYCLE_NEWSLETTER_ONLY

    if signup_path is None:
        attribution = getattr(user, "attribution", None)
        signup_path = getattr(attribution, "signup_path", "") if attribution else ""

    if (
        user.account_activated
        or user.signup_source in ACCOUNT_CREATING_SIGNUP_SOURCES
        or signup_path in ACCOUNT_CREATING_SIGNUP_PATHS
    ):
        return ACCOUNT_LIFECYCLE_FULL_ACCOUNT

    return ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN


def account_lifecycle_q(value, *, user_prefix="", signup_path_lookup=None):
    """Return a queryset ``Q`` for a lifecycle bucket."""
    if value not in ACCOUNT_LIFECYCLE_VALUES:
        return Q()

    newsletter_q = Q(**{
        f"{user_prefix}signup_source": SIGNUP_SOURCE_NEWSLETTER,
        f"{user_prefix}account_activated": False,
    })
    account_source_q = Q(**{
        f"{user_prefix}signup_source__in": ACCOUNT_CREATING_SIGNUP_SOURCES,
    })
    activated_q = Q(**{f"{user_prefix}account_activated": True})

    if signup_path_lookup is None:
        signup_path_lookup = f"{user_prefix}attribution__signup_path"
    path_q = Q(**{
        f"{signup_path_lookup}__in": ACCOUNT_CREATING_SIGNUP_PATHS,
    })
    full_q = ~newsletter_q & (activated_q | account_source_q | path_q)

    if value == ACCOUNT_LIFECYCLE_NEWSLETTER_ONLY:
        return newsletter_q
    if value == ACCOUNT_LIFECYCLE_FULL_ACCOUNT:
        return full_q
    return ~newsletter_q & ~full_q


def lifecycle_payload(user, *, signup_path=None):
    """Return wire-format lifecycle fields for a user payload."""
    lifecycle = derive_account_lifecycle(user, signup_path=signup_path)
    return {
        "signup_source": user.signup_source,
        "account_activated": bool(user.account_activated),
        "account_lifecycle": lifecycle,
    }


__all__ = [
    "ACCOUNT_LIFECYCLE_CHOICES",
    "ACCOUNT_LIFECYCLE_FULL_ACCOUNT",
    "ACCOUNT_LIFECYCLE_IMPORTED_OR_UNKNOWN",
    "ACCOUNT_LIFECYCLE_LABELS",
    "ACCOUNT_LIFECYCLE_NEWSLETTER_ONLY",
    "ACCOUNT_LIFECYCLE_VALUES",
    "account_lifecycle_q",
    "derive_account_lifecycle",
    "lifecycle_label",
    "lifecycle_payload",
    "normalize_account_lifecycle",
]
