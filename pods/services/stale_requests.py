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
from pods.models import POD_STATUS_ARCHIVED, REQUEST_STATUS_PENDING, PodJoinRequest
from pods.services import config as pods_config
from pods.services.people import display_name, humanize_age

logger = logging.getLogger(__name__)

MAX_LINES = 10
FALLBACK_TEXT = 'Stale pod requests need attention'
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


def headline(count, stale_days):
    return (
        f'{count} pod {_plural(count, "request", "requests")} '
        f'{_plural(count, "has", "have")} waited more than '
        f'{stale_days} {_plural(stale_days, "day", "days")}'
    )


def earlier_line(count):
    return (
        f'{count} earlier {_plural(count, "request is", "requests are")} still waiting.'
    )


def request_line(join_request, base, now):
    """``<url|Pod> (Course, Cohort) - Name (email) asked 6 days ago - owner Anna K.``"""
    pod = join_request.pod
    cohort = pod.cohort
    pod_url = f'{base}{reverse("studio_pod_detail", kwargs={"pod_id": pod.pk})}'
    activity = ''
    if cohort is not None:
        activity = (
            f' ({escape_slack_text(cohort.course.title)}, '
            f'{escape_slack_text(cohort.name)})'
        )
    owner = escape_slack_text(display_name(pod.owner)) if pod.owner_id else 'no owner'
    requester = join_request.user
    return (
        f'<{pod_url}|{escape_slack_text(pod.name)}>{activity} - '
        f'{escape_slack_text(display_name(requester))} '
        f'({escape_slack_text(requester.email)}) '
        f'asked {humanize_age(join_request.created_at, now)} - owner {owner}'
    )


def build_stale_request_message(new_requests, earlier_count, *, stale_days, now=None):
    """``chat.postMessage`` payload minus ``channel``; ``None`` when nothing is new."""
    new_requests = list(new_requests)
    if not new_requests:
        return None
    now = now or timezone.now()
    base = site_base_url().rstrip('/')
    lines = [request_line(r, base, now) for r in new_requests[:MAX_LINES]]
    extra = len(new_requests) - MAX_LINES
    if extra > 0:
        lines.append(f'and {extra} more')
    blocks = [
        {'type': 'section', 'text': mrkdwn(f'*{headline(len(new_requests), stale_days)}*')},
        {'type': 'section', 'text': mrkdwn('\n'.join(lines))},
    ]
    if earlier_count:
        blocks.append({'type': 'context', 'elements': [mrkdwn(earlier_line(earlier_count))]})
    blocks.append({
        'type': 'actions',
        'elements': [{
            'type': 'button',
            'text': {'type': 'plain_text', 'text': BUTTON_LABEL},
            'url': f'{base}{reverse("studio_pod_list")}',
        }],
    })
    return message_payload(FALLBACK_TEXT, blocks)


def send_stale_request_alert(*, now=None):
    """Post the daily stale-request alert; never raises.

    Returns the number of requests newly announced (``0`` when nothing was
    posted).
    """
    try:
        return _send_stale_request_alert(now=now)
    except Exception:
        logger.exception('Stale pod request Slack alert failed')
        return 0


def _send_stale_request_alert(*, now=None):
    if not pods_config.stale_request_alert_enabled():
        logger.info('Stale pod request alert skipped: PODS_STALE_REQUEST_ALERT_ENABLED is off')
        return 0
    now = now or timezone.now()
    stale_days = pods_config.stale_request_days()
    stale = list(stale_requests(now=now, stale_days=stale_days))
    new_requests = [r for r in stale if r.stale_alerted_at is None]
    if not new_requests:
        logger.info('Stale pod request alert: nothing new (%s still waiting)', len(stale))
        return 0
    if not slack_api_enabled():
        logger.info(
            'Stale pod request alert skipped for %s: Slack API disabled',
            _requests_label(len(new_requests)),
        )
        return 0
    channel_id = staff_alert_channel_id()
    if not channel_id:
        logger.info(
            'Stale pod request alert skipped for %s: no '
            'STAFF_COMMENT_NOTIFY_CHANNEL_ID or STAFF_SIGNUP_NOTIFY_CHANNEL_ID',
            _requests_label(len(new_requests)),
        )
        return 0
    message = build_stale_request_message(
        new_requests, len(stale) - len(new_requests), stale_days=stale_days, now=now,
    )
    subject = _requests_label(len(new_requests))
    result = post_staff_slack_message(channel_id, message)
    if result.exception is not None:
        logger.error(
            'Stale pod request Slack alert failed for %s (channel=%s)',
            subject, channel_id, exc_info=result.exception,
        )
        return 0
    if not result.ok:
        logger.error(
            'Stale pod request Slack alert rejected for %s (channel=%s): %s',
            subject, channel_id, result.error,
        )
        return 0
    PodJoinRequest.objects.filter(pk__in=[r.pk for r in new_requests]).update(stale_alerted_at=now)
    logger.info(
        'Posted stale pod request Slack alert for %s to channel=%s', subject, channel_id,
    )
    return len(new_requests)
