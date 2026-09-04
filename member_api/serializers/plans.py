"""Member-safe plan serializers for ``/member-api/v1``."""

from __future__ import annotations

from accounts.utils.display import display_name


def isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def _annotated_progress(plan):
    return {
        "checkpoints_done": getattr(plan, "checkpoints_done", 0) or 0,
        "checkpoints_total": getattr(plan, "checkpoints_total", 0) or 0,
    }


def _first_related(related_manager):
    related = list(related_manager.all())
    return related[0] if related else None


def serialize_member_plan_summary(plan):
    progress = _annotated_progress(plan)
    return {
        "id": plan.id,
        "sprint": {
            "slug": plan.sprint.slug,
            "name": plan.sprint.name,
        },
        "member": {
            "display_name": display_name(plan.member),
        },
        "title": plan.display_title,
        "visibility": plan.visibility,
        "progress": progress,
        "shared_at": isoformat_or_none(plan.shared_at),
        "created_at": isoformat_or_none(plan.created_at),
        "updated_at": isoformat_or_none(plan.updated_at),
    }


def serialize_member_week_note(note):
    if note is None:
        return None
    return {
        "id": note.id,
        "week_id": note.week_id,
        "body": note.body,
        "created_at": isoformat_or_none(note.created_at),
        "updated_at": isoformat_or_none(note.updated_at),
    }


def serialize_member_checkpoint(checkpoint):
    return {
        "id": checkpoint.id,
        "week_id": checkpoint.week_id,
        "description": checkpoint.description,
        "position": checkpoint.position,
        "done_at": isoformat_or_none(checkpoint.done_at),
    }


def serialize_member_week(week):
    return {
        "id": week.id,
        "plan_id": week.plan_id,
        "week_number": week.week_number,
        "theme": week.theme,
        "position": week.position,
        "note": serialize_member_week_note(_first_related(week.notes)),
        "checkpoints": [
            serialize_member_checkpoint(checkpoint)
            for checkpoint in week.checkpoints.all()
        ],
    }


def serialize_member_resource(resource):
    return {
        "id": resource.id,
        "title": resource.title,
        "url": resource.url,
        "note": resource.note,
        "position": resource.position,
    }


def serialize_member_deliverable(deliverable):
    return {
        "id": deliverable.id,
        "description": deliverable.description,
        "position": deliverable.position,
        "done_at": isoformat_or_none(deliverable.done_at),
    }


def serialize_member_next_step(next_step):
    return {
        "id": next_step.id,
        "kind": next_step.kind,
        "description": next_step.description,
        "position": next_step.position,
        "done_at": isoformat_or_none(next_step.done_at),
    }


def serialize_member_plan_detail(plan):
    data = serialize_member_plan_summary(plan)
    data.update({
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
        "weeks": [serialize_member_week(week) for week in plan.weeks.all()],
        "resources": [
            serialize_member_resource(resource)
            for resource in plan.resources.all()
        ],
        "deliverables": [
            serialize_member_deliverable(deliverable)
            for deliverable in plan.deliverables.all()
        ],
        "next_steps": [
            serialize_member_next_step(next_step)
            for next_step in plan.next_steps.all()
        ],
    })
    return data
