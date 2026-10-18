"""Slack payload assembly for study-group alerts."""

from django.urls import reverse
from django.utils import timezone

from integrations.config import site_base_url
from notifications.services.staff_slack import message_payload
from pods.services.pod_alert_lines import (
    earlier_line,
    earlier_proposals_line,
    headline,
    proposal_line,
    proposals_headline,
    request_line,
    section,
)

FALLBACK_TEXT = "Stale pod requests need attention"
PROPOSALS_FALLBACK_TEXT = "Stuck pod proposals need attention"
BOTH_FALLBACK_TEXT = "Pod requests and proposals need attention"
BUTTON_LABEL = "Open pods in Studio"


def _fallback_text(new_requests, new_proposals):
    if new_requests and new_proposals:
        return BOTH_FALLBACK_TEXT
    if new_proposals:
        return PROPOSALS_FALLBACK_TEXT
    return FALLBACK_TEXT


def _request_blocks(new_requests, earlier_requests, stale_days, base, now):
    lines = [request_line(request, base, now) for request in new_requests]
    earlier = ""
    if earlier_requests:
        earlier = earlier_line(earlier_requests)
    return section(headline(len(new_requests), stale_days), lines, earlier)


def _proposal_blocks(new_proposals, earlier_proposals, stuck_hours, base, now):
    lines = [proposal_line(wait, base, now) for wait in new_proposals]
    earlier = ""
    if earlier_proposals:
        earlier = earlier_proposals_line(earlier_proposals)
    return section(proposals_headline(len(new_proposals), stuck_hours), lines, earlier)


def _studio_action(base):
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": BUTTON_LABEL},
                "url": f"{base}{reverse('studio_pod_list')}",
            }
        ],
    }


def build_pods_alert_message(
    new_requests,
    earlier_requests,
    new_proposals,
    earlier_proposals,
    *,
    stale_days,
    stuck_hours,
    now=None,
):
    new_requests = list(new_requests)
    new_proposals = list(new_proposals)
    if not new_requests and not new_proposals:
        return None
    now = now or timezone.now()
    base = site_base_url().rstrip("/")
    blocks = []
    if new_requests:
        blocks += _request_blocks(new_requests, earlier_requests, stale_days, base, now)
    if new_proposals:
        blocks += _proposal_blocks(new_proposals, earlier_proposals, stuck_hours, base, now)
    blocks.append(_studio_action(base))
    return message_payload(_fallback_text(new_requests, new_proposals), blocks)


def build_stale_request_message(new_requests, earlier_count, *, stale_days, now=None):
    return build_pods_alert_message(
        new_requests,
        earlier_count,
        [],
        0,
        stale_days=stale_days,
        stuck_hours=0,
        now=now,
    )
