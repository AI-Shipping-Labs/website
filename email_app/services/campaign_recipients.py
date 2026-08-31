"""Shared campaign recipient visibility helpers."""

from email_app.models import CampaignDelivery, EmailLog

SENT_RECIPIENT_STATUSES = {"sending", "needs_attention", "sent"}


def campaign_recipient_mode(campaign):
    """Return ``preview`` for draft campaigns, otherwise actual sent logs."""
    if campaign.status in SENT_RECIPIENT_STATUSES:
        return "sent"
    return "preview"


def _recipient_email_for_log(log):
    if log.user_id:
        return log.user.email
    return log.recipient_email


def disposition_for_log(log):
    """Summarize recipient deliverability from an EmailLog row."""
    if log.complained_at:
        return "complained"
    if log.bounced_at:
        return "bounced"
    return "delivered"


def build_campaign_recipient_rows(campaign):
    """Return recipient rows for Studio/API from one source of truth."""
    mode = campaign_recipient_mode(campaign)
    if mode == "preview":
        users = (
            campaign.get_eligible_recipients()
            .select_related("tier")
            .order_by("email")
        )
        return [
            {
                "mode": mode,
                "user": user,
                "user_id": user.pk,
                "user_email": user.email,
                "recipient_email": user.email,
                "sent_at": None,
                "opens": 0,
                "clicks": 0,
                "disposition": "preview",
                "bounce_type": "",
                "bounce_subtype": "",
                "bounce_diagnostic": "",
                "email_log_id": None,
                "delivery_id": None,
                "delivery_state": "preview",
                "delivery_state_label": "Preview",
                "attempt_count": 0,
                "updated_at": None,
                "claimed_at": None,
                "claim_expires_at": None,
                "completed_at": None,
                "resolution": "",
                "resolved_at": None,
                "resolved_by_id": None,
                "resolved_by_email": "",
                "ses_message_id": "",
                "last_error": "",
            }
            for user in users
        ]

    deliveries = list(
        CampaignDelivery.objects.filter(campaign=campaign)
        .select_related("user", "email_log", "resolved_by")
        .order_by("recipient_email", "pk")
    )
    if deliveries:
        rows = []
        for delivery in deliveries:
            log = delivery.email_log
            rows.append({
                "mode": mode,
                "user": delivery.user,
                "user_id": delivery.user_id,
                "user_email": delivery.user.email if delivery.user_id else "",
                "recipient_email": delivery.recipient_email,
                "sent_at": log.sent_at if log else None,
                "opens": log.opens if log else 0,
                "clicks": log.clicks if log else 0,
                "disposition": disposition_for_log(log) if log else delivery.state,
                "bounce_type": log.bounce_type if log else "",
                "bounce_subtype": log.bounce_subtype if log else "",
                "bounce_diagnostic": log.bounce_diagnostic if log else "",
                "email_log_id": log.pk if log else None,
                "complained_at": log.complained_at if log else None,
                "bounced_at": log.bounced_at if log else None,
                "delivery_id": delivery.pk,
                "delivery_state": delivery.state,
                "delivery_state_label": delivery.get_state_display(),
                "attempt_count": delivery.attempt_count,
                "updated_at": delivery.updated_at,
                "claimed_at": delivery.claimed_at,
                "claim_expires_at": delivery.claim_expires_at,
                "completed_at": delivery.completed_at,
                "resolution": delivery.resolution,
                "resolved_at": delivery.resolved_at,
                "resolved_by_id": delivery.resolved_by_id,
                "resolved_by_email": (
                    delivery.resolved_by.email if delivery.resolved_by_id else ""
                ),
                "ses_message_id": delivery.ses_message_id,
                "last_error": delivery.last_error or delivery.skip_reason,
            })
        return rows

    logs = (
        EmailLog.objects
        .filter(campaign=campaign)
        .select_related("user")
        .order_by("-sent_at", "recipient_email", "user__email")
    )
    rows = []
    for log in logs:
        rows.append({
            "mode": mode,
            "user": log.user,
            "user_id": log.user_id,
            "user_email": log.user.email if log.user_id else "",
            "recipient_email": _recipient_email_for_log(log),
            "sent_at": log.sent_at,
            "opens": log.opens,
            "clicks": log.clicks,
            "disposition": disposition_for_log(log),
            "bounce_type": log.bounce_type,
            "bounce_subtype": log.bounce_subtype,
            "bounce_diagnostic": log.bounce_diagnostic,
            "email_log_id": log.pk,
            "complained_at": log.complained_at,
            "bounced_at": log.bounced_at,
            "delivery_id": None,
            "delivery_state": "sent",
            "delivery_state_label": "Sent",
            "attempt_count": 1,
            "updated_at": log.sent_at,
            "claimed_at": None,
            "claim_expires_at": None,
            "completed_at": log.sent_at,
            "resolution": "",
            "resolved_at": None,
            "resolved_by_id": None,
            "resolved_by_email": "",
            "ses_message_id": log.ses_message_id,
            "last_error": "",
        })
    return rows


def serialize_campaign_recipients(campaign):
    """Serialize campaign recipient visibility for the operator API."""
    mode = campaign_recipient_mode(campaign)
    rows = build_campaign_recipient_rows(campaign)
    return {
        "campaign_id": campaign.pk,
        "mode": mode,
        "count": len(rows),
        "recipients": [
            {
                "email": row["recipient_email"],
                "user": (
                    {
                        "id": row["user_id"],
                        "email": row["user_email"],
                    }
                    if row["user_id"]
                    else None
                ),
                "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
                "opens": row["opens"],
                "clicks": row["clicks"],
                "disposition": row["disposition"],
                "bounce_type": row["bounce_type"],
                "bounce_subtype": row["bounce_subtype"],
                "bounce_diagnostic": row["bounce_diagnostic"],
                "email_log_id": row["email_log_id"],
                "delivery_id": row["delivery_id"],
                "delivery_state": row["delivery_state"],
                "attempt_count": row["attempt_count"],
                "updated_at": (
                    row["updated_at"].isoformat() if row["updated_at"] else None
                ),
                "claimed_at": (
                    row["claimed_at"].isoformat() if row["claimed_at"] else None
                ),
                "claim_expires_at": (
                    row["claim_expires_at"].isoformat()
                    if row["claim_expires_at"]
                    else None
                ),
                "completed_at": (
                    row["completed_at"].isoformat()
                    if row["completed_at"]
                    else None
                ),
                "resolution": row["resolution"],
                "resolved_at": (
                    row["resolved_at"].isoformat() if row["resolved_at"] else None
                ),
                "resolved_by": (
                    {
                        "id": row["resolved_by_id"],
                        "email": row["resolved_by_email"],
                    }
                    if row["resolved_by_id"]
                    else None
                ),
                "ses_message_id": row["ses_message_id"],
                "last_error": row["last_error"],
            }
            for row in rows
        ],
    }
