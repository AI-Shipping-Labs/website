"""Schedule, unschedule, and dispatch newsletter campaigns.

The schedule lives on the ``EmailCampaign`` row itself (``scheduled_at`` as
the UTC dispatch instant, ``scheduled_timezone`` as the IANA display name),
so Studio and the API serialize it like any other campaign field. A
recurring django-q schedule (``campaign-scheduled-dispatch``, every minute)
calls :func:`dispatch_due_campaigns`, which claims due drafts through the
same :func:`~email_app.services.campaign_dispatch.claim_and_enqueue_campaign`
path Studio's manual send uses — the claim is the single-send guarantee, and
claiming also clears the schedule fields.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from email_app.models import EmailCampaign

SCHEDULE_DISPLAY_TZ_FALLBACK = "UTC"


@dataclass(frozen=True)
class CampaignDispatchOutcome:
    campaign_id: int
    dispatched: bool
    status: str


class CampaignScheduleValidationError(ValueError):
    """A schedule payload failed validation; ``errors`` is per-field."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


class CampaignScheduleConflict(Exception):
    """The campaign state does not allow the schedule operation."""


def parse_scheduled_time(raw, timezone_name, *, now):
    """Parse an ISO datetime + optional IANA tz into a UTC dispatch instant.

    A naive ``raw`` is interpreted in ``timezone_name`` (required in that
    case). An aware ``raw`` is used as-is; when ``timezone_name`` is also
    given it must agree with the datetime's offset so the stored display
    timezone never contradicts the instant. Returns ``(instant, tz_name)``
    where ``tz_name`` is the caller's timezone name or ``None``.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise CampaignScheduleValidationError({
            "scheduled_at": "Must be an ISO 8601 datetime string.",
        })
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise CampaignScheduleValidationError({
            "scheduled_at": f"Could not parse {raw!r} as an ISO 8601 datetime.",
        })

    zone = None
    if timezone_name:
        if not isinstance(timezone_name, str):
            raise CampaignScheduleValidationError({
                "scheduled_timezone": "Must be an IANA timezone name string.",
            })
        try:
            zone = ZoneInfo(timezone_name.strip())
        except Exception:
            raise CampaignScheduleValidationError({
                "scheduled_timezone": (
                    f"Unknown IANA timezone {timezone_name!r}."
                ),
            })

    if parsed.tzinfo is None:
        if zone is None:
            raise CampaignScheduleValidationError({
                "scheduled_timezone": (
                    "Required when scheduled_at carries no UTC offset "
                    "(e.g. \"Europe/Berlin\")."
                ),
            })
        parsed = parsed.replace(tzinfo=zone)
    elif zone is not None:
        local_offset = parsed.replace(tzinfo=zone).utcoffset()
        if parsed.utcoffset() != local_offset:
            raise CampaignScheduleValidationError({
                "scheduled_timezone": (
                    f"{timezone_name} does not match the UTC offset in "
                    "scheduled_at."
                ),
            })

    if parsed <= now:
        raise CampaignScheduleValidationError({
            "scheduled_at": "Must be in the future.",
        })

    tz_name = timezone_name.strip() if timezone_name else None
    return parsed.astimezone(dt_timezone.utc), tz_name


def schedule_campaign(campaign_id, *, scheduled_at, scheduled_timezone, now=None):
    """Set or replace the send schedule on a draft campaign."""
    now = now or timezone.now()
    instant, tz_name = parse_scheduled_time(
        scheduled_at, scheduled_timezone, now=now,
    )
    with transaction.atomic():
        campaign = (
            EmailCampaign.objects.select_for_update()
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            raise EmailCampaign.DoesNotExist(
                f"Campaign {campaign_id} not found"
            )
        if campaign.status != "draft":
            raise CampaignScheduleConflict(
                f"Campaign is {campaign.status}; only draft campaigns "
                "can be scheduled."
            )
        campaign.scheduled_at = instant
        campaign.scheduled_timezone = tz_name
        campaign.save(update_fields=["scheduled_at", "scheduled_timezone"])
    return campaign


def unschedule_campaign(campaign_id):
    """Clear the schedule on a draft campaign that has not been claimed."""
    with transaction.atomic():
        campaign = (
            EmailCampaign.objects.select_for_update()
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            raise EmailCampaign.DoesNotExist(
                f"Campaign {campaign_id} not found"
            )
        if campaign.scheduled_at is None and not campaign.scheduled_timezone:
            raise CampaignScheduleConflict("Campaign is not scheduled.")
        if campaign.status != "draft":
            raise CampaignScheduleConflict(
                f"Campaign is {campaign.status}; its send was already "
                "claimed, so there is nothing to unschedule."
            )
        campaign.scheduled_at = None
        campaign.scheduled_timezone = None
        campaign.save(update_fields=["scheduled_at", "scheduled_timezone"])
    return campaign


def dispatch_due_campaigns(*, now=None):
    """Claim every draft whose scheduled time has arrived.

    Uses the Studio send claim, so a campaign is dispatched exactly once and
    claiming clears the schedule fields. Campaigns whose status changed
    between the due scan and the claim (manual send, archive edit) are
    reported as skipped, never double-sent.
    """
    from email_app.services.campaign_dispatch import claim_and_enqueue_campaign

    now = now or timezone.now()
    due_ids = list(
        EmailCampaign.objects.filter(
            status="draft",
            scheduled_at__isnull=False,
            scheduled_at__lte=now,
        )
        .order_by("scheduled_at", "pk")
        .values_list("pk", flat=True)
    )
    dispatched = []
    skipped = []
    for campaign_id in due_ids:
        result = claim_and_enqueue_campaign(
            campaign_id,
            source="Scheduled campaign dispatch",
        )
        outcome = CampaignDispatchOutcome(
            campaign_id=campaign_id,
            dispatched=result.claimed,
            status=result.campaign.status,
        )
        if result.claimed:
            dispatched.append(outcome)
        else:
            skipped.append(outcome)
    return {
        "due": len(due_ids),
        "dispatched": [outcome.campaign_id for outcome in dispatched],
        "skipped": [
            {"campaign_id": outcome.campaign_id, "status": outcome.status}
            for outcome in skipped
        ],
    }


def localized_schedule_display(campaign):
    """Wall-clock string for the schedule in its own timezone, or None."""
    if not campaign.scheduled_at:
        return None
    zone = SCHEDULE_DISPLAY_TZ_FALLBACK
    if campaign.scheduled_timezone:
        try:
            ZoneInfo(campaign.scheduled_timezone)
            zone = campaign.scheduled_timezone
        except Exception:
            pass
    local = timezone.localtime(campaign.scheduled_at, ZoneInfo(zone))
    suffix = "" if zone == SCHEDULE_DISPLAY_TZ_FALLBACK else f" ({zone})"
    return f"{local.strftime('%Y-%m-%d %H:%M')}{suffix}"
