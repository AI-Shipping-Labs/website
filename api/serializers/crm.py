"""Serializers for CRM record payloads exposed by operator APIs."""

from django.urls import reverse

from api.serializers.datetime import isoformat_or_none
from crm.services.persona import resolve_crm_persona


def serialize_crm_record_summary(record):
    """Return the compact CRM record shape, or ``None``."""
    if record is None:
        return None
    return {
        "id": record.pk,
        "status": record.status,
        "persona": resolve_crm_persona(record),
    }


def serialize_crm_record_full(record):
    """Return the complete CRM record shape used by CRM export."""
    if record is None:
        return None
    return {
        **serialize_crm_record_summary(record),
        "summary": record.summary or "",
        "next_steps": record.next_steps or "",
        "created_at": isoformat_or_none(record.created_at),
        "updated_at": isoformat_or_none(record.updated_at),
    }


def serialize_crm_record_for_operator(record):
    """Return the compact shape plus the existing Studio navigation URLs."""
    if record is None:
        return None
    path = reverse("studio_crm_detail", kwargs={"crm_id": record.pk})
    return {
        **serialize_crm_record_summary(record),
        "studio_url": path,
        "onboarding_url": f"{path}#onboarding",
    }
