"""Response serializers shared by the cohesive Pods API route modules."""

from zoneinfo import ZoneInfo

from django.db.models import Count

from api.serializers.datetime import isoformat_or_none
from pods.models import MEETING_STATUS_CANCELLED, Pod, PodArtifact, PodMeeting
from pods.services import chat as chat_svc
from pods.services import config as pods_config
from pods.services.meeting_rules import meeting_end, meeting_state
from pods.services.stuck_proposals import proposal_waits
from pods.services.suggestions import load_member_availability, suggest_slots


def pod_queryset():
    return Pod.objects.select_related("cohort__course", "sprint", "owner").annotate(
        member_total=Count("memberships", distinct=True),
    )


def get_pod(pod_id):
    return pod_queryset().filter(pk=pod_id).first()


def _activity_fields(pod):
    fields = {
        "course_slug": None,
        "cohort_key": None,
        "cohort_name": None,
        "sprint_slug": None,
        "sprint_name": None,
    }
    if pod.cohort is not None:
        fields.update(
            {
                "course_slug": pod.cohort.course.slug,
                "cohort_key": pod.cohort.external_key,
                "cohort_name": pod.cohort.name,
            }
        )
    if pod.sprint is not None:
        fields.update({"sprint_slug": pod.sprint.slug, "sprint_name": pod.sprint.name})
    return fields


def serialize_summary(pod):
    """Serialize a pod from ``pod_queryset`` with its member annotation."""
    owner_email = None
    if pod.owner_id:
        owner_email = pod.owner.email
    return {
        "id": pod.pk,
        **_activity_fields(pod),
        "name": pod.name,
        "purpose": pod.purpose,
        "scheduling_note": pod.scheduling_note,
        "internal_notes": pod.internal_notes,
        "status": pod.status,
        "member_count": pod.member_total,
        "meeting_count": pod.meeting_count,
        "meeting_minutes": pod.meeting_minutes,
        "owner_email": owner_email,
        "source": pod.source,
        "slack_channel_url": pod.slack_channel_url,
        "created_at": isoformat_or_none(pod.created_at),
    }


def serialize_request(join_request):
    decided_by = None
    if join_request.decided_by_id:
        decided_by = join_request.decided_by.email
    return {
        "id": join_request.pk,
        "email": join_request.user.email,
        "status": join_request.status,
        "message": join_request.message,
        "created_at": isoformat_or_none(join_request.created_at),
        "decided_at": isoformat_or_none(join_request.decided_at),
        "decided_by": decided_by,
        "stale_alerted_at": isoformat_or_none(join_request.stale_alerted_at),
    }


def _serialize_members(memberships, availability):
    return [
        {
            "email": membership.user.email,
            "first_name": membership.user.first_name,
            "last_name": membership.user.last_name,
            "timezone": availability[membership.user_id].timezone_name or None,
            "joined_at": isoformat_or_none(membership.joined_at),
            "source": membership.source,
        }
        for membership in memberships
    ]


def _serialize_slot(slot):
    local_starts = [
        {
            "email": local["member"].user.email,
            "timezone": local["timezone"],
            "local_start": isoformat_or_none(slot.start.astimezone(ZoneInfo(local["timezone"]))),
        }
        for local in slot.local_starts
    ]
    return {
        "start": isoformat_or_none(slot.start),
        "end": isoformat_or_none(slot.end),
        "available": [member.user.email for member in slot.available],
        "if_needed": [member.user.email for member in slot.if_needed],
        "missing": [member.user.email for member in slot.missing],
        "local_starts": local_starts,
    }


def serialize_detail(pod):
    pod = get_pod(pod.pk)
    memberships = list(pod.memberships.select_related("user").order_by("joined_at", "pk"))
    availability = load_member_availability([membership.user for membership in memberships])
    member_avail = [availability[membership.user_id] for membership in memberships]
    slots = suggest_slots(member_avail, pod.meeting_minutes, all_members=member_avail)
    visible = chat_svc.visible_messages(pod).order_by("-created_at", "-pk").values_list("created_at", flat=True)
    data = serialize_summary(pod)
    data.update(
        {
            "meeting_url": pod.meeting_url,
            "message_count": visible.count(),
            "last_message_at": isoformat_or_none(visible.first()),
            "artifact_count": PodArtifact.objects.filter(pod=pod).count(),
            "meetings": serialize_meetings(pod, exclude_cancelled=True),
            "members": _serialize_members(memberships, availability),
            "suggested_slots": [_serialize_slot(slot) for slot in slots],
        }
    )
    return data


def _meeting_queryset(pod):
    return (
        PodMeeting.objects.filter(pod=pod)
        .select_related("proposed_by", "moved_by")
        .prefetch_related("responses__user")
        .order_by("starts_at", "pk")
    )


def _meeting_people(meeting, wait):
    data = {"proposed_by": None, "moved_by": None, "waiting_on": [], "confirmable": None}
    if meeting.proposed_by_id:
        data["proposed_by"] = meeting.proposed_by.email
    if meeting.moved_by_id:
        data["moved_by"] = meeting.moved_by.email
    if wait:
        data["waiting_on"] = [user.email for user in wait.waiting]
        data["confirmable"] = not wait.unconfirmable
    return data


def _serialize_meeting(meeting, state, waits):
    wait = waits.get(meeting.pk)
    series_id = None
    if meeting.series_id:
        series_id = str(meeting.series_id)
    return {
        "id": meeting.pk,
        "number": state.numbers.get(meeting.pk),
        "starts_at": isoformat_or_none(meeting.starts_at),
        "ends_at": isoformat_or_none(meeting_end(meeting)),
        "duration_minutes": meeting.duration_minutes,
        "timezone": meeting.timezone,
        "status": meeting.status,
        "expired": state.is_expired(meeting),
        "series_id": series_id,
        "created_via": meeting.created_via,
        **_meeting_people(meeting, wait),
        "moved_at": isoformat_or_none(meeting.moved_at),
        "previous_starts_at": isoformat_or_none(meeting.previous_starts_at),
        "reminder_sent_at": isoformat_or_none(meeting.reminder_sent_at),
        "responses": [
            {"email": response.user.email, "response": response.response}
            for response in sorted(meeting.responses.all(), key=lambda response: response.pk)
        ],
        "stuck": bool(wait and wait.stuck),
        "stuck_alerted_at": isoformat_or_none(meeting.stuck_alerted_at),
    }


def serialize_meetings(pod, *, exclude_cancelled=False, status=None, only_ids=None):
    meetings = list(_meeting_queryset(pod))
    state = meeting_state(pod, meetings=meetings)
    members = [membership.user for membership in pod.memberships.select_related("user").order_by("joined_at", "pk")]
    responses = []
    for meeting in meetings:
        responses.extend(meeting.responses.all())
    waits = proposal_waits(pod, state, members, responses, hours=pods_config.stuck_proposal_hours())
    rows = []
    for meeting in state.meetings:
        if exclude_cancelled and meeting.status == MEETING_STATUS_CANCELLED:
            continue
        if status and meeting.status != status:
            continue
        if only_ids is not None and meeting.pk not in only_ids:
            continue
        rows.append(_serialize_meeting(meeting, state, waits))
    return rows
