"""Presentation context for a single member-facing study-group page."""

from dataclasses import dataclass, field

from django.utils import timezone

from pods.models import POD_STATUS_OPEN
from pods.services import artifacts as artifact_svc
from pods.services.activity import activity_for_pod
from pods.services.availability import freshness_line, summary_rows
from pods.services.meeting_presentation import meetings_section
from pods.services.membership import can_manage
from pods.services.people import (
    display_name,
    is_slack_eligible,
    offset_range_label,
    slack_eligibility_map,
    timezone_label,
)
from pods.services.pod_suggestions import add_meeting_actions, suggestions_context
from pods.services.pod_summary import fit_line, meta_line, public_pod, viewer_state
from pods.services.slack import profile_url, suggested_channel_name
from pods.services.suggestions import load_member_availability, weekly_overlap_hours


@dataclass
class PageData:
    pod: object
    viewer: object
    now: object
    members: list = field(default_factory=list)
    availability: dict = field(default_factory=dict)
    member_avail: list = field(default_factory=list)
    viewer_availability: object = None
    viewer_zone: str = "UTC"
    is_member: bool = False
    can_join: bool = False
    state: object = None
    manage: bool = False
    show_private: bool = False
    viewer_slack_ok: bool = False
    eligibility: dict = field(default_factory=dict)
    suggestions: object = None
    meetings: object = None
    notes: object = None
    fit_hours: object = None
    profile: object = None


def _viewer_zone(viewer_availability):
    if viewer_availability is not None and viewer_availability.timezone_name:
        return viewer_availability.timezone_name
    return "UTC"


def _member_rows(data):
    rows = []
    for user in data.members:
        member = data.availability[user.pk]
        slack_url = ""
        if data.is_member and data.viewer_slack_ok and user.pk != data.viewer.pk and data.eligibility.get(user.pk):
            slack_url = profile_url(user)
        rows.append(
            {
                "user_id": user.pk,
                "name": display_name(user),
                "timezone_label": timezone_label(member.timezone_name),
                "has_availability": member.has_windows,
                "is_owner": data.pod.owner_id == user.pk,
                "is_viewer": user.pk == data.viewer.pk,
                "slack_url": slack_url,
            }
        )
    return rows


def _meetings_and_notes(data, artifacts):
    meetings = meetings_section(
        data.pod,
        data.viewer,
        member_avail=data.member_avail,
        viewer_zone=data.viewer_zone,
        is_member=data.is_member,
        can_manage=data.manage,
        now=data.now,
        artifacts=artifacts,
    )
    notes = None
    if artifacts is not None:
        notes = artifact_svc.member_section(
            data.pod,
            data.viewer,
            artifacts,
            numbers=meetings["state"].numbers,
            is_member=data.is_member,
            viewer_zone=data.viewer_zone,
        )
    return meetings, notes


def _load_private_sections(data):
    if not data.show_private:
        return
    data.suggestions = suggestions_context(data.pod, data.member_avail, data.viewer_zone, now=data.now)
    artifacts = None
    if artifact_svc.artifacts_enabled():
        artifacts = artifact_svc.pod_artifacts(data.pod)
    data.meetings, data.notes = _meetings_and_notes(data, artifacts)
    add_meeting_actions(
        data.pod,
        data.suggestions,
        data.meetings["state"],
        data.member_avail,
        data.viewer_zone,
        is_member=data.is_member,
        count=len(data.members),
        now=data.now,
        proposal_unconfirmable=data.meetings["proposal_unconfirmable"],
    )


