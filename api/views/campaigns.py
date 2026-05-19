"""Email campaign draft API endpoints (issue #676).

This operator API intentionally exposes ONLY draft authoring: API callers can
list, create, read, and patch ``EmailCampaign`` rows, but cannot send, test-
send, duplicate, or delete them. Sending remains a deliberate human action via
Studio. This module must NEVER import or invoke the campaign-send task; a
sentinel test in ``api/tests/test_campaigns.py`` greps this source to enforce
that constraint.

Archive (hide) by ``PATCH {"is_archived": true}``; there is no DELETE route
registered for either endpoint, so DELETE returns 405 from ``require_methods``.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.tags import normalize_tags
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from email_app.models import EmailCampaign

READ_ONLY_FIELDS = {
    "id",
    "status",
    "sent_at",
    "sent_count",
    "created_at",
}

WRITABLE_FIELDS = {
    "subject",
    "body",
    "target_min_level",
    "target_tags_any",
    "target_tags_none",
    "slack_filter",
    "audience_verification",
    "is_archived",
}

VALID_STATUSES = {value for value, _label in EmailCampaign.STATUS_CHOICES}
VALID_TARGET_LEVELS = {value for value, _label in EmailCampaign.TARGET_LEVEL_CHOICES}
VALID_SLACK_FILTERS = {value for value, _label in EmailCampaign.SLACK_FILTER_CHOICES}
VALID_AUDIENCE_VERIFICATIONS = {
    value for value, _label in EmailCampaign.AUDIENCE_VERIFICATION_CHOICES
}


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_campaign(campaign):
    return {
        "id": campaign.pk,
        "subject": campaign.subject,
        "body": campaign.body,
        "target_min_level": campaign.target_min_level,
        "target_tags_any": list(campaign.target_tags_any or []),
        "target_tags_none": list(campaign.target_tags_none or []),
        "slack_filter": campaign.slack_filter,
        "audience_verification": campaign.audience_verification,
        "status": campaign.status,
        "is_archived": campaign.is_archived,
        "sent_at": _iso(campaign.sent_at),
        "sent_count": campaign.sent_count,
        "created_at": _iso(campaign.created_at),
    }


def _body_must_be_object_response():
    return error_response(
        "Body must be a JSON object",
        "invalid_type",
        details={"field": "body", "expected": "object"},
    )


def _validation_response(details, message="Validation error"):
    return error_response(
        message,
        "validation_error",
        status=422,
        details=details,
    )


def _read_only_field_response(field):
    return error_response(
        f"{field} is read-only",
        "read_only_field",
        status=422,
        details={"field": field},
    )


def _coerce_optional_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _collect_campaign_values(data, *, existing=None):
    """Validate API payload and return model field values + per-field errors.

    ``existing`` is ``None`` for POST (creation) so required fields are checked
    even when absent; on PATCH it's the row being updated, and we only
    validate keys that are actually present (partial update).
    """
    errors = {}
    values = {}

    if "subject" in data:
        subject = _coerce_optional_text(data["subject"])
        if not subject:
            errors["subject"] = "Subject is required."
        elif len(subject) > 255:
            errors["subject"] = "Subject must be at most 255 characters."
        values["subject"] = subject
    elif existing is None:
        errors["subject"] = "Subject is required."

    if "body" in data:
        raw_body = data["body"]
        if not isinstance(raw_body, str) or not raw_body.strip():
            errors["body"] = "Body is required."
        values["body"] = raw_body if isinstance(raw_body, str) else ""
    elif existing is None:
        errors["body"] = "Body is required."

    if "target_min_level" in data:
        try:
            target_min_level = int(data["target_min_level"])
        except (TypeError, ValueError):
            target_min_level = None
        if target_min_level not in VALID_TARGET_LEVELS:
            errors["target_min_level"] = "Unknown tier level."
        values["target_min_level"] = target_min_level

    if "slack_filter" in data:
        slack_filter = _coerce_optional_text(data["slack_filter"])
        if slack_filter not in VALID_SLACK_FILTERS:
            errors["slack_filter"] = "Unknown slack filter."
        values["slack_filter"] = slack_filter

    if "audience_verification" in data:
        audience_verification = _coerce_optional_text(
            data["audience_verification"]
        )
        if audience_verification not in VALID_AUDIENCE_VERIFICATIONS:
            errors["audience_verification"] = "Unknown audience verification."
        values["audience_verification"] = audience_verification

    for field in ("target_tags_any", "target_tags_none"):
        if field in data:
            raw = data[field]
            if not isinstance(raw, list) or not all(
                isinstance(tag, str) for tag in raw
            ):
                errors[field] = "Must be an array of strings."
            else:
                values[field] = normalize_tags(raw)

    if "is_archived" in data:
        if not isinstance(data["is_archived"], bool):
            errors["is_archived"] = "Must be a boolean."
        else:
            values["is_archived"] = data["is_archived"]

    return values, errors


def _apply_campaign_values(campaign, values):
    for field, value in values.items():
        if field in WRITABLE_FIELDS:
            setattr(campaign, field, value)


def _parse_bool_query(value):
    """Return True/False/None for archived query filter values."""
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
def campaigns_collection(request):
    """GET/POST ``/api/campaigns``.

    DELETE is intentionally NOT in the allowed-methods list: archive via
    ``PATCH {"is_archived": true}`` instead. DELETE requests fall through to
    the ``require_methods`` 405 response.
    """
    if request.method == "GET":
        qs = EmailCampaign.objects.all().order_by("-created_at")

        status_filter = request.GET.get("status")
        if status_filter:
            if status_filter not in VALID_STATUSES:
                return _validation_response({"status": "Unknown status."})
            qs = qs.filter(status=status_filter)

        archived_raw = request.GET.get("archived")
        if archived_raw is not None:
            archived = _parse_bool_query(archived_raw)
            if archived is None:
                return _validation_response({"archived": "Must be a boolean."})
            qs = qs.filter(is_archived=archived)

        return JsonResponse(
            {"campaigns": [_serialize_campaign(c) for c in qs]},
            status=200,
        )

    # POST: create a draft. ``status`` in the body is silently overwritten to
    # "draft" (NOT 422) so automation that round-trips the detail JSON keeps
    # working. Every other read-only field is rejected with 422 as usual.
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    for field in sorted(READ_ONLY_FIELDS):
        if field == "status":
            # Silently overwritten below; see contract above.
            continue
        if field in data:
            return _read_only_field_response(field)

    values, errors = _collect_campaign_values(data, existing=None)
    if errors:
        return _validation_response(errors)

    campaign = EmailCampaign(status="draft")
    _apply_campaign_values(campaign, values)
    campaign.save()
    return JsonResponse(_serialize_campaign(campaign), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH")
def campaign_detail(request, campaign_id):
    """GET/PATCH ``/api/campaigns/<id>``.

    DELETE is not in the allowed-methods list (see ``campaigns_collection``
    docstring); ``require_methods`` returns 405 for it.
    """
    campaign = EmailCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return error_response(
            "Campaign not found",
            "unknown_campaign",
            status=404,
        )

    if request.method == "GET":
        return JsonResponse(_serialize_campaign(campaign), status=200)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    # ``status`` is a special-case read-only field: PATCH with the row's
    # current status is a silent no-op (so round-tripping detail JSON works);
    # any other value is rejected with the immutability error message.
    if "status" in data:
        if data["status"] != campaign.status:
            return _validation_response({
                "status": (
                    "status is immutable through the API; "
                    "use Studio to send the campaign."
                ),
            })

    for field in sorted(READ_ONLY_FIELDS):
        if field == "status":
            continue
        if field in data:
            return _read_only_field_response(field)

    values, errors = _collect_campaign_values(data, existing=campaign)
    if errors:
        return _validation_response(errors)

    _apply_campaign_values(campaign, values)
    campaign.save()
    return JsonResponse(_serialize_campaign(campaign), status=200)
