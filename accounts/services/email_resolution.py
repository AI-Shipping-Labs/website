"""Single source of truth for email -> canonical ``User`` resolution (issue #840a).

The precedence is deliberately fixed so future callers (Stripe webhooks,
the #841 merge engine, the operator alias API) never re-implement it:

1. Primary login wins: the ``User`` whose ``email__iexact`` matches.
2. Otherwise the ``User`` owning an ``EmailAlias`` whose normalized
   ``email`` matches.
3. Otherwise ``None``.

The alias step only fires when no primary ``User.email`` matches, which
enforces the model invariant "an address is either a primary login OR an
alias, never both" at lookup time.
"""

from django.contrib.auth import get_user_model

from accounts.models import EmailAlias

User = get_user_model()


def normalize_email(email):
    """Normalize an email the same way the alias rows are stored.

    Returns ``""`` for falsy input so callers can short-circuit cleanly.
    Mirrors the normalization the operator API and the tier-override grant
    use (``User.objects.normalize_email(stripped).lower()``).
    """
    if not email:
        return ""
    return User.objects.normalize_email(str(email).strip()).lower()


def resolve_user_by_email(email):
    """Resolve ``email`` to a canonical ``User`` (primary wins, then alias).

    Returns the matching ``User`` or ``None``. An ACTIVE primary login email
    always takes precedence over an alias -- the alias query only runs when no
    active primary matches.

    The ``is_active`` filter on the primary step matters for the account-merge
    engine (#841): a merged-away account is DEACTIVATED but keeps its original
    ``email`` on the row, while its address becomes an ``EmailAlias`` of the
    surviving canonical account. Skipping inactive primaries here lets a future
    Stripe / relay event for the merged email fall through to the alias and
    resolve to canonical instead of the dead secondary row.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None

    primary = (
        User.objects.filter(email__iexact=normalized, is_active=True).first()
    )
    if primary is not None:
        return primary

    alias = (
        EmailAlias.objects.select_related("user")
        .filter(email=normalized)
        .first()
    )
    if alias is not None:
        return alias.user

    return None
