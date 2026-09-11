"""Background job: reconcile Slack membership and community channels.

Runs daily at 06:00 UTC. For users whose ``slack_checked_at`` is
NULL or older than ``SLACK_MEMBERSHIP_REFRESH_DAYS``, calls Slack's
``users.lookupByEmail`` and updates the canonical ``slack_member`` flag
plus ``slack_checked_at`` timestamp.

Issue #358: ``slack_user_id`` is a poor proxy for "is in the workspace"
(OAuth populates it without a join), so we maintain an explicit verified
``slack_member`` boolean. Used by the dashboard CTA and campaign
targeting.

Issue #918 — Main+ scoping and Tier-2 pacing
--------------------------------------------
Only Main-tier-and-above members (and users with an active
``TierOverride`` to Main+) can be in the Slack workspace at all — Slack
access is a Main+ benefit. The candidate queryset is therefore scoped
to "effective level >= LEVEL_MAIN" using the same canonical predicate as
``email_app.models.email_campaign`` (``tier.level >= LEVEL_MAIN`` OR an
active, non-expired override whose ``override_tier.level >= LEVEL_MAIN``).
This drops the per-run set from "every account" (mostly Free) to the
handful of real Main+ members, which is the direct fix for the 300s
``Q_CLUSTER['timeout']`` failures: prior runs checked a full 120-user
chunk of Free accounts that can never be in Slack, returning
``unknown=120``.

Issue #715 — chunked chain pattern
----------------------------------
The task processes at most ``SLACK_MEMBERSHIP_CHUNK_SIZE`` users per
run. Sizing math (issue #918): ``users.lookupByEmail`` is a Slack
Tier 2 method (~20 requests/minute → a 3s minimum gap between calls).
The global ``Q_CLUSTER['timeout']`` is 300s; at a conservative 3s gap
plus up to 10s of HTTP timeout per call, a chunk of 30 users costs
~30 x 3s = 90s of pacing base, leaving ample headroom under 300s even
when individual calls hit their HTTP timeout (target: a typical run
finishes well under ~120s). With Main+ scoping the realistic candidate
count per run is small (tens, not thousands), so this comfortably
drains the backlog. Prior behaviour (1000 users in one task, then a
120-Free-account chunk paced at a too-fast 1.5s) repeatedly tripped the
300s timeout on prod.

When more users still match the predicate after a chunk completes, we
enqueue a follow-up ``async_task`` pointing at this same function so
the backlog drains in chains instead of one giant run. The cron in
``setup_schedules.py`` is unchanged — it just kicks off the first link
of each chain.

All-unknown guard: if every user in the chunk came back ``unknown``
(Slack integration unconfigured or in a hard outage), we do NOT
enqueue the follow-up. Without this guard, the same users would
re-match the predicate on the next run and chain forever.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field

from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from accounts.tier_audience import effective_level_at_least_q
from accounts.utils.names import set_name_from_external
from community.models import CommunityAuditLog
from community.services import get_community_service
from community.services.staff_notifications import notify_slack_join
from content.access import LEVEL_MAIN
from jobs.tasks import async_task, build_task_name

logger = logging.getLogger(__name__)

# Re-check daily so newly joined eligible members reach community channels
# without needing an operator action.
SLACK_MEMBERSHIP_REFRESH_DAYS = 1

# Per-chunk cap. Each scheduled run processes up to this many users and
# enqueues a follow-up ``async_task`` if more remain. Sized (issue #918)
# to fit comfortably inside the 300s ``Q_CLUSTER['timeout']`` ceiling at
# the corrected Tier-2 pacing: 30 users x 3.0s pacing = 90s base,
# leaving >200s headroom even when individual calls hit their 10s HTTP
# timeout. With Main+ scoping the realistic candidate count per run is
# small (tens), so this drains the backlog without ever approaching the
# timeout. See issues #715 and #918.
SLACK_MEMBERSHIP_CHUNK_SIZE = 30

# Absolute per-run hard ceiling. Retained for callers that explicitly
# request a larger ``batch_size`` (e.g. one-off backfills); the periodic
# scheduled run uses ``SLACK_MEMBERSHIP_CHUNK_SIZE`` instead.
SLACK_MEMBERSHIP_BATCH_SIZE = 1000

# Sleep between API calls to stay under Slack's rate limit.
# ``users.lookupByEmail`` is a Slack Tier 2 method (~20 requests/minute),
# so the minimum safe gap is 60/20 = 3.0s. We use exactly that. The
# previous 1.5s value (~40 RPM) was sized against a wrong "Tier 4 /
# 50 RPM" assumption and caused Slack to ``ratelimited`` most of the
# batch, producing the all-unknown result (issue #918). A single
# bounded retry honoring ``Retry-After`` in ``check_workspace_membership``
# absorbs any transient throttle on top of this pacing.
SLACK_MEMBERSHIP_SLEEP_SECONDS = 3.0

# Tag name mirrored on User.tags when issue #354 ships the tags primitive.
SLACK_MEMBER_TAG = "slack-member"


@dataclass(frozen=True)
class ChannelReconciliationResult:
    status: str = "skipped"
    configured_count: int = 0
    added_count: int = 0
    already_present_count: int = 0
    failed_count: int = 0

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SlackMembershipCheckResult:
    outcome: str
    channels: ChannelReconciliationResult = field(
        default_factory=ChannelReconciliationResult,
    )

    def as_dict(self):
        return {"outcome": self.outcome, "channels": self.channels.as_dict()}


def _has_tags_field():
    """Detect whether issue #354 has shipped the ``User.tags`` field.

    The auto-tag mirror is forward-compatible: if the field doesn't
    exist yet we silently skip the tag write.
    """
    try:
        User._meta.get_field('tags')
        return True
    except Exception:
        return False


def _set_slack_member_tag(user, present):
    """Mirror the boolean as a ``slack-member`` tag on User.tags.

    No-op if #354 hasn't shipped or the tags field shape is unexpected.
    Failures are swallowed — the tag is a convenience mirror, not the
    canonical signal.
    """
    if not _has_tags_field():
        return
    try:
        from accounts.utils.tags import set_tags

        tags = list(getattr(user, 'tags', []) or [])
        if present and SLACK_MEMBER_TAG not in tags:
            tags.append(SLACK_MEMBER_TAG)
            set_tags(user, tags)
        elif not present and SLACK_MEMBER_TAG in tags:
            tags = [t for t in tags if t != SLACK_MEMBER_TAG]
            set_tags(user, tags)
    except Exception:
        logger.warning(
            "Failed to mirror Slack membership tag: user_id=%s",
            user.pk,
        )


def _log_check_transition(user, previous_member, new_member, *, source):
    """Write a CommunityAuditLog row for a state transition.

    Caller is responsible for skipping no-op re-checks (same value as
    last time) so we don't flood the audit table.
    """
    CommunityAuditLog.objects.create(
        user=user,
        action="check",
        details=json.dumps({
            "previous": previous_member,
            "new": new_member,
            "source": source,
        }),
    )


def check_user_slack_membership(
    user,
    *,
    service=None,
    audit_source="schedule",
    actor_token=None,
):
    """Check one selected user and apply only a definite Slack outcome.

    This is the shared operation used by scheduled refreshes and explicit
    Studio/API checks. ``unknown`` and provider exceptions are mutation-free.
    Definite members with current Main+ access are also reconciled against
    every configured community channel.
    """
    try:
        service = service or get_community_service()
        outcome, uid = service.check_workspace_membership(user.email)
    except Exception:
        logger.error("Unexpected Slack membership check error: user_id=%s", user.pk)
        return SlackMembershipCheckResult("unknown")
    if outcome not in {"member", "not_member"}:
        return SlackMembershipCheckResult("unknown")

    is_first_check = user.slack_checked_at is None
    previous_member = bool(user.slack_member)
    now = timezone.now()

    if outcome == "member":
        update_fields = ["slack_member", "slack_checked_at"]
        user.slack_member = True
        user.slack_checked_at = now
        if uid and not user.slack_user_id:
            user.slack_user_id = uid
            update_fields.append("slack_user_id")
        if _backfill_name_from_slack(service, user):
            update_fields.extend(["first_name", "last_name"])
        user.save(update_fields=update_fields)
        _set_slack_member_tag(user, True)
        if is_first_check or previous_member is not True:
            _log_check_transition(
                user,
                previous_member,
                True,
                source=audit_source,
            )
        if previous_member is False and is_first_check is False:
            try:
                notify_slack_join(user)
            except Exception:
                logger.error(
                    "Failed Slack-join staff notification: user_id=%s",
                    user.pk,
                )

        channels = _reconcile_community_channels(
            user,
            service,
            slack_user_id=user.slack_user_id or uid,
        )
        _log_channel_reconciliation(
            user,
            channels,
            source=audit_source,
            actor_token=actor_token,
        )
        return SlackMembershipCheckResult("member", channels)
    else:
        user.slack_member = False
        user.slack_checked_at = now
        user.save(update_fields=["slack_member", "slack_checked_at"])
        _set_slack_member_tag(user, False)
        if is_first_check or previous_member is not False:
            _log_check_transition(
                user,
                previous_member,
                False,
                source=audit_source,
            )
    return SlackMembershipCheckResult("not_member")


def _reconcile_community_channels(user, service, *, slack_user_id):
    """Return a deterministic aggregate for one eligible member."""
    raw_channel_ids = getattr(service, "channel_ids", ())
    channel_ids = (
        tuple(raw_channel_ids)
        if isinstance(raw_channel_ids, (list, tuple))
        else ()
    )

    eligible = User.objects.filter(pk=user.pk).filter(main_plus_q()).exists()
    if not eligible:
        return ChannelReconciliationResult(
            status="skipped",
            configured_count=len(channel_ids),
        )
    if not channel_ids:
        return ChannelReconciliationResult(status="unavailable")
    if not slack_user_id:
        return ChannelReconciliationResult(
            status="failed",
            configured_count=len(channel_ids),
            failed_count=len(channel_ids),
        )

    try:
        raw_results = service.add_to_channels(slack_user_id)
    except Exception:
        logger.error(
            "Unexpected Slack channel reconciliation error: user_id=%s configured_count=%s",
            user.pk,
            len(channel_ids),
        )
        return ChannelReconciliationResult(
            status="failed",
            configured_count=len(channel_ids),
            failed_count=len(channel_ids),
        )

    results = raw_results if isinstance(raw_results, list) else []
    added = sum(
        bool(item.get("ok")) and not item.get("already_in")
        for item in results
        if isinstance(item, dict)
    )
    already = sum(
        bool(item.get("ok")) and bool(item.get("already_in"))
        for item in results
        if isinstance(item, dict)
    )
    failed = max(0, len(channel_ids) - added - already)
    if failed == 0:
        status = "complete"
    elif added or already:
        status = "partial"
    else:
        status = "failed"
    return ChannelReconciliationResult(
        status=status,
        configured_count=len(channel_ids),
        added_count=added,
        already_present_count=already,
        failed_count=failed,
    )


def _log_channel_reconciliation(user, result, *, source, actor_token=None):
    if result.added_count == 0 and result.failed_count == 0:
        return
    details = {
        "source": source,
        "result": result.status,
        "configured_count": result.configured_count,
        "added_count": result.added_count,
        "already_present_count": result.already_present_count,
        "failed_count": result.failed_count,
        "subject_user_id": user.pk,
    }
    if source == "api" and actor_token:
        details["actor_token"] = actor_token
    CommunityAuditLog.objects.create(
        user=user,
        action="link",
        details=json.dumps(details),
    )


def refresh_slack_membership(
    *,
    batch_size=None,
    refresh_days=None,
    sleep_seconds=None,
):
    """Refresh ``slack_member`` for users with stale or missing checks.

    Selects up to ``batch_size`` users (default
    ``SLACK_MEMBERSHIP_CHUNK_SIZE``) where ``slack_checked_at`` is NULL
    or older than ``refresh_days``, ordered NULLs first. For each user
    calls ``service.check_workspace_membership(email)``:

    - ``("member", uid)``: set ``slack_member=True``, fill
      ``slack_user_id`` if empty, set ``slack_checked_at=now()``.
    - ``("not_member", None)``: set ``slack_member=False``,
      ``slack_checked_at=now()``.
    - ``("unknown", None)``: leave fields alone — retry next cycle.

    Self-throttles to stay under Slack's Tier 2 rate limit for
    ``users.lookupByEmail`` (~20 RPM → 3s gap; issue #918). If the
    integration is unconfigured (no token), ``check_workspace_membership``
    returns ``unknown`` for everyone and this function becomes a safe
    no-op.

    Candidate scope (issue #918): only users whose effective level is
    Main (``LEVEL_MAIN``) or above are ever checked — either their real
    ``tier.level >= LEVEL_MAIN`` or they hold an active, non-expired
    ``TierOverride`` to Main+. Slack access is a Main+ benefit, so
    Free/Basic accounts are never queried.

    Chain pattern (issue #715): if the chunk completes with at least
    one definite outcome (``member`` or ``not_member``) AND more users
    still match the predicate, this function enqueues a follow-up
    ``async_task`` so the backlog drains in chains without raising the
    global ``Q_CLUSTER['timeout']``. If every user in the chunk came
    back ``unknown`` (integration unconfigured / total outage), we do
    NOT enqueue the follow-up — those users would just match again on
    the next run and chain forever.

    Returns:
        dict: counts keyed by ``members``, ``not_members``, ``unknown``,
        ``total_checked``, channel outcomes, newly added memberships, and
        ``enqueued_followup``.
    """
    batch_size = batch_size or SLACK_MEMBERSHIP_CHUNK_SIZE
    refresh_days = refresh_days or SLACK_MEMBERSHIP_REFRESH_DAYS
    if sleep_seconds is None:
        sleep_seconds = SLACK_MEMBERSHIP_SLEEP_SECONDS

    service = get_community_service()
    cutoff = timezone.now() - timezone.timedelta(days=refresh_days)
    main_plus = main_plus_q()

    # NULLs first so brand-new users are picked up before stale ones.
    # Scope to Main+ effective level (issue #918): a Free/Basic account
    # can never be in the Slack workspace, so checking it is wasteful and
    # was the direct cause of the 300s timeout. ``.distinct()`` collapses
    # the duplicate rows the override join can produce.
    users = list(
        User.objects.filter(main_plus & models_q_null_or_old(cutoff))
        .order_by('slack_checked_at')
        .distinct()[:batch_size]
    )

    members = 0
    not_members = 0
    unknown = 0
    channel_complete = 0
    channel_partial = 0
    channel_failed = 0
    channel_unavailable = 0
    newly_added_channel_memberships = 0

    for index, user in enumerate(users):
        # Self-throttle BETWEEN calls (not before the first one).
        if index > 0 and sleep_seconds:
            time.sleep(sleep_seconds)

        result = check_user_slack_membership(user, service=service)
        if result.outcome == "unknown":
            unknown += 1
            continue
        if result.outcome == "member":
            members += 1
            if result.channels.status == "complete":
                channel_complete += 1
            elif result.channels.status == "partial":
                channel_partial += 1
            elif result.channels.status == "failed":
                channel_failed += 1
            elif result.channels.status == "unavailable":
                channel_unavailable += 1
            newly_added_channel_memberships += result.channels.added_count
        elif result.outcome == "not_member":
            not_members += 1

    total_checked = len(users)

    # Decide whether to chain a follow-up run. We chain only when (a)
    # at least one user in this chunk resolved to a definite outcome
    # (so we know the Slack integration is actually working) AND (b)
    # more users still match the predicate. Without (a) we'd loop
    # forever on an unconfigured environment where every call returns
    # ``unknown``; without (b) we'd kick off a guaranteed-empty run.
    enqueued_followup = False
    made_progress = total_checked > 0 and unknown < total_checked
    if made_progress:
        # Count only Main+ users (issue #918) so the chain decision is
        # computed against the same population as the chunk selection.
        more_remaining = User.objects.filter(
            main_plus & models_q_null_or_old(cutoff)
        ).distinct().exists()
        if more_remaining:
            async_task(
                'community.tasks.slack_membership.refresh_slack_membership',
                task_name=build_task_name(
                    'Refresh Slack membership',
                    f'chunk follow-up ({total_checked} checked)',
                    'Slack membership chain',
                ),
            )
            enqueued_followup = True

    summary = {
        "total_checked": total_checked,
        "members": members,
        "not_members": not_members,
        "unknown": unknown,
        "channel_complete": channel_complete,
        "channel_partial": channel_partial,
        "channel_failed": channel_failed,
        "channel_unavailable": channel_unavailable,
        "newly_added_channel_memberships": newly_added_channel_memberships,
        "enqueued_followup": enqueued_followup,
    }
    logger.info("Slack membership refresh complete: %s", summary)
    return summary


def models_q_null_or_old(cutoff):
    """Q object: ``slack_checked_at IS NULL OR slack_checked_at < cutoff``.

    Extracted so tests can introspect / re-use the predicate.
    """
    return Q(slack_checked_at__isnull=True) | Q(slack_checked_at__lt=cutoff)


def main_plus_q():
    """Q object: effective level >= ``LEVEL_MAIN`` (issue #918).

    Matches users who are Main tier or above either by their real
    ``tier`` row OR by an active, non-expired ``TierOverride`` to Main+.
    This is the same canonical predicate used in
    ``email_app.models.email_campaign`` and mirrors
    ``content.access.get_user_level``'s override resolution. Thin wrapper
    over :func:`accounts.tier_audience.effective_level_at_least_q` so there
    is a single definition. Querysets using this MUST ``.distinct()``
    because the override join can duplicate rows.
    """
    return effective_level_at_least_q(LEVEL_MAIN)


def _backfill_name_from_slack(service, user):
    """Backfill ``first_name`` / ``last_name`` from the Slack user profile.

    Issue #699. Skipped silently if the service does not expose
    ``lookup_user_profile_by_email`` (defensive — keeps tests with
    minimal mocks working), if the profile lookup fails, or if the
    user already has a non-empty name.

    Falls back to splitting ``real_name`` via ``full_name=`` when the
    Slack profile's ``first_name`` / ``last_name`` are both blank
    (some workspaces only fill ``real_name``).

    Returns:
        bool: ``True`` if the in-memory user was mutated; ``False``
        otherwise. Caller folds ``first_name`` / ``last_name`` into
        its existing ``update_fields`` list when this returns True.
    """
    lookup = getattr(service, "lookup_user_profile_by_email", None)
    if not callable(lookup):
        return False
    try:
        profile = lookup(user.email)
    except Exception:
        logger.warning(
            "Slack profile lookup failed; skipping name backfill: user_id=%s",
            user.pk,
        )
        return False
    # Require a concrete dict — MagicMock services in tests that didn't
    # opt into a profile mock will fall through this guard so they
    # don't accidentally write garbage to first_name / last_name.
    if not isinstance(profile, dict):
        return False

    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if first or last:
        return set_name_from_external(
            user, first=first, last=last, source="slack_probe",
        )
    real_name = (profile.get("real_name") or "").strip()
    if real_name:
        return set_name_from_external(
            user, full_name=real_name, source="slack_probe",
        )
    return False
