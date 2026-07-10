"""Shared campaign recipient visibility helpers."""

from email_app.models import EmailLog

SENT_RECIPIENT_STATUSES = {"sending", "sent"}


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
            }
            for user in users
        ]

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
            }
            for row in rows
        ],
    }
