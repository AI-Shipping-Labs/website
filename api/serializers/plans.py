"""Plain-dict serializers for the plans API (issue #433).

These are pure functions, not DRF serializers. Each ``serialize_*`` takes
a model instance (or queryset/iterable for the list helpers) and returns
the JSON-ready dict shape documented in the spec.

The detail serializer for ``Plan`` is the only one that crosses table
boundaries; everything else is a flat row. Callers should
``prefetch_related`` on the relevant reverse FKs so the nested detail
endpoint does not N+1.
"""

from __future__ import annotations

from plans.models import InterviewNote
from plans.templatetags.plan_markdown import render_plan_markdown


def _isoformat_or_none(value):
    """Return ``value.isoformat()`` for non-null datetimes, else ``None``."""
    if value is None:
        return None
    return value.isoformat()


def serialize_sprint(sprint):
    """Sprint dict shape used by every sprint endpoint."""
    series = sprint.event_series
    lifecycle_badge = sprint.sprint_badge_current
    return {
        "slug": sprint.slug,
        "name": sprint.name,
        "description": sprint.description,
        "outcomes": sprint.outcomes,
        "audience": sprint.audience,
        "start_date": (
            sprint.start_date.isoformat() if sprint.start_date else None
        ),
        "end_date": (
            sprint.end_date.isoformat() if sprint.end_date else None
        ),
        "duration_weeks": sprint.duration_weeks,
        "status": sprint.status,
        "lifecycle_badge": {
            "state": lifecycle_badge.state,
            "label": lifecycle_badge.label,
        },
        "event_series": (
            {"id": series.id, "slug": series.slug} if series else None
        ),
        "created_at": _isoformat_or_none(sprint.created_at),
        "updated_at": _isoformat_or_none(sprint.updated_at),
    }


def serialize_plan_flat(plan):
    """Flat plan row used by the sprint-plans list endpoint."""
    return {
        "id": plan.id,
        "sprint": plan.sprint.slug,
        "user_email": plan.member.email,
        "title": plan.display_title,
        "visibility": plan.visibility,
        "shared_at": _isoformat_or_none(plan.shared_at),
        "created_at": _isoformat_or_none(plan.created_at),
        "updated_at": _isoformat_or_none(plan.updated_at),
    }


def serialize_checkpoint(checkpoint):
    """Single checkpoint row dict (nested under a week or returned solo)."""
    return {
        "id": checkpoint.id,
        "week_id": checkpoint.week_id,
        "description": checkpoint.description,
        "description_html": render_plan_markdown(checkpoint.description),
        "position": checkpoint.position,
        "done_at": _isoformat_or_none(checkpoint.done_at),
    }


def serialize_week_note(note):
    """Singleton participant week-note dict."""
    if note is None:
        return None
    return {
        "id": note.id,
        "week_id": note.week_id,
        "body": note.body,
        "author_email": note.author.email if note.author_id else None,
        "created_at": _isoformat_or_none(note.created_at),
        "updated_at": _isoformat_or_none(note.updated_at),
    }


def serialize_week(week, *, with_checkpoints=True):
    """Week dict. When ``with_checkpoints`` is True the week includes a
    ``checkpoints`` array (used in plan detail). The flat shape is used by
    ``/api/plans/<id>/weeks/`` list/detail endpoints.
    """
    data = {
        "id": week.id,
        "plan_id": week.plan_id,
        "week_number": week.week_number,
        "theme": week.theme,
        "position": week.position,
        "note": serialize_week_note(week.notes.first()),
    }
    if with_checkpoints:
        data["checkpoints"] = [
            serialize_checkpoint(cp)
            for cp in week.checkpoints.all().order_by("position", "id")
        ]
    return data


def serialize_resource(resource):
    """Resource row dict."""
    return {
        "id": resource.id,
        "title": resource.title,
        "url": resource.url,
        "note": resource.note,
        "position": resource.position,
    }


def serialize_deliverable(deliverable):
    """Deliverable row dict."""
    return {
        "id": deliverable.id,
        "description": deliverable.description,
        "position": deliverable.position,
        "done_at": _isoformat_or_none(deliverable.done_at),
    }


def serialize_next_step(next_step):
    """Next-step row dict."""
    return {
        "id": next_step.id,
        "kind": next_step.kind,
        "description": next_step.description,
        "position": next_step.position,
        "done_at": _isoformat_or_none(next_step.done_at),
    }


def serialize_plan_detail(plan, *, viewer=None):
    """Full nested plan dict (weeks -> checkpoints, plus all child rows).

    Reads each child collection from ``.all()`` so callers that want a
    single-roundtrip read should ``prefetch_related`` weeks,
    weeks__checkpoints, resources, deliverables, and next_steps before
    calling this.

    ``viewer`` is required to filter ``interview_notes``: staff see all
    notes, the plan's member sees only their own ``external`` notes,
    everyone else (anonymous / ``None``) sees an empty list. The key
    is always present so the GET / PATCH response shape is stable.
    """
    weeks = list(
        plan.weeks.all().order_by("position", "week_number")
    )
    visible_notes = (
        InterviewNote.objects.visible_to(viewer).filter(plan=plan)
        .order_by("-created_at")
    )
    return {
        "id": plan.id,
        "sprint": plan.sprint.slug,
        "user_email": plan.member.email,
        "title": plan.display_title,
        "visibility": plan.visibility,
        "goal": plan.goal,
        "summary": {
            "current_situation": plan.summary_current_situation,
            "goal": plan.summary_goal,
            "main_gap": plan.summary_main_gap,
            "weekly_hours": plan.summary_weekly_hours,
            "why_this_plan": plan.summary_why_this_plan,
        },
        "focus": {
            "main": plan.focus_main,
            "supporting": list(plan.focus_supporting or []),
        },
        "accountability": plan.accountability,
        "weeks": [serialize_week(w, with_checkpoints=True) for w in weeks],
        "resources": [
            serialize_resource(r)
            for r in plan.resources.all().order_by("position", "id")
        ],
        "deliverables": [
            serialize_deliverable(d)
            for d in plan.deliverables.all().order_by("position", "id")
        ],
        "next_steps": [
            serialize_next_step(n)
            for n in plan.next_steps.all().order_by("position", "id")
        ],
        "interview_notes": [
            serialize_interview_note(n) for n in visible_notes
        ],
        "shared_at": _isoformat_or_none(plan.shared_at),
        "created_at": _isoformat_or_none(plan.created_at),
        "updated_at": _isoformat_or_none(plan.updated_at),
    }


def serialize_interview_note(note):
    """InterviewNote dict shape."""
    return {
        "id": note.id,
        "user_email": note.member.email,
        "plan_id": note.plan_id,
        "visibility": note.visibility,
        "kind": note.kind,
        "body": note.body,
        "tags": list(note.tags or []),
        "source_type": note.source_type,
        "source_metadata": dict(note.source_metadata or {}),
        "created_by_email": (
            note.created_by.email if note.created_by_id else None
        ),
        "created_at": _isoformat_or_none(note.created_at),
        "updated_at": _isoformat_or_none(note.updated_at),
    }
