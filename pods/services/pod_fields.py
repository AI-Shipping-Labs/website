"""Validation for editable study-group fields."""

from dataclasses import dataclass

from pods.models import MEETING_MINUTES_CHOICES
from pods.services import config as pods_config
from pods.services.membership_common import MSG_MAX_MEMBERS_RETIRED

VALID_MEETING_MINUTES = {value for value, _label in MEETING_MINUTES_CHOICES}


@dataclass
class PodFields:
    name: str = ""
    purpose: str = ""
    scheduling_note: str = ""
    internal_notes: str = ""
    meeting_count: int = 1
    meeting_minutes: int = 60


def _parse_int(raw, field_name, label, minimum, maximum, errors):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        errors[field_name] = f"{label} must be a whole number."
        return None
    if value < minimum or value > maximum:
        errors[field_name] = f"{label} must be between {minimum} and {maximum}."
        return None
    return value


def _has_value(data, key):
    return key in data and data[key] is not None and str(data[key]).strip() != ""


def _clean_text(data, cleaned, errors, *, key, label, limit, partial, required=False):
    if partial and key not in data:
        return
    value = str(data.get(key) or "").strip()
    if required and not value:
        errors[key] = f"{label} is required."
    elif len(value) > limit:
        errors[key] = f"{label} must be {limit:,} characters or fewer."
    else:
        cleaned[key] = value


def _clean_meeting_count(data, cleaned, errors, partial):
    if not (_has_value(data, "meeting_count") or not partial):
        return
    raw = pods_config.default_meeting_count()
    if _has_value(data, "meeting_count"):
        raw = data["meeting_count"]
    value = _parse_int(raw, "meeting_count", "Number of meetings", 1, 20, errors)
    if value is not None:
        cleaned["meeting_count"] = value


def _clean_meeting_minutes(data, cleaned, errors, partial):
    if not (_has_value(data, "meeting_minutes") or not partial):
        return
    raw = pods_config.default_meeting_minutes()
    if _has_value(data, "meeting_minutes"):
        raw = data["meeting_minutes"]
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = None
    if value not in VALID_MEETING_MINUTES:
        errors["meeting_minutes"] = "Meeting length must be 30, 45, 60 or 90 minutes."
    else:
        cleaned["meeting_minutes"] = value


def _clean_internal_notes(data, cleaned, errors, partial, staff):
    if "internal_notes" not in data:
        return
    if not staff:
        errors["internal_notes"] = "Only staff can edit internal formation notes."
        return
    _clean_text(
        data,
        cleaned,
        errors,
        key="internal_notes",
        label="Internal formation notes",
        limit=2000,
        partial=partial,
    )


def clean_pod_fields(data, *, current_members=0, partial=False, existing=None, staff=False):
    """Validate editable settings and return ``(cleaned, errors)``."""
    errors = {}
    cleaned = {}
    _clean_text(data, cleaned, errors, key="name", label="Name", limit=80, partial=partial, required=True)
    _clean_text(data, cleaned, errors, key="purpose", label="Purpose", limit=160, partial=partial, required=True)
    if "max_members" in data:
        errors["max_members"] = MSG_MAX_MEMBERS_RETIRED
    _clean_text(
        data,
        cleaned,
        errors,
        key="scheduling_note",
        label="Scheduling guidance",
        limit=500,
        partial=partial,
    )
    _clean_internal_notes(data, cleaned, errors, partial, staff)
    _clean_meeting_count(data, cleaned, errors, partial)
    _clean_meeting_minutes(data, cleaned, errors, partial)
    return cleaned, errors