def _slack_context(data):
    if not data.is_member:
        return None
    others_below_main = False
    for user in data.members:
        if user.pk != data.viewer.pk and not data.eligibility.get(user.pk):
            others_below_main = True
    return {
        "channel_url": data.pod.slack_channel_url,
        "viewer_eligible": data.viewer_slack_ok,
        "show_callout": not data.pod.slack_channel_url and len(data.members) >= 2 and data.viewer_slack_ok,
        "show_gate": not data.pod.slack_channel_url and not data.viewer_slack_ok,
        "others_below_main": others_below_main,
        "viewer_in_workspace": bool(getattr(data.viewer, "slack_member", False)),
        "suggested_name": suggested_channel_name(data.pod),
    }


def _load_members(data):
    memberships = data.pod.memberships.select_related("user").order_by("joined_at", "pk")
    data.members = [membership.user for membership in memberships]
    data.is_member = any(user.pk == data.viewer.pk for user in data.members)
    users = {data.viewer.pk: data.viewer}
    for user in data.members:
        users.setdefault(user.pk, user)
    data.availability = load_member_availability(users.values())
    data.member_avail = [data.availability[user.pk] for user in data.members]
    data.viewer_availability = data.availability.get(data.viewer.pk)
    data.viewer_zone = _viewer_zone(data.viewer_availability)


def _load_permissions(data):
    activity = activity_for_pod(data.pod)
    data.can_join = activity.is_participant(data.viewer) and not data.is_member and data.pod.status == POD_STATUS_OPEN
    data.state = viewer_state(data.pod, data.viewer, is_member=data.is_member, can_join=data.can_join)
    data.manage = can_manage(data.pod, data.viewer)
    data.show_private = data.is_member or bool(data.viewer.is_staff)
    data.viewer_slack_ok = is_slack_eligible(data.viewer)
    if data.is_member:
        data.eligibility = slack_eligibility_map(data.members)
    if not data.is_member:
        data.fit_hours = weekly_overlap_hours(data.viewer_availability, data.member_avail, now=data.now)
    data.profile = getattr(data.viewer, "_pods_profile", None)


def _private_copy(data):
    owner_name = ""
    if data.pod.owner_id:
        owner_name = display_name(data.pod.owner)
    call_link_value = ""
    if data.show_private:
        call_link_value = data.pod.meeting_url
    return owner_name, call_link_value


def _availability_copy(data):
    summary = []
    freshness = ""
    if data.is_member:
        summary = summary_rows(data.profile)
        freshness = freshness_line(data.profile, data.now)
    timezone_name = ""
    if data.viewer_availability:
        timezone_name = timezone_label(data.viewer_availability.timezone_name)
    return summary, freshness, timezone_name


def _page_context(data):
    owner_name, call_link_value = _private_copy(data)
    summary, freshness, availability_timezone = _availability_copy(data)
    context = {
        "pod": public_pod(data.pod),
        "count": len(data.members),
        "meta": meta_line(data.pod, len(data.members)),
        "owner_name": owner_name,
        "state": data.state,
        "is_member": data.is_member,
        "can_manage": data.manage,
        "can_join": data.can_join,
        "show_private": data.show_private,
        "suggestions": data.suggestions,
        "meetings": data.meetings,
        "notes": data.notes,
        "call_link_value": call_link_value,
        "viewer_zone": data.viewer_zone,
    }
    context.update(_secondary_context(data, summary, freshness, availability_timezone))
    return context


def _secondary_context(data, summary, freshness, availability_timezone):
    zones = [member.timezone_name for member in data.member_avail]
    return {
        "member_rows": _member_rows(data),
        "zones": offset_range_label(zones, data.now),
        "fit_line": fit_line(data.fit_hours),
        "slack": _slack_context(data),
        "viewer_has_availability": bool(data.viewer_availability and data.viewer_availability.has_windows),
        "availability_summary": summary,
        "availability_timezone": availability_timezone,
        "freshness_line": freshness,
    }


def build_pod_page(pod, viewer, *, now=None):
    if now is None:
        now = timezone.now()
    data = PageData(pod=pod, viewer=viewer, now=now)
    _load_members(data)
    _load_permissions(data)
    _load_private_sections(data)
    return _page_context(data)
