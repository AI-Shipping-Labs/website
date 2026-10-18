"""Suggested-time presentation for study-group pages."""

from types import SimpleNamespace

from accounts.services.timezones import format_user_datetime
from pods.services import config as pods_config
from pods.services.meeting_presentation import propose_state, propose_url, same_week_slot, without_near
from pods.services.people import display_name
from pods.services.suggestions import member_strip, required_attendance, suggest_slots, weekly_overlap

SLOT_DATETIME_FORMAT = "%a, %b %d, %H:%M"


def slot_rows(slots, viewer_zone, members):
    viewer = SimpleNamespace(preferred_timezone=viewer_zone)
    rows = []
    for slot in slots:
        rows.append(
            {
                "slot": slot,
                "start_label": format_user_datetime(slot.start, viewer, fmt=SLOT_DATETIME_FORMAT),
                "start_iso": slot.start.isoformat(),
                "fit_label": slot.fit_label,
                "fit_tone": slot.fit_tone,
                "strip": member_strip(slot, members),
            }
        )
    return rows


def horizon_label(days):
    if days == 7:
        return "week"
    if days % 7 == 0:
        return f"{days // 7} weeks"
    return f"{days} days"


def _join_names(names):
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _suggestion_copy(context, considered, missing, needed):
    if missing:
        names = [display_name(member.user) for member in missing]
        verb = "haven't"
        if len(names) == 1:
            verb = "hasn't"
        context["based_on_line"] = (
            f"Based on {len(considered)} of {context['member_total']} members. "
            f"{_join_names(names)} {verb} added availability yet."
        )
    if context["slots"]:
        return
    horizon = horizon_label(pods_config.suggestion_horizon_days())
    context["no_fit_line"] = (
        f"No time fits at least {needed} of you in the next {horizon}. "
        "Ask members to add more windows, or add some yourself."
    )


def suggestions_context(pod, members, viewer_zone, *, now=None):
    considered = []
    missing = []
    for member in members:
        if member.has_windows:
            considered.append(member)
        else:
            missing.append(member)
    needed = required_attendance(len(considered))
    context = {
        "considered_count": len(considered),
        "member_total": len(members),
        "needs_more": needed is None,
        "slots": [],
        "based_on_line": "",
        "no_fit_line": "",
        "raw_slots": [],
        "overlap": None,
    }
    if needed is None:
        return context
    context["overlap"] = weekly_overlap(members, viewer_zone, now=now)
    slots = suggest_slots(members, pod.meeting_minutes, now=now, all_members=members)
    context["raw_slots"] = slots
    context["slots"] = slot_rows(slots, viewer_zone, members)
    _suggestion_copy(context, considered, missing, needed)
    return context


def add_meeting_actions(
    pod, suggestions, state, member_avail, viewer_zone, *, is_member, count, now, proposal_unconfirmable=False
):
    can_propose, blocked = propose_state(
        pod,
        state,
        is_member=is_member,
        member_count=count,
        proposal_unconfirmable=proposal_unconfirmable,
    )
    same = same_week_slot(pod, state, member_avail, now)
    if same is not None:
        rows = slot_rows([same], viewer_zone, member_avail)
        rows[0]["same_week"] = True
        suggestions["slots"] = rows + slot_rows(without_near(suggestions["raw_slots"], same), viewer_zone, member_avail)
    for row in suggestions["slots"]:
        row["propose_url"] = ""
        if can_propose:
            row["propose_url"] = propose_url(pod, row["slot"].start)
    suggestions["propose_blocked_line"] = blocked
    suggestions["custom_propose_url"] = ""
    if can_propose:
        suggestions["custom_propose_url"] = propose_url(pod)
