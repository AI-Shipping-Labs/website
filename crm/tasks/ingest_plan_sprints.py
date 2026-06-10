"""Daily ingest of the `#plan-sprints` Slack channel (issue #889, Phase 1).

Reads `#plan-sprints` once per day, persists every thread (root + all
replies) verbatim, incrementally appends new replies to threads already
captured on earlier runs, and links each thread to the authoring member
and (when resolvable) their active-sprint plan.

Phase 1 is pure capture + matching + read-only surfacing: it does NOT
parse meaning or mutate any plan progress (that is Phase 2, issue #890).

The task is safe to run when Slack is disabled or the channel id is
unset — it logs and returns without creating any rows.
"""

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from community.services.slack import SlackAPIError, SlackCommunityService
from community.slack_config import get_slack_plan_sprints_channel_id
from crm.models import SlackChannelIngest, SlackMessage, SlackThread
from integrations.config import is_enabled
from plans.models import Plan

logger = logging.getLogger(__name__)

User = get_user_model()

# How far back to read on the very first run (no prior successful ingest).
FIRST_RUN_LOOKBACK_DAYS = 7


def _ts_to_datetime(ts):
    """Convert a Slack ts string ("seconds.micros") to an aware datetime."""
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return timezone.now()
    return datetime.fromtimestamp(seconds, tz=dt_timezone.utc)


def _is_member_message(message):
    """True when a Slack message is a real member post (not a bot/system event).

    Join notices, channel-topic changes, and bot posts either carry a
    ``subtype`` / ``bot_id`` or have no ``user`` — none of those should
    be persisted as a member thread.
    """
    if message.get("bot_id"):
        return False
    if message.get("subtype"):
        return False
    if not message.get("user"):
        return False
    return True


def _resolve_member_and_plan(slack_user_id):
    """Resolve a Slack user id to a local User and their active-sprint plan.

    Returns ``(member, plan)``. ``member`` is None when no user matches
    the ``slack_user_id``; ``plan`` is None when the member has no plan
    in an active sprint. The most recent active sprint wins when several
    are active.
    """
    if not slack_user_id:
        return None, None
    member = User.objects.filter(slack_user_id=slack_user_id).first()
    if member is None:
        return None, None
    plan = (
        Plan.objects
        .filter(member=member, sprint__status='active')
        .select_related('sprint')
        .order_by('-sprint__start_date', '-created_at')
        .first()
    )
    return member, plan


def _last_successful_latest_ts(channel_id):
    """The ``latest_ts`` of the most recent successful run for this channel."""
    last = (
        SlackChannelIngest.objects
        .filter(channel_id=channel_id, status='success')
        .exclude(latest_ts='')
        .order_by('-started_at')
        .first()
    )
    return last.latest_ts if last else ''


