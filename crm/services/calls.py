"""CRM call recording (issue #1949).

The single write path for :class:`crm.models.Call` rows so the Studio
form, the operator API, and the booking app (issue #1948) share one
matching + idempotency contract. Machine writers pass ``source`` plus a
stable ``source_ref``; re-delivered bookings upsert instead of
duplicating.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from crm.models.call import (
    CALL_SOURCE_CHOICES,
    SOURCE_BOOKING,
    SOURCE_CALENDLY,
    SOURCE_MANUAL,
    Call,
)
from crm.models.crm_record import CRMRecord

__all__ = [
    "CALL_SOURCE_CHOICES",
    "SOURCE_BOOKING",
    "SOURCE_CALENDLY",
    "SOURCE_MANUAL",
    "calls_for_member",
    "calls_for_members",
    "record_call",
]


def record_call(
    *,
    email,
    occurred_at,
    summary="",
    source=SOURCE_MANUAL,
    source_ref="",
    created_by=None,
):
    """Create or update one CRM :class:`~crm.models.Call`.

    Matching rules: ``email`` resolves to a user case-insensitively and
    the call links ``contact`` only when that user already has a
    :class:`~crm.models.CRMRecord` -- the CRM stays opt-in, so no record
    is ever auto-created. The raw ``email`` is stored either way, so
    calls whose email matches nobody (yet) still render everywhere.
    When a contact does resolve, the member's earlier still-unlinked
    calls for the same email are adopted by that record, so
    pre-tracking history shows up on the contact page too.

    Idempotency: with a non-blank ``source_ref`` the call upserts on
    ``(source, source_ref)`` -- a repeated delivery for the same key
    updates the row in place and never creates a second one. With a
    blank ``source_ref`` it always creates (manual entries never
    deduplicate).

    Args:
        email: Raw contact email the call is associated with (required).
        occurred_at: Aware datetime for when the call was (required).
        summary: What was discussed (staff-only data).
        source: Free-form origin token; ``manual`` default, ``booking``
            from the booking app, ``calendly`` for the Calendly era.
        source_ref: External id from the source system (e.g. booking
            confirmation id); enables the ``(source, source_ref)``
            upsert. Blank for manual entries.
        created_by: Optional staff user who logged the call. On upsert
            the first non-null attribution wins.

    Returns:
        The created or updated :class:`~crm.models.Call`.
    """
    email = (email or "").strip()
    if not email:
        raise ValueError("record_call requires a contact email.")
    if occurred_at is None:
        raise ValueError("record_call requires occurred_at.")

    user = get_user_model().objects.filter(email__iexact=email).first()
    contact = CRMRecord.objects.filter(user=user).first() if user else None

    defaults = {
        "contact": contact,
        "email": email,
        "occurred_at": occurred_at,
        "summary": summary or "",
    }

    with transaction.atomic():
        if contact is not None:
            # Adopt pre-tracking rows: calls stored for this email while
            # the member was untracked keep contact=NULL until any later
            # recorded call re-resolves the record (#1949).
            Call.objects.filter(
                contact__isnull=True,
                email__iexact=email,
            ).update(contact=contact)
        if source_ref:
            existing = Call.objects.filter(
                source=source,
                source_ref=source_ref,
            ).first()
            if existing is not None:
                for field, value in defaults.items():
                    setattr(existing, field, value)
                if created_by is not None and existing.created_by_id is None:
                    # First writer wins attribution; retries keep it.
                    existing.created_by = created_by
                existing.save()
                return existing
            return Call.objects.create(
                source=source,
                source_ref=source_ref,
                created_by=created_by,
                **defaults,
            )
        return Call.objects.create(
            source=source,
            source_ref="",
            created_by=created_by,
            **defaults,
        )


def calls_for_member(user):
    """Newest-first calls belonging to one member (read helper, #1949).

    Union of calls linked through the member's CRM record and calls
    recorded for their email while ``contact`` was still NULL, so the
    picture never loses pre-tracking history.
    """
    return (
        Call.objects.filter(Q(contact__user=user) | Q(contact__isnull=True, email__iexact=user.email))
        .select_related("contact__user", "created_by")
        .order_by("-occurred_at", "-created_at")
    )


def calls_for_members(users):
    """Batched ``{user_id: [Call, ...]}`` map for a page of users.

    Same union semantics as :func:`calls_for_member` resolved in one
    query for the whole page, so the export's query budget stays flat
    as the member count grows (issue #1949 AC15 guard).
    """
    users = list(users)
    if not users:
        return {}
    email_to_user = {}
    for user in users:
        email_to_user.setdefault(user.email.lower(), user.pk)
    rows = (
        Call.objects.filter(
            Q(contact__user__in=users)
            | Q(
                contact__isnull=True,
                email__in=[user.email for user in users],
            ),
        )
        .select_related("created_by")
        .order_by("-occurred_at", "-created_at")
    )
    grouped = {user.pk: [] for user in users}
    for call in rows:
        user_id = call.contact.user_id if call.contact_id else None
        if user_id is None:
            user_id = email_to_user.get(call.email.lower())
        if user_id in grouped:
            grouped[user_id].append(call)
    return grouped
