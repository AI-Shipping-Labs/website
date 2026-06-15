"""Background job: refresh Slack workspace membership per user.

Runs every 30 minutes (cron). For users whose ``slack_checked_at`` is
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

# Re-check workspace membership at most once a week to keep API spend low
# while still picking up users who joined Slack after signup.
SLACK_MEMBERSHIP_REFRESH_DAYS = 7

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
        tags = list(getattr(user, 'tags', []) or [])
        if present and SLACK_MEMBER_TAG not in tags:
            tags.append(SLACK_MEMBER_TAG)
            user.tags = tags
            user.save(update_fields=['tags'])
        elif not present and SLACK_MEMBER_TAG in tags:
            tags = [t for t in tags if t != SLACK_MEMBER_TAG]
            user.tags = tags
            user.save(update_fields=['tags'])
    except Exception:
        logger.warning(
            "Failed to mirror slack_member to tag for %s",
            getattr(user, 'email', '<unknown>'),
            exc_info=True,
        )


def _log_check_transition(user, previous_member, new_member):
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
            "source": "slack_membership_task",
        }),
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
        ``total_checked``, ``transitions``, ``enqueued_followup``.
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
    transitions = 0

    for index, user in enumerate(users):
        # Self-throttle BETWEEN calls (not before the first one).
        if index > 0 and sleep_seconds:
            time.sleep(sleep_seconds)

        try:
            outcome, uid = service.check_workspace_membership(user.email)
        except Exception:
            logger.exception(
                "Unexpected error checking Slack membership for %s",
                user.email,
            )
            unknown += 1
            continue

        if outcome == "unknown":
            unknown += 1
            continue

        # Treat NULL slack_checked_at as a first check; both the
        # state flip member<->not_member and the first-ever check
        # are worth recording. Stale re-checks that return the same
        # value are skipped to keep the audit table small.
        is_first_check = user.slack_checked_at is None
        previous_member = bool(user.slack_member)
        now = timezone.now()

        if outcome == "member":
            update_fields = ['slack_member', 'slack_checked_at']
            user.slack_member = True
            user.slack_checked_at = now
            if uid and not user.slack_user_id:
                user.slack_user_id = uid
                update_fields.append('slack_user_id')
            # Backfill first_name/last_name from the Slack profile if
            # those fields are still empty locally (issue #699).
            # Best-effort: a probe failure must NEVER break the
            # membership update — the user is in Slack, that's the
            # source of truth here.
            if _backfill_name_from_slack(service, user):
                update_fields.extend(['first_name', 'last_name'])
            user.save(update_fields=update_fields)
            _set_slack_member_tag(user, True)
            members += 1
            if is_first_check or previous_member is not True:
                transitions += 1
                _log_check_transition(user, previous_member, True)
            # Issue #959: staff heads-up on a GENUINE join. Fire ONLY on a
            # forward transition observed on a PRIOR cycle: the user was a
            # non-member previously (``previous_member is False``) AND was
            # already checked before (``is_first_check is False``). The
            # latter is the load-bearing backfill guard — it prevents the
            # first-ever observation of a user who is already in Slack from
            # email-blasting. Wrapped so a notifier failure never breaks the
            # membership update or the chunk/chain loop.
            if previous_member is False and is_first_check is False:
                try:
                    notify_slack_join(user)
                except Exception:
                    logger.exception(
                        "Failed to send Slack-join staff notification for %s",
                        user.email,
                    )
        elif outcome == "not_member":
            user.slack_member = False
            user.slack_checked_at = now
            user.save(update_fields=['slack_member', 'slack_checked_at'])
            _set_slack_member_tag(user, False)
            not_members += 1
            if is_first_check or previous_member is not False:
                transitions += 1
                _log_check_transition(user, previous_member, False)

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
        "transitions": transitions,
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
            "Slack profile lookup failed for %s; skipping name backfill",
            user.email,
            exc_info=True,
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
