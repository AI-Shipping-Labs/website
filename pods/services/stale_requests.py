"""Stale pod join requests and the daily staff Slack alert (issue #1927).

A request is stale when it is ``pending``, its pod is not archived, and it
was created more than ``PODS_STALE_REQUEST_DAYS`` days ago. Waitlisted
requests never count: nobody can approve them until a seat opens. Studio's
``/studio/pods/`` ``stale`` badge uses the same rule (``is_stale``).

The daily job posts at most one message per run to the staff alert channel
(``STAFF_COMMENT_NOTIFY_CHANNEL_ID``, falling back to
``STAFF_SIGNUP_NOTIFY_CHANNEL_ID``), and only when at least one stale
request has not been announced yet (``stale_alerted_at`` is empty). After
Slack accepts the post every newly stale request is marked; on any failure
nothing is marked, so the next day's run retries. The job never raises.

Since issue #1935 the same message also carries stuck pod meeting
proposals (rule in ``pods.services.stuck_proposals``) as their own section.
Each stuck proposal is announced once (``PodMeeting.stuck_alerted_at`` on
its head meeting, set only after Slack accepts the post and cleared when
the proposal is moved). ``PODS_STALE_REQUEST_ALERT_ENABLED`` gates the
requests section and ``PODS_STUCK_PROPOSAL_ALERT_ENABLED`` the proposals
section; the job posts when either section has something new.
"""

import logging
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from community.slack_config import slack_api_enabled
from integrations.config import site_base_url
from notifications.services.staff_slack import (
    escape_slack_text,
    message_payload,
    mrkdwn,
    post_staff_slack_message,
    staff_alert_channel_id,
)
from pods.models import (
    MEETING_STATUS_PROPOSED,
    POD_STATUS_ARCHIVED,
    REQUEST_STATUS_PENDING,
    PodJoinRequest,
    PodMeeting,
)
from pods.services import config as pods_config
from pods.services.meetings import format_in_zone
from pods.services.people import display_name, humanize_age
from pods.services.stuck_proposals import stuck_proposals

logger = logging.getLogger(__name__)

MAX_LINES = 10
# Slack rejects a section whose text is longer than 3000 characters.
MAX_SECTION_CHARS = 3000
FALLBACK_TEXT = 'Stale pod requests need attention'
PROPOSALS_FALLBACK_TEXT = 'Stuck pod proposals need attention'
BOTH_FALLBACK_TEXT = 'Pod requests and proposals need attention'
BUTTON_LABEL = 'Open pods in Studio'


def is_stale(created_at, now, stale_days):
    """``True`` when a pending request created at ``created_at`` is stale."""
    return created_at is not None and now - created_at > timedelta(days=stale_days)


def stale_requests(*, now=None, stale_days=None):
    """Stale pending requests on non-archived pods, oldest first."""
    now = now or timezone.now()
    stale_days = pods_config.stale_request_days() if stale_days is None else stale_days
    return (
        PodJoinRequest.objects.filter(
            status=REQUEST_STATUS_PENDING,
            created_at__lt=now - timedelta(days=stale_days),
        )
        .exclude(pod__status=POD_STATUS_ARCHIVED)
        .select_related('pod__cohort__course', 'pod__owner', 'user')
        .order_by('created_at', 'pk')
    )


def _plural(count, singular, plural):
    return singular if count == 1 else plural


def _requests_label(count):
    """``1 pod request`` / ``3 pod requests``."""
    return f'{count} pod {_plural(count, "request", "requests")}'


def _proposals_label(count):
    """``1 pod proposal`` / ``3 pod proposals``."""
    return f'{count} pod {_plural(count, "proposal", "proposals")}'


def headline(count, stale_days):
    return (
        f'{count} pod {_plural(count, "request", "requests")} '
        f'{_plural(count, "has", "have")} waited more than '
        f'{stale_days} {_plural(stale_days, "day", "days")}'
    )


def proposals_headline(count, hours):
    """``1 pod proposal has had no answer for 48 hours``."""
    return (
        f'{_proposals_label(count)} {_plural(count, "has", "have")} had no answer for '
        f'{hours} {_plural(hours, "hour", "hours")}'
    )


def earlier_line(count):
    return (
        f'{count} earlier {_plural(count, "request is", "requests are")} still waiting.'
    )


def earlier_proposals_line(count):
    return (
        f'{count} earlier {_plural(count, "proposal is", "proposals are")} still waiting.'
    )


def _pod_link(pod, base):
    """``<url|Pod> (Course, Cohort)`` with every member-written part escaped."""
    pod_url = f'{base}{reverse("studio_pod_detail", kwargs={"pod_id": pod.pk})}'
    cohort = pod.cohort
    activity = ''
    if cohort is not None:
        activity = (
            f' ({escape_slack_text(cohort.course.title)}, '
            f'{escape_slack_text(cohort.name)})'
        )
    return f'<{pod_url}|{escape_slack_text(pod.name)}>{activity}'


