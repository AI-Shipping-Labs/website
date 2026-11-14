"""Schedule, unschedule, and dispatch newsletter campaigns.

The schedule lives on the ``EmailCampaign`` row itself (``scheduled_at`` as
the UTC dispatch instant, ``scheduled_timezone`` as the IANA display name),
so Studio and the API serialize it like any other campaign field. A
recurring django-q schedule (``campaign-scheduled-dispatch``, every minute)
calls :func:`dispatch_due_campaigns`, which claims due drafts through the
same :func:`~email_app.services.campaign_dispatch.claim_and_submit_campaign`
path Studio's manual send uses — the claim is the single-send guarantee, and
claiming also clears the schedule fields.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _parse_iso_datetime(raw):
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
    return parsed


def _get_timezone(timezone_name):
    if not timezone_name:
        return None, None
    if not isinstance(timezone_name, str):
        raise CampaignScheduleValidationError({
            "scheduled_timezone": "Must be an IANA timezone name string.",
        })
    normalized_name = timezone_name.strip()
    try:
        zone = ZoneInfo(normalized_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CampaignScheduleValidationError({
            "scheduled_timezone": (
                f"Unknown IANA timezone {timezone_name!r}."
            ),
        })
    return zone, normalized_name


def _valid_local_time_candidates(local_datetime, zone):
    candidates = {}
    for fold in (0, 1):
        candidate = local_datetime.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(dt_timezone.utc)
        round_trip = instant.astimezone(zone).replace(tzinfo=None)
        if round_trip == local_datetime:
            candidates[instant] = candidate
    return list(candidates.values())


def _interpret_naive_datetime(parsed, zone, timezone_name):
    candidates = _valid_local_time_candidates(parsed, zone)
    if not candidates:
        raise CampaignScheduleValidationError({
            "scheduled_at": (
                f"This local time does not exist in {timezone_name} "
                "because of a daylight-saving transition."
            ),
        })
    if len(candidates) > 1:
        raise CampaignScheduleValidationError({
            "scheduled_at": (
                f"This local time occurs twice in {timezone_name}; include "
                "an explicit UTC offset to choose one."
            ),
        })
    return candidates[0]


def _validate_aware_datetime(parsed, zone, timezone_name):
    candidates = _valid_local_time_candidates(parsed.replace(tzinfo=None), zone)
    if not candidates:
        raise CampaignScheduleValidationError({
            "scheduled_at": (
                f"This local time does not exist in {timezone_name} "
                "because of a daylight-saving transition."
            ),
        })
    valid_offsets = {candidate.utcoffset() for candidate in candidates}
    if parsed.utcoffset() not in valid_offsets:
        raise CampaignScheduleValidationError({
            "scheduled_timezone": (
                f"{timezone_name} does not match the UTC offset in "
                "scheduled_at."
            ),
        })


def _resolve_scheduled_datetime(parsed, zone, timezone_name):
    if zone is None:
        if parsed.tzinfo is not None:
            return parsed
        raise CampaignScheduleValidationError({
            "scheduled_timezone": (
                "Required when scheduled_at carries no UTC offset "
                "(e.g. \"Europe/Berlin\")."
            ),
        })
    if parsed.tzinfo is None:
        return _interpret_naive_datetime(parsed, zone, timezone_name)
    _validate_aware_datetime(parsed, zone, timezone_name)
    return parsed


def parse_scheduled_time(raw, timezone_name, *, now):
    """Return a validated UTC instant and its optional IANA display timezone.

    Naive local times require a timezone and are rejected when a daylight-
    saving transition makes them nonexistent or ambiguous. An aware datetime
    may include a timezone when its offset is valid for that local time.
    """
    parsed = _parse_iso_datetime(raw)
    zone, normalized_name = _get_timezone(timezone_name)
    parsed = _resolve_scheduled_datetime(parsed, zone, normalized_name)

    if parsed <= now:
        raise CampaignScheduleValidationError({
            "scheduled_at": "Must be in the future.",
        })

    return parsed.astimezone(dt_timezone.utc), normalized_name


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
    from email_app.services.campaign_dispatch import claim_and_submit_campaign

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
        result = claim_and_submit_campaign(
            campaign_id,
            source="Scheduled campaign dispatch",
            scheduled_due_by=now,
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
