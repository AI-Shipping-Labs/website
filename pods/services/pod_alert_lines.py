"""Slack-safe text and section builders for study-group alerts."""

from django.urls import reverse

from notifications.services.staff_slack import escape_slack_text, mrkdwn
from pods.services.meetings import format_in_zone
from pods.services.people import display_name, humanize_age

MAX_LINES = 10
MAX_SECTION_CHARS = 3000
UNCONFIRMABLE_TEXT = "can't be confirmed, needs another time"
ELLIPSIS = "..."


def plural(count, singular, plural):
    if count == 1:
        return singular
    return plural


def requests_label(count):
    return f"{count} pod {plural(count, 'request', 'requests')}"


def proposals_label(count):
    return f"{count} pod {plural(count, 'proposal', 'proposals')}"


def headline(count, stale_days):
    return (
        f"{count} pod {plural(count, 'request', 'requests')} "
        f"{plural(count, 'has', 'have')} waited more than "
        f"{stale_days} {plural(stale_days, 'day', 'days')}"
    )


def proposals_headline(count, hours):
    return (
        f"{proposals_label(count)} {plural(count, 'has', 'have')} waited more than "
        f"{hours} {plural(hours, 'hour', 'hours')} or half the time to the meeting"
    )


def earlier_line(count):
    return f"{count} earlier {plural(count, 'request is', 'requests are')} still waiting."


def earlier_proposals_line(count):
    return f"{count} earlier {plural(count, 'proposal is', 'proposals are')} still waiting."


def pod_link(pod, base):
    pod_url = f"{base}{reverse('studio_pod_detail', kwargs={'pod_id': pod.pk})}"
    activity = ""
    if pod.sprint_id:
        activity = f" ({escape_slack_text(pod.sprint.name)})"
    elif pod.cohort is not None:
        activity = f" ({escape_slack_text(pod.cohort.course.title)}, {escape_slack_text(pod.cohort.name)})"
    return f"<{pod_url}|{escape_slack_text(pod.name)}>{activity}"


def request_line(join_request, base, now):
    owner = "no owner"
    if join_request.pod.owner_id:
        owner = escape_slack_text(display_name(join_request.pod.owner))
    requester = join_request.user
    return (
        f"{pod_link(join_request.pod, base)} - "
        f"{escape_slack_text(display_name(requester))} "
        f"({escape_slack_text(requester.email)}) "
        f"asked {humanize_age(join_request.created_at, now)} - owner {owner}"
    )


def _last_sign_in(user, now):
    if user.last_login is None:
        return "never signed in"
    return f"last sign-in {humanize_age(user.last_login, now)}"


def _proposal_parts(wait, base, now):
    head = wait.head
    age = humanize_age(wait.waiting_since, now)
    if head.moved_at:
        origin = f"moved by {escape_slack_text(display_name(head.moved_by))} {age}"
    else:
        origin = f"proposed by {escape_slack_text(display_name(head.proposed_by))} {age}"
    lead = [pod_link(head.pod, base), f"{format_in_zone(head.starts_at, 'UTC')}, {origin}"]
    waiting = []
    for user in wait.waiting:
        waiting.append(
            f"{escape_slack_text(display_name(user))} ({escape_slack_text(user.email)}, {_last_sign_in(user, now)})"
        )
    cant = [escape_slack_text(display_name(user)) for user in wait.cant]
    return lead, waiting, cant


def _shortened(entries, shown):
    if shown >= len(entries):
        return entries
    return [*entries[:shown], f"and {len(entries) - shown} more"]


def _proposal_text(wait, lead, waiting, cant, waiting_shown, cant_shown):
    parts = list(lead)
    if waiting:
        parts.append("waiting on " + "; ".join(_shortened(waiting, waiting_shown)))
    if cant:
        parts.append("can't make it: " + ", ".join(_shortened(cant, cant_shown)))
    if wait.unconfirmable:
        parts.append(UNCONFIRMABLE_TEXT)
    return " - ".join(parts)


def proposal_line(wait, base, now):
    """Build a whole-entity-truncated line for one stuck proposal."""
    lead, waiting, cant = _proposal_parts(wait, base, now)
    waiting_shown = len(waiting)
    cant_shown = len(cant)
    line = _proposal_text(wait, lead, waiting, cant, waiting_shown, cant_shown)
    while len(line) > MAX_SECTION_CHARS and waiting_shown > 1:
        waiting_shown -= 1
        line = _proposal_text(wait, lead, waiting, cant, waiting_shown, cant_shown)
    while len(line) > MAX_SECTION_CHARS and cant_shown > 1:
        cant_shown -= 1
        line = _proposal_text(wait, lead, waiting, cant, waiting_shown, cant_shown)
    return line


def _drop_partial_entity(text):
    amp = text.rfind("&")
    if amp != -1 and ";" not in text[amp:]:
        return text[:amp]
    return text


def _cut_before(text, limit):
    space = text.rfind(" ", 0, limit + 1)
    if space > 0:
        return text[:space]
    return text[:limit]


def _cut_line(line, limit=MAX_SECTION_CHARS):
    if len(line) <= limit:
        return line
    budget = limit - len(ELLIPSIS)
    kept = _drop_partial_entity(_cut_before(line, budget))
    link_at = kept.rfind("<")
    if link_at > kept.rfind(">"):
        label_at = kept.find("|", link_at) + 1
        if label_at:
            kept = _drop_partial_entity(_cut_before(kept, budget - 1))
        if label_at and len(kept) > label_at:
            return f"{kept.rstrip()}>{ELLIPSIS}"
        kept = kept[:link_at]
    return f"{kept.rstrip()}{ELLIPSIS}"


def _line_sections(lines):
    chunks = []
    current = ""
    for line in lines:
        line = _cut_line(line)
        candidate = line
        if current:
            candidate = f"{current}\n{line}"
        if current and len(candidate) > MAX_SECTION_CHARS:
            chunks.append(current)
            candidate = line
        current = candidate
    if current:
        chunks.append(current)
    return [{"type": "section", "text": mrkdwn(chunk)} for chunk in chunks]


def _capped(lines):
    extra = len(lines) - MAX_LINES
    lines = lines[:MAX_LINES]
    if extra > 0:
        lines.append(f"and {extra} more")
    return lines


def section(title, lines, earlier):
    blocks = [{"type": "section", "text": mrkdwn(f"*{title}*")}, *_line_sections(_capped(lines))]
    if earlier:
        blocks.append({"type": "context", "elements": [mrkdwn(earlier)]})
    return blocks