def request_line(join_request, base, now):
    """``<url|Pod> (Course, Cohort) - Name (email) asked 6 days ago - owner Anna K.``"""
    pod = join_request.pod
    owner = escape_slack_text(display_name(pod.owner)) if pod.owner_id else 'no owner'
    requester = join_request.user
    return (
        f'{_pod_link(pod, base)} - '
        f'{escape_slack_text(display_name(requester))} '
        f'({escape_slack_text(requester.email)}) '
        f'asked {humanize_age(join_request.created_at, now)} - owner {owner}'
    )


def _last_sign_in(user, now):
    if user.last_login is None:
        return 'never signed in'
    return f'last sign-in {humanize_age(user.last_login, now)}'


def proposal_line(wait, base, now):
    """One stuck proposal.

    ``<url|Pod> (Course, Cohort) - Tue Oct 20, 18:00 UTC, proposed by Anna K.
    3 days ago - waiting on Mike K. (mike@example.com, last sign-in 12 days
    ago)``. A moved proposal reads ``moved by`` (its clock restarted then).
    Members who answered ``Can't make it`` follow as ``- can't make it:
    Raj P.``, which is the whole tail once nobody is left to answer.
    """
    head = wait.head
    age = humanize_age(wait.waiting_since, now)
    if head.moved_at:
        origin = f'moved by {escape_slack_text(display_name(head.moved_by))} {age}'
    else:
        origin = f'proposed by {escape_slack_text(display_name(head.proposed_by))} {age}'
    parts = [_pod_link(head.pod, base), f'{format_in_zone(head.starts_at, "UTC")}, {origin}']
    if wait.waiting:
        parts.append('waiting on ' + '; '.join(
            f'{escape_slack_text(display_name(user))} '
            f'({escape_slack_text(user.email)}, {_last_sign_in(user, now)})'
            for user in wait.waiting
        ))
    if wait.cant:
        parts.append("can't make it: " + ', '.join(escape_slack_text(display_name(u)) for u in wait.cant))
    return ' - '.join(parts)


def _line_sections(lines):
    """Section blocks holding ``lines``, each under Slack's text limit."""
    chunks = []
    current = ''
    for line in lines:
        candidate = f'{current}\n{line}' if current else line
        if current and len(candidate) > MAX_SECTION_CHARS:
            chunks.append(current)
            candidate = line
        current = candidate[:MAX_SECTION_CHARS]
    if current:
        chunks.append(current)
    return [{'type': 'section', 'text': mrkdwn(chunk)} for chunk in chunks]


def _capped(lines):
    extra = len(lines) - MAX_LINES
    lines = lines[:MAX_LINES]
    if extra > 0:
        lines.append(f'and {extra} more')
    return lines


def _section(title, lines, earlier):
    blocks = [{'type': 'section', 'text': mrkdwn(f'*{title}*')}, *_line_sections(_capped(lines))]
    if earlier:
        blocks.append({'type': 'context', 'elements': [mrkdwn(earlier)]})
    return blocks


def build_pods_alert_message(
    new_requests, earlier_requests, new_proposals, earlier_proposals, *,
    stale_days, stuck_hours, now=None,
):
    """``chat.postMessage`` payload minus ``channel``; ``None`` when nothing is new.

    The requests section comes first, then the proposals section; each is
    present only when it has something new.
    """
    new_requests = list(new_requests)
    new_proposals = list(new_proposals)
    if not new_requests and not new_proposals:
        return None
    now = now or timezone.now()
    base = site_base_url().rstrip('/')
    blocks = []
    if new_requests:
        blocks += _section(
            headline(len(new_requests), stale_days),
            [request_line(r, base, now) for r in new_requests],
            earlier_line(earlier_requests) if earlier_requests else '',
        )
    if new_proposals:
        blocks += _section(
            proposals_headline(len(new_proposals), stuck_hours),
            [proposal_line(w, base, now) for w in new_proposals],
            earlier_proposals_line(earlier_proposals) if earlier_proposals else '',
        )
    blocks.append({
        'type': 'actions',
        'elements': [{
            'type': 'button',
            'text': {'type': 'plain_text', 'text': BUTTON_LABEL},
            'url': f'{base}{reverse("studio_pod_list")}',
        }],
    })
    if new_requests and new_proposals:
        text = BOTH_FALLBACK_TEXT
    elif new_proposals:
        text = PROPOSALS_FALLBACK_TEXT
    else:
        text = FALLBACK_TEXT
    return message_payload(text, blocks)


