"""Privacy-safe affected-entity resolution for completed django-q tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.urls import reverse

from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from integrations.models import ContentSource
from integrations.services.banner_generator.content_models import (
    SUPPORTED_CONTENT_TYPES,
)

logger = logging.getLogger(__name__)

BANNER_FUNC = "integrations.services.banner_generator.tasks.render_banner_for_content"
CONTENT_SYNC_FUNC = "integrations.services.github.sync_content_source"
EVENT_FUNCS = {
    "events.tasks.notify_reschedule.send_reschedule_notice_fanout",
    "events.tasks.notify_reschedule.send_reschedule_notice_one",
    "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
    "events.tasks.notify_cancellation.send_cancellation_notice_one",
    "events.tasks.notify_series_invite.send_series_update",
    "events.tasks.notify_series_invite.send_series_cancellation",
    "events.tasks.send_post_event_followup.send_post_event_followup_fanout",
    "events.tasks.send_post_event_followup.send_post_event_followup_one",
}
CAMPAIGN_FUNCS = {
    "email_app.tasks.send_campaign.send_campaign",
    "email_app.tasks.send_campaign.send_campaign_batch",
}


@dataclass(frozen=True)
class _Reference:
    kind: str
    identifier: int | UUID


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _safe_payload(task):
    try:
        args = task.args
        kwargs = task.kwargs
    except Exception:
        logger.warning("Unable to decode affected-entity task payload")
        return None, None
    if not isinstance(args, (tuple, list)):
        args = ()
    if not isinstance(kwargs, dict):
        kwargs = {}
    return args, kwargs


def _extract_reference(task):
    """Return one allow-listed typed reference, never payload-derived text."""
    func = getattr(task, "func", None)
    if func not in ({BANNER_FUNC, CONTENT_SYNC_FUNC} | EVENT_FUNCS | CAMPAIGN_FUNCS):
        return None
    args, kwargs = _safe_payload(task)
    if args is None:
        return None

    if func == BANNER_FUNC:
        if len(args) < 2 or args[0] not in SUPPORTED_CONTENT_TYPES:
            return None
        identifier = _positive_int(args[1])
        return _Reference(args[0], identifier) if identifier else None

    if func == CONTENT_SYNC_FUNC:
        if not args or not isinstance(args[0], ContentSource):
            return None
        try:
            identifier = UUID(str(args[0].pk))
        except (TypeError, ValueError, AttributeError):
            return None
        return _Reference("content_source", identifier)

    if func in EVENT_FUNCS:
        identifier = _positive_int(args[0]) if args else None
        return _Reference("event", identifier) if identifier else None

    keyword_present = "campaign_id" in kwargs
    keyword_id = _positive_int(kwargs.get("campaign_id")) if keyword_present else None
    positional_present = bool(args)
    positional_id = _positive_int(args[0]) if positional_present else None
    if keyword_present and keyword_id is None:
        return None
    if positional_present and positional_id is None:
        return None
    if keyword_present and positional_present and keyword_id != positional_id:
        return None
    identifier = keyword_id if keyword_present else positional_id
    return _Reference("campaign", identifier) if identifier else None


def _kind_config(kind):
    return {
        "article": (Article, "Article", "title", "studio_article_edit", "article_id"),
        "course": (Course, "Course", "title", "studio_course_edit", "course_id"),
        "project": (Project, "Project", "title", "studio_project_review", "project_id"),
        "download": (Download, "Download", "title", "studio_download_edit", "download_id"),
        "workshop": (Workshop, "Workshop", "title", "studio_workshop_detail", "workshop_id"),
        "event": (Event, "Event", "title", "studio_event_edit", "event_id"),
        "event_series": (
            EventSeries,
            "Event series",
            "name",
            "studio_event_series_detail",
            "series_id",
        ),
        "campaign": (
            EmailCampaign,
            "Campaign",
            "subject",
            "studio_campaign_detail",
            "campaign_id",
        ),
        "content_source": (
            ContentSource,
            "Content source",
            "repo_name",
            "studio_sync_dashboard",
            None,
        ),
    }.get(kind)


def _serialize_reference(reference, obj):
    config = _kind_config(reference.kind)
    if config is None:
        return None
    _model, kind_label, label_attr, route_name, route_kwarg = config
    json_id = str(reference.identifier) if isinstance(reference.identifier, UUID) else reference.identifier
    if obj is None:
        return {
            "kind": reference.kind,
            "id": json_id,
            "label": f"{kind_label} {reference.identifier} (not found)",
            "state": "missing",
            "studio_url": None,
        }
    record_label = str(getattr(obj, label_attr, "") or "").strip()
    if reference.kind == "content_source":
        label = f"{kind_label} — {record_label}"
        studio_url = f"{reverse(route_name)}#content-source-{reference.identifier}"
    else:
        label = f"{kind_label} #{reference.identifier}"
        if record_label:
            label += f" — {record_label}"
        studio_url = reverse(route_name, kwargs={route_kwarg: reference.identifier})
    return {
        "kind": reference.kind,
        "id": json_id,
        "label": label,
        "state": "available",
        "studio_url": studio_url,
    }


def _safe_extract_reference(task):
    try:
        return _extract_reference(task)
    except Exception:
        logger.warning("Affected-entity payload inspection failed")
        return None


def _safe_serialize_reference(reference, obj):
    try:
        return _serialize_reference(reference, obj)
    except Exception:
        logger.warning("Affected-entity serialization failed")
        return None


def resolve_task_affected_entity(task):
    """Resolve one task with at most one current-record lookup."""
    reference = _safe_extract_reference(task)
    if reference is None:
        return None
    config = _kind_config(reference.kind)
    if config is None:
        return None
    model = config[0]
    try:
        obj = model.objects.filter(pk=reference.identifier).first()
    except Exception:
        logger.warning("Affected-entity lookup failed for recognized task")
        obj = None
    return _safe_serialize_reference(reference, obj)


def resolve_tasks_affected_entities(tasks):
    """Bulk-resolve a bounded task slice, deduplicated by kind and identifier."""
    tasks = list(tasks)
    references = {}
    for task in tasks:
        try:
            task_id = task.id
        except Exception:
            logger.warning("Affected-entity task identity lookup failed")
            continue
        references[task_id] = _safe_extract_reference(task)
    grouped = {}
    for reference in references.values():
        if reference is not None:
            grouped.setdefault(reference.kind, set()).add(reference.identifier)

    records = {}
    for kind, identifiers in grouped.items():
        config = _kind_config(kind)
        if config is None:
            continue
        try:
            records[kind] = config[0].objects.in_bulk(identifiers)
        except Exception:
            logger.warning("Bulk affected-entity lookup failed for recognized task kind")
            records[kind] = {}

    resolved = {}
    for task_id, reference in references.items():
        if reference is None:
            resolved[task_id] = None
            continue
        obj = records.get(reference.kind, {}).get(reference.identifier)
        resolved[task_id] = _safe_serialize_reference(reference, obj)
    return resolved