def _upsert_thread(service, run, channel_id, root_message):
    """Upsert a SlackThread and all its messages; returns new-replies count.

    Idempotent on ``(channel_id, thread_ts)`` for the thread and on
    ``(thread, ts)`` for each message. On a re-run only genuinely new
    replies are inserted. Returns the number of NEW SlackMessage rows
    added this run (for ``replies_added`` accounting).
    """
    thread_ts = root_message.get("thread_ts") or root_message["ts"]
    slack_user_id = root_message.get("user", "")
    member, plan = _resolve_member_and_plan(slack_user_id)

    thread, created = SlackThread.objects.get_or_create(
        channel_id=channel_id,
        thread_ts=thread_ts,
        defaults={
            "slack_user_id": slack_user_id,
            "member": member,
            "plan": plan,
            "posted_at": _ts_to_datetime(thread_ts),
            "ingest": run,
            "last_seen_ingest": run,
        },
    )
    if created:
        thread.permalink = service.get_message_permalink(channel_id, thread_ts)

    # Fetch the full thread (root + every reply) so late-arriving replies
    # are picked up on later runs.
    try:
        thread_messages = service.fetch_conversation_replies(channel_id, thread_ts)
    except SlackAPIError:
        logger.warning(
            "Failed to fetch replies for thread %s in %s", thread_ts, channel_id,
        )
        thread_messages = [root_message]

    existing_ts = set(thread.messages.values_list("ts", flat=True))
    new_replies = 0
    display_cache = {}
    for msg in thread_messages:
        if not _is_member_message(msg):
            continue
        msg_ts = msg["ts"]
        if msg_ts in existing_ts:
            continue
        msg_user = msg.get("user", "")
        if msg_user not in display_cache:
            display_cache[msg_user] = service.lookup_user_display_name(msg_user)
        SlackMessage.objects.create(
            thread=thread,
            ts=msg_ts,
            slack_user_id=msg_user,
            author_display=display_cache[msg_user],
            text=msg.get("text", "") or "",
            posted_at=_ts_to_datetime(msg_ts),
            is_root=(msg_ts == thread_ts),
            first_seen_ingest=run,
        )
        existing_ts.add(msg_ts)
        if not created or msg_ts != thread_ts:
            # On first capture the root message is not a "reply"; replies
            # added beyond the root (and everything on a re-run) count.
            new_replies += 1

    # Keep thread bookkeeping in sync.
    thread.last_seen_ingest = run
    thread.reply_count = max(thread.messages.count() - 1, 0)
    update_fields = ["last_seen_ingest", "reply_count"]
    if created:
        update_fields.append("permalink")
    thread.save(update_fields=update_fields)

    return new_replies, created, (member is not None)


def ingest_plan_sprints():
    """Daily `#plan-sprints` ingest entry point (registered in setup_schedules).

    No-ops cleanly (logs, returns, creates no rows) when Slack is
    disabled or the channel id is unset.
    """
    if not is_enabled('SLACK_ENABLED'):
        logger.info("plan-sprints ingest skipped: SLACK_ENABLED is off")
        return None
    channel_id = get_slack_plan_sprints_channel_id()
    if not channel_id:
        logger.info("plan-sprints ingest skipped: no #plan-sprints channel configured")
        return None

    service = SlackCommunityService()

    oldest = _last_successful_latest_ts(channel_id)
    if not oldest:
        lookback = timezone.now() - timezone.timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
        oldest = f"{lookback.timestamp():.6f}"

    run = SlackChannelIngest.objects.create(
        channel_id=channel_id,
        oldest_ts=oldest,
        status='running',
    )

    try:
        messages = service.fetch_conversation_history(channel_id, oldest=oldest)
    except SlackAPIError as exc:
        run.status = 'error'
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error', 'finished_at'])
        logger.exception("plan-sprints ingest failed reading history")
        return run

    messages_seen = 0
    threads_persisted = 0
    replies_added = 0
    members_matched = 0
    latest_ts = oldest

    try:
        for message in messages:
            messages_seen += 1
            if message.get("ts", "") > latest_ts:
                latest_ts = message["ts"]
            if not _is_member_message(message):
                continue
            with transaction.atomic():
                new_replies, created, matched = _upsert_thread(
                    service, run, channel_id, message,
                )
            replies_added += new_replies if not created else 0
            if created:
                threads_persisted += 1
                if matched:
                    members_matched += 1
    except SlackAPIError as exc:
        run.status = 'error'
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.messages_seen = messages_seen
        run.threads_persisted = threads_persisted
        run.replies_added = replies_added
        run.members_matched = members_matched
        run.latest_ts = latest_ts
        run.save()
        logger.exception("plan-sprints ingest failed mid-run")
        return run

    run.messages_seen = messages_seen
    run.threads_persisted = threads_persisted
    run.replies_added = replies_added
    run.members_matched = members_matched
    run.latest_ts = latest_ts
    run.status = 'success'
    run.finished_at = timezone.now()
    run.save()
    logger.info(
        "plan-sprints ingest complete: %s seen, %s new threads, %s new replies, %s matched",
        messages_seen, threads_persisted, replies_added, members_matched,
    )
    return run
