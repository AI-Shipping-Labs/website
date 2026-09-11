"""Privacy-bounded Maven occurrence serialization for the operator API."""

import re

from api.serializers.datetime import isoformat_or_none
from integrations.services.maven import SLACK_NOT_IN_WORKSPACE_NOTE, STEP_NAMES
from integrations.services.maven_attention import (
    failed_step_names,
    occurrence_attention_reasons,
)

_SAFE_EXCEPTION_CLASS = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,254}(?:Error|Exception|DoesNotExist)$"
)
_SAFE_CONTROLLED_REASONS = {SLACK_NOT_IN_WORKSPACE_NOTE}


def serialize_maven_step_error(value):
    """Return a proven-safe persisted error and whether redaction occurred."""
    value = value or ""
    if not value:
        return "", False
    if value in _SAFE_CONTROLLED_REASONS or _SAFE_EXCEPTION_CLASS.fullmatch(value):
        return value, False
    return "redacted", True


def serialize_maven_occurrence(occurrence, *, detail=False, now=None):
    """Serialize one occurrence with one stable summary/detail vocabulary."""
    attention_reasons = occurrence_attention_reasons(occurrence, now=now)
    data = {
        "id": occurrence.pk,
        "user": (
            {"id": occurrence.user_id, "email": occurrence.user.email}
            if occurrence.user_id
            else None
        ),
        "occurrence_email": (
            "" if occurrence.payload_redacted_at is not None else occurrence.email
        ),
        "course": occurrence.course,
        "cohort": occurrence.cohort,
        "course_key": occurrence.course_key,
        "cohort_key": occurrence.cohort_key,
        "event_type": occurrence.event_type,
        "lifecycle": occurrence.lifecycle,
        "outcome": occurrence.outcome,
        "failed_steps": failed_step_names(occurrence),
        "needs_attention_steps": [
            name for name in STEP_NAMES if name in attention_reasons
        ],
        "payload_redacted_at": isoformat_or_none(occurrence.payload_redacted_at),
        "created_at": isoformat_or_none(occurrence.created_at),
        "updated_at": isoformat_or_none(occurrence.updated_at),
    }
    if not detail:
        return data

    data.update(
        {
            "account_created": occurrence.account_created,
            "welcome_eligible": occurrence.welcome_eligible,
            "removed_at": isoformat_or_none(occurrence.removed_at),
            "steps": [
                _serialize_step(occurrence, name, attention_reasons)
                for name in STEP_NAMES
            ],
        }
    )
    return data


def _serialize_step(occurrence, name, attention_reasons):
    last_error, error_redacted = serialize_maven_step_error(
        getattr(occurrence, f"{name}_error")
    )
    return {
        "name": name,
        "status": getattr(occurrence, f"{name}_status"),
        "attempts": getattr(occurrence, f"{name}_attempts"),
        "attempted_at": isoformat_or_none(
            getattr(occurrence, f"{name}_attempted_at")
        ),
        "completed_at": isoformat_or_none(
            getattr(occurrence, f"{name}_completed_at")
        ),
        "needs_attention": name in attention_reasons,
        "last_error": last_error,
        "error_redacted": error_redacted,
    }