def build_stale_request_message(new_requests, earlier_count, *, stale_days, now=None):
    """The requests-only message (issue #1927); ``None`` when nothing is new."""
    return build_pods_alert_message(
        new_requests, earlier_count, [], 0, stale_days=stale_days, stuck_hours=0, now=now,
    )


def send_stale_request_alert(*, now=None):
    """Post the daily pods staff alert; never raises.

    Returns the number of requests and proposals newly announced (``0``
    when nothing was posted).
    """
    try:
        return _send_stale_request_alert(now=now)
    except Exception:
        logger.exception('Stale pod request Slack alert failed')
        return 0


def _new_stale_requests(now):
    """``(new, earlier_count, stale_days)`` for the requests section."""
    stale_days = pods_config.stale_request_days()
    stale = list(stale_requests(now=now, stale_days=stale_days))
    new_requests = [r for r in stale if r.stale_alerted_at is None]
    if not new_requests:
        logger.info('Stale pod request alert: nothing new (%s still waiting)', len(stale))
    return new_requests, len(stale) - len(new_requests), stale_days


def _new_stuck_proposals(now):
    """``(new, earlier_count, hours)`` for the proposals section."""
    hours = pods_config.stuck_proposal_hours()
    stuck = stuck_proposals(now=now, hours=hours)
    new_proposals = [w for w in stuck if w.head.stuck_alerted_at is None]
    if not new_proposals:
        logger.info('Stuck pod proposal alert: nothing new (%s still stuck)', len(stuck))
    return new_proposals, len(stuck) - len(new_proposals), hours


def _subject(new_requests, new_proposals):
    """``(alert name, counts)`` for log lines, e.g. ``1 pod request``."""
    if new_requests and new_proposals:
        return (
            'Pod request and proposal',
            f'{_requests_label(len(new_requests))} and {_proposals_label(len(new_proposals))}',
        )
    if new_proposals:
        return 'Stuck pod proposal', _proposals_label(len(new_proposals))
    return 'Stale pod request', _requests_label(len(new_requests))


def _mark_proposals_announced(new_proposals, now):
    """Set ``stuck_alerted_at`` on each head unless it moved since it was read."""
    for wait in new_proposals:
        PodMeeting.objects.filter(
            pk=wait.head.pk,
            status=MEETING_STATUS_PROPOSED,
            moved_at=wait.head.moved_at,
            stuck_alerted_at__isnull=True,
        ).update(stuck_alerted_at=now)


def _send_stale_request_alert(*, now=None):
    requests_on = pods_config.stale_request_alert_enabled()
    proposals_on = pods_config.stuck_proposal_alert_enabled()
    if not requests_on:
        logger.info('Stale pod request alert skipped: PODS_STALE_REQUEST_ALERT_ENABLED is off')
    if not proposals_on:
        logger.info('Stuck pod proposal alert skipped: PODS_STUCK_PROPOSAL_ALERT_ENABLED is off')
    if not requests_on and not proposals_on:
        return 0
    now = now or timezone.now()
    new_requests, earlier_requests, stale_days = [], 0, pods_config.stale_request_days()
    if requests_on:
        new_requests, earlier_requests, stale_days = _new_stale_requests(now)
    new_proposals, earlier_proposals, hours = [], 0, pods_config.stuck_proposal_hours()
    if proposals_on:
        new_proposals, earlier_proposals, hours = _new_stuck_proposals(now)
    if not new_requests and not new_proposals:
        return 0
    name, subject = _subject(new_requests, new_proposals)
    if not slack_api_enabled():
        logger.info('%s alert skipped for %s: Slack API disabled', name, subject)
        return 0
    channel_id = staff_alert_channel_id()
    if not channel_id:
        logger.info(
            '%s alert skipped for %s: no '
            'STAFF_COMMENT_NOTIFY_CHANNEL_ID or STAFF_SIGNUP_NOTIFY_CHANNEL_ID',
            name, subject,
        )
        return 0
    message = build_pods_alert_message(
        new_requests, earlier_requests, new_proposals, earlier_proposals,
        stale_days=stale_days, stuck_hours=hours, now=now,
    )
    result = post_staff_slack_message(channel_id, message)
    if result.exception is not None:
        logger.error(
            '%s Slack alert failed for %s (channel=%s)',
            name, subject, channel_id, exc_info=result.exception,
        )
        return 0
    if not result.ok:
        logger.error(
            '%s Slack alert rejected for %s (channel=%s): %s',
            name, subject, channel_id, result.error,
        )
        return 0
    if new_requests:
        PodJoinRequest.objects.filter(pk__in=[r.pk for r in new_requests]).update(stale_alerted_at=now)
    _mark_proposals_announced(new_proposals, now)
    logger.info('Posted %s Slack alert for %s to channel=%s', name.lower(), subject, channel_id)
    return len(new_requests) + len(new_proposals)
