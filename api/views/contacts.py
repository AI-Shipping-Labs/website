"""Contacts API endpoints (issue #431).

Three JSON-in / JSON-out endpoints under ``/api/contacts/``:

- ``POST /import`` -- bulk upsert. Per-row tags are MERGED into the user's
  existing tags (idempotent append, NOT replace). Reuses
  ``studio.services.contacts_import.import_contact_rows`` so the per-row
  upsert logic lives in one place.
- ``GET /export`` -- dump every ``User`` row. JSON by default; ``?format=csv``
  switches to ``text/csv`` with the same columns as the Studio CSV export.
- ``POST /<email>/tags`` -- REPLACE the user's tags with the normalized list.
  Different semantics from import on purpose (import merges; this replaces).

All three are gated by ``token_required`` and rejected with ``405`` for the
wrong HTTP method. ``token_required`` is the outermost decorator so 401 fires
before CSRF or method checks.
"""

import csv
import datetime

from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.tags import normalize_tags
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from payments.models import Tier
from studio.services.contacts_import import import_contact_rows

User = get_user_model()


# Columns shared between the JSON and CSV export formats. The CSV header row
# is taken verbatim from this list; the JSON dicts use the same keys.
EXPORT_COLUMNS = [
    "email",
    "first_name",
    "last_name",
    "tags",
    "tier",
    "email_verified",
    "unsubscribed",
    "date_joined",
    "last_login",
]


def _isoformat_or_none(value):
    """Return ``value.isoformat()`` for non-null datetimes, else None."""
    if value is None:
        return None
    return value.isoformat()


def _serialize_user(user):
    """Build the export dict for a single ``User`` row.

    Tier resolution mirrors ``user_export_csv`` minus the override layer:
    the base ``user.tier.slug`` (or "free" when ``tier_id is None``). Effective
    tier override resolution is intentionally NOT applied here for v1 -- a
    follow-up issue can add an override-aware export when the API has callers
    that need it.
    """
    tier_slug = user.tier.slug if user.tier_id else "free"
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "tags": list(user.tags or []),
        "tier": tier_slug,
        "email_verified": user.email_verified,
        "unsubscribed": user.unsubscribed,
        "date_joined": _isoformat_or_none(user.date_joined),
        "last_login": _isoformat_or_none(user.last_login),
    }


@token_required
@csrf_exempt
@require_methods("POST")
def contacts_import(request):
    """Bulk upsert contacts.

    Request body::

        {
          "contacts": [
            {"email": "...", "tags": ["..."], "tier": "main"}, ...
          ],
          "default_tag": "...",
          "default_tier": "..."
        }

    ``default_tag`` / ``default_tier`` are optional and apply to every row.
    Per-row ``tags`` MERGE into the user's existing tags (idempotent). Per-row
    ``tier`` (when level > 0) creates a long-lived ``TierOverride``.

    Returns ``{created, updated, skipped, malformed, warnings}`` matching the
    fields of ``ImportResult``.
    """
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    contacts = data.get("contacts") if isinstance(data, dict) else None
    if not isinstance(contacts, list):
        return error_response(
            "contacts must be a list",
            "missing_contacts",
        )

    default_tag = data.get("default_tag") or ""
    default_tier_slug = data.get("default_tier") or ""
    default_tier = None
    if default_tier_slug:
        default_tier = Tier.objects.filter(slug=default_tier_slug).first()
        if default_tier is None:
            return error_response(
                f"Unknown tier: {default_tier_slug}",
                "unknown_tier",
            )

    result = import_contact_rows(
        contacts,
        default_tag=default_tag,
        default_tier=default_tier,
        granted_by=request.user,
    )

    return JsonResponse(
        {
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "malformed": result.malformed,
            "warnings": [
                {"row": row, "value": value, "reason": reason}
                for (row, value, reason) in result.warnings
            ],
        },
        status=200,
    )


@token_required
@require_methods("GET")
def contacts_export(request):
    """Dump every ``User`` row in the system.

    Default response is JSON. ``?format=csv`` switches to ``text/csv`` with an
    ``aishippinglabs-contacts-<utc-timestamp>.csv`` attachment header. Output
    is ordered by ``id`` so repeat calls are deterministic.
    """
    users = list(
        User.objects.select_related("tier").order_by("id")
    )
    rows = [_serialize_user(user) for user in users]

    fmt = (request.GET.get("format") or "").lower()
    if fmt == "csv":
        timestamp = (
            timezone.now()
            .astimezone(datetime.timezone.utc)
            .strftime("%Y%m%d-%H%M%S")
        )
        filename = f"aishippinglabs-contacts-{timestamp}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )
        writer = csv.writer(response)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row["email"],
                    row["first_name"],
                    row["last_name"],
                    ",".join(row["tags"]),
                    row["tier"],
                    "true" if row["email_verified"] else "false",
                    "true" if row["unsubscribed"] else "false",
                    row["date_joined"] or "",
                    row["last_login"] or "",
                ]
            )
        return response

    return JsonResponse({"contacts": rows}, status=200)


@token_required
@csrf_exempt
@require_methods("POST")
def contacts_set_tags(request, email):
    """Replace a contact's tags with the given normalized list.

    Different semantics from import on purpose: import merges, this replaces.
    An empty list clears the user's tags.
    """
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error

    raw_tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(raw_tags, list):
        return error_response(
            "tags must be a list",
            "missing_tags",
        )

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return error_response(
            "Contact not found",
            "contact_not_found",
            status=404,
        )

    normalized = normalize_tags(raw_tags)
    user.tags = normalized
    user.save(update_fields=["tags"])

    return JsonResponse(
        {"email": user.email, "tags": normalized},
        status=200,
    )
