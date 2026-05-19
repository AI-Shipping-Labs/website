"""Helpers for populating ``User.first_name`` / ``User.last_name`` from
external identity providers (issue #699).

External sources of a user's display name today:

- Stripe checkout (``customer_details.name``) — a single string.
- OAuth providers — Google / Slack ship ``given_name`` / ``family_name``
  as separate OIDC claims; GitHub ships only a combined ``name``.
- Slack workspace probe (``users.lookupByEmail``) — exposes either
  ``profile.first_name`` / ``profile.last_name`` or ``real_name``.

``set_name_from_external`` is the single in-memory mutator used by all
three paths. The caller is responsible for the DB save so the name
fields can be folded into an existing ``update_fields=[...]`` list,
avoiding a second round-trip.

Trust order is enforced by the "only fill empty" rule alone: once any
source has filled a field, no other source touches it. The user can
always overwrite via Studio once that UI lands.
"""

import logging

logger = logging.getLogger(__name__)


def _split_full_name(full_name):
    """Split a single name string on the LAST whitespace.

    Returns ``(first, last)``. A single token goes to ``first`` and
    ``last`` is the empty string. Surrounding and inner extra whitespace
    is collapsed.

    Examples:
        "Salvador Castillo Raya" -> ("Salvador Castillo", "Raya")
        "Madonna"                -> ("Madonna", "")
        "   Alex  Grigorev   "   -> ("Alex", "Grigorev")
        ""                       -> ("", "")
    """
    if not full_name:
        return "", ""
    # ``split()`` with no args collapses any run of whitespace and strips
    # surrounding whitespace. This handles "   Alex  Grigorev   " in one step.
    tokens = full_name.split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    return " ".join(tokens[:-1]), tokens[-1]


def set_name_from_external(user, *, full_name=None, first=None, last=None, source):
    """Populate ``user.first_name`` / ``user.last_name`` from an external identity.

    Only writes to fields that are currently empty (after ``strip()``).
    NEVER overwrites a value the user has set, regardless of source.
    Returns ``True`` iff any field was changed; caller is responsible
    for ``save()``.

    Args:
        user: The ``User`` instance to mutate in memory. Not saved.
        full_name: A single string from an IdP that did not split it
            (Stripe, GitHub). Split on the LAST whitespace.
        first: Pre-split first-name component (Google ``given_name``,
            Slack ``given_name``). Either side may be missing.
        last: Pre-split family-name component (Google ``family_name``,
            Slack ``family_name``). Either side may be missing.
        source: One of ``"stripe"``, ``"oauth:google"``,
            ``"oauth:github"``, ``"oauth:slack"``, ``"slack_probe"`` —
            used for log lines so on-call can trace where a name came
            from. Required keyword argument.

    Returns:
        bool: ``True`` if either ``first_name`` or ``last_name`` was
        mutated on the in-memory user; ``False`` otherwise.
    """
    # Pre-split (first/last) wins over combined ``full_name`` when both
    # are provided — pre-split has already done the work the splitter
    # would otherwise guess at.
    if first is not None or last is not None:
        new_first = (first or "").strip()
        new_last = (last or "").strip()
    else:
        new_first, new_last = _split_full_name(full_name or "")

    changed_fields = []

    # Only fill an empty (or whitespace-only) existing value.
    if new_first and not (user.first_name or "").strip():
        user.first_name = new_first
        changed_fields.append("first_name")

    if new_last and not (user.last_name or "").strip():
        user.last_name = new_last
        changed_fields.append("last_name")

    if not changed_fields:
        return False

    # No PII in the log body — just the pk, the source, and which fields
    # were touched. The actual name values are intentionally omitted.
    logger.info(
        "set_name_from_external: user=%s source=%s fields=%s",
        user.pk,
        source,
        ",".join(changed_fields),
    )
    return True
