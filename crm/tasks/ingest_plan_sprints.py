"""Daily ingest of the `#plan-sprints` Slack channel (issue #889, Phase 1).

Reads `#plan-sprints` once per day, persists every thread (root + all
replies) verbatim, incrementally appends new replies to threads already
captured on earlier runs, and links each thread to the authoring member
and (when resolvable) their active-sprint plan.

After capture/append, Phase 2 (issue #890) parses the threads touched this
run that are matched to a member + active-sprint plan and auto-applies the
parsed progress to their plan items (reversibly). The LLM parse step is
gated on ``llm.is_enabled()``: when the LLM is disabled the run still
succeeds as a pure Phase-1 capture run, and a per-thread LLM failure is
logged without failing the run.

The task is safe to run when Slack is disabled or the channel id is
unset — it logs and returns without creating any rows.
"""

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.services.email_resolution import resolve_user_by_email
from community.services.slack import SlackAPIError, SlackCommunityService
from community.slack_config import get_slack_plan_sprints_channel_id
from crm.models import SlackChannelIngest, SlackMessage, SlackThread
from crm.services.slack_note_sync import sync_thread_to_interview_note
from crm.tasks.apply_plan_sprint_progress import apply_progress_for_threads
from integrations.config import get_config, is_enabled
from plans.models import Plan

logger = logging.getLogger(__name__)

User = get_user_model()

# Default days to read back on the very first run (no prior successful
# ingest). Overridable via the ``PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS``
# IntegrationSetting (Studio-editable, no redeploy).
FIRST_RUN_LOOKBACK_DAYS = 7
THREAD_REFRESH_DAYS = 45
INGEST_LEASE_MINUTES = 60


def _first_run_lookback_days():
    """Resolve the first-run lookback window (Studio-overridable, default 7)."""
    raw = get_config('PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS', FIRST_RUN_LOOKBACK_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return FIRST_RUN_LOOKBACK_DAYS
    return days if days > 0 else FIRST_RUN_LOOKBACK_DAYS


def _positive_int_config(key, default):
    try:
        value = int(get_config(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


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


def _resolve_member_and_plan(service, slack_user_id):
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
        profile_lookup = getattr(service, 'lookup_user_profile_by_id', None)
        if profile_lookup is not None:
            profile = profile_lookup(slack_user_id) or {}
            member = resolve_user_by_email(profile.get('email', ''))
            if member is not None and not member.slack_user_id:
                member.slack_user_id = slack_user_id
                member.save(update_fields=['slack_user_id'])
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


def _refresh_thread_match(thread, member, plan):
    """Persist a newly-resolved member/plan on an existing raw thread."""
    update_fields = []
    member_id = member.pk if member is not None else None
    plan_id = plan.pk if plan is not None else None
    if thread.member_id != member_id:
        thread.member = member
        update_fields.append("member")
    if thread.plan_id != plan_id:
        thread.plan = plan
        update_fields.append("plan")
    if update_fields:
        thread.save(update_fields=update_fields)
    return thread.member_id is not None


def _last_successful_latest_ts(channel_id):
    """The ``latest_ts`` of the most recent successful run for this channel."""
    last = (
        SlackChannelIngest.objects
        .filter(
            channel_id=channel_id,
            status='success',
            advances_watermark=True,
        )
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
    member, plan = _resolve_member_and_plan(service, slack_user_id)

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
    else:
        _refresh_thread_match(thread, member, plan)

    # Fetch the full thread (root + every reply) so late-arriving replies
    # are picked up on later runs.
    thread_messages = service.fetch_conversation_replies(channel_id, thread_ts)
    if not thread_messages:
        raise SlackAPIError(
            f"Slack returned no messages for thread {thread_ts}",
            method="conversations.replies",
            error_code="empty_thread",
        )

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
    if member is not None:
        sync_thread_to_interview_note(thread, ingest=run)

    return new_replies, created, (member is not None), thread


def _date_to_ts(since):
    """Convert a ``datetime.date``/``datetime`` to a Slack ``oldest`` ts string.

    A bare ``date`` is interpreted as midnight UTC of that day.
    """
    if isinstance(since, datetime):
        dt = since if since.tzinfo else since.replace(tzinfo=dt_timezone.utc)
    else:
        dt = datetime(since.year, since.month, since.day, tzinfo=dt_timezone.utc)
    return f"{dt.timestamp():.6f}"


def _resolve_oldest(channel_id, *, oldest_ts=None, since=None):
    """Resolve the effective ``oldest`` Slack ts for this run.

    Precedence: an explicit ``oldest_ts`` (string) wins, then a ``since``
    date/datetime, then the forward watermark of the last successful run,
    and finally the :data:`FIRST_RUN_LOOKBACK_DAYS` default when nothing
    has run before. Returning the same default behaviour keeps the daily
    task unchanged when called with no arguments.
    """
    if oldest_ts:
        return str(oldest_ts)
    if since is not None:
        return _date_to_ts(since)
    watermark = _last_successful_latest_ts(channel_id)
    if watermark:
        return watermark
    lookback = timezone.now() - timezone.timedelta(days=_first_run_lookback_days())
    return f"{lookback.timestamp():.6f}"


def _acquire_run(channel_id, oldest, *, advances_watermark=True):
    """Acquire the per-channel ingest lease, expiring abandoned runs."""
    now = timezone.now()
    lease_minutes = _positive_int_config(
        'PLAN_SPRINTS_INGEST_LEASE_MINUTES', INGEST_LEASE_MINUTES,
    )
    lease_expires_at = now + timezone.timedelta(minutes=lease_minutes)
    stale_before = now - timezone.timedelta(minutes=lease_minutes)

    with transaction.atomic():
        stale = SlackChannelIngest.objects.select_for_update().filter(
            channel_id=channel_id,
            status='running',
        ).filter(
            Q(lease_expires_at__lte=now)
            | Q(lease_expires_at__isnull=True, started_at__lte=stale_before)
        )
        stale.update(
            status='error',
            error='Ingest lease expired before the run completed',
            finished_at=now,
            lease_expires_at=None,
        )
        try:
            with transaction.atomic():
                return SlackChannelIngest.objects.create(
                    channel_id=channel_id,
                    oldest_ts=oldest,
                    status='running',
                    lease_expires_at=lease_expires_at,
                    advances_watermark=advances_watermark,
                )
        except IntegrityError:
            return None


def _running_run(channel_id):
    return (
        SlackChannelIngest.objects
        .filter(channel_id=channel_id, status='running')
        .order_by('-started_at')
        .first()
    )


def _fail_run(run, exc, *, messages_seen=0, threads_persisted=0,
              replies_added=0, members_matched=0, known_threads_checked=0):
    """Put an acquired run in a terminal error state without moving its watermark."""
    run.status = 'error'
    run.error = f'{exc.__class__.__name__}: {exc}'
    run.finished_at = timezone.now()
    run.latest_ts = run.oldest_ts
    run.messages_seen = messages_seen
    run.threads_persisted = threads_persisted
    run.replies_added = replies_added
    run.members_matched = members_matched
    run.known_threads_checked = known_threads_checked
    run.lease_expires_at = None
    run.save()
    logger.exception('plan-sprints ingest failed')
    return run


def ingest_plan_sprints(*, oldest_ts=None, since=None, dry_run=False):
    """`#plan-sprints` ingest entry point (registered in setup_schedules).

    Called with no arguments by the daily schedule: reads from the forward
    watermark (or the :data:`FIRST_RUN_LOOKBACK_DAYS` default on a first
    run). A retroactive backfill (issue #904) passes an explicit
    ``oldest_ts`` (a Slack ts string) or ``since`` (a ``date``/``datetime``)
    to read older history; both override the watermark/default.

    ``dry_run=True`` runs the full capture + parse + apply path inside a
    transaction that is rolled back at the end, so nothing is persisted —
    the returned :class:`SlackChannelIngest` carries the counts of what a
    real run WOULD have written. The run is idempotent regardless: the
    ``IngestedProgressEvent`` / ``AppliedProgressChange`` watermarks make a
    committed re-run over the same window a no-op for already-applied
    progress.

    No-ops cleanly (logs, returns None, creates no rows) when Slack is
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

    oldest = _resolve_oldest(channel_id, oldest_ts=oldest_ts, since=since)
    advances_watermark = oldest_ts is None and since is None

    if dry_run:
        with transaction.atomic():
            run = _run_ingest(
                service,
                channel_id,
                oldest,
                dry_run=True,
                advances_watermark=advances_watermark,
            )
            # Roll back every row written this run; the in-memory ``run``
            # object keeps its counts so the caller can report them.
            transaction.set_rollback(True)
        return run

    return _run_ingest(
        service,
        channel_id,
        oldest,
        dry_run=False,
        advances_watermark=advances_watermark,
    )


def _reread_thread_replies(service, run, thread):
    """Re-fetch a known thread's replies and append genuinely-new rows.

    Idempotent on ``(thread, ts)``: only messages not already persisted are
    written. Recomputes ``reply_count`` and bumps ``last_seen_ingest``.
    Returns the number of NEW reply :class:`SlackMessage` rows added.
    """
    thread_messages = service.fetch_conversation_replies(
        thread.channel_id, thread.thread_ts,
    )
    if not thread_messages:
        raise SlackAPIError(
            f"Slack returned no messages for thread {thread.thread_ts}",
            method="conversations.replies",
            error_code="empty_thread",
        )

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
            is_root=(msg_ts == thread.thread_ts),
            first_seen_ingest=run,
        )
        existing_ts.add(msg_ts)
        if msg_ts != thread.thread_ts:
            new_replies += 1

    thread.last_seen_ingest = run
    thread.reply_count = max(thread.messages.count() - 1, 0)
    thread.save(update_fields=["last_seen_ingest", "reply_count"])
    if thread.member_id is not None:
        sync_thread_to_interview_note(thread, ingest=run)
    return new_replies


def reparse_plan_sprints(*, since, dry_run=False):
    """Force a replies re-read + Phase 2 re-parse of EXISTING watermarked threads.

    The daily task rides a forward watermark, so it never revisits threads it
    has already captured. This operator path iterates every persisted
    :class:`SlackThread` in the channel whose root was posted at/after
    ``since`` (a ``date``/``datetime``), re-reads ``conversations.replies`` for
    each, appends any genuinely-new reply rows (idempotent on ``(thread, ts)``),
    recomputes ``reply_count``, and re-runs the Phase 2 parse + auto-apply for
    those threads. The ``source_message_ts`` watermark in ``apply_thread_progress``
    keeps the parse a no-op when no new reply changes the latest ts.

    ``dry_run=True`` runs the full path inside a transaction that is rolled
    back, so nothing is persisted; the returned :class:`SlackChannelIngest`
    carries the counts of what a real run WOULD have written.

    No-ops cleanly (logs, returns None) when Slack is disabled or the channel
    id is unset.
    """
    if not is_enabled('SLACK_ENABLED'):
        logger.info("plan-sprints reparse skipped: SLACK_ENABLED is off")
        return None
    channel_id = get_slack_plan_sprints_channel_id()
    if not channel_id:
        logger.info("plan-sprints reparse skipped: no #plan-sprints channel configured")
        return None

    service = SlackCommunityService()
    since_ts = _date_to_ts(since)

    if dry_run:
        with transaction.atomic():
            run = _run_reparse(service, channel_id, since_ts, dry_run=True)
            transaction.set_rollback(True)
        return run

    return _run_reparse(service, channel_id, since_ts, dry_run=False)


def _run_reparse(service, channel_id, since_ts, *, dry_run):
    """Re-read replies + re-parse existing threads since ``since_ts``. Returns the run."""
    run = _acquire_run(channel_id, since_ts, advances_watermark=False)
    if run is None:
        return _running_run(channel_id)

    since_dt = _ts_to_datetime(since_ts)
    threads = list(
        SlackThread.objects
        .filter(
            channel_id=channel_id,
            posted_at__gte=since_dt,
            privacy_erased=False,
        )
        .select_related('member', 'plan__sprint', 'interview_note')
        .order_by('posted_at')
    )

    threads_seen = 0
    replies_added = 0
    members_matched = 0
    latest_ts = since_ts
    touched_threads = {}

    try:
        if getattr(service, 'reply_user_token', 'test-double') == '':
            raise SlackAPIError(
                'SLACK_PLAN_SPRINTS_USER_TOKEN is not configured',
                method='conversations.replies',
                error_code='missing_reply_user_token',
            )
        for thread in threads:
            threads_seen += 1
            member, plan = _resolve_member_and_plan(service, thread.slack_user_id)
            matched = _refresh_thread_match(thread, member, plan)
            if matched:
                members_matched += 1
            with transaction.atomic():
                new_replies = _reread_thread_replies(service, run, thread)
            replies_added += new_replies
            if thread.thread_ts > latest_ts:
                latest_ts = thread.thread_ts
            # Always a re-parse candidate: the watermark guard in
            # apply_thread_progress makes it a no-op when nothing changed.
            touched_threads[thread.pk] = thread
        if touched_threads:
            candidates = [
                t for t in touched_threads.values()
                if t.member_id is not None and t.plan_id is not None
            ]
            if candidates:
                apply_progress_for_threads(candidates, ingest=run)
    except Exception as exc:
        return _fail_run(
            run, exc,
            messages_seen=threads_seen,
            replies_added=replies_added,
            members_matched=members_matched,
            known_threads_checked=threads_seen,
        )

    run.messages_seen = threads_seen
    run.threads_persisted = 0
    run.replies_added = replies_added
    run.members_matched = members_matched
    run.latest_ts = latest_ts
    run.known_threads_checked = threads_seen
    run.status = 'success'
    run.finished_at = timezone.now()
    run.lease_expires_at = None
    run.save()
    logger.info(
        "plan-sprints reparse complete%s: %s threads re-read, %s replies added, %s matched",
        " (dry-run, rolled back)" if dry_run else "",
        threads_seen, replies_added, members_matched,
    )
    return run


def _run_ingest(
    service,
    channel_id,
    oldest,
    *,
    dry_run,
    advances_watermark,
):
    """Capture + parse + apply over the ``oldest..now`` window. Returns the run.

    Shared by the live daily task and the dry-run backfill. The ``dry_run``
    flag is only carried onto the returned run for reporting — the caller
    wraps the dry-run call in a transaction it rolls back.
    """
    run = _acquire_run(
        channel_id,
        oldest,
        advances_watermark=advances_watermark,
    )
    if run is None:
        return _running_run(channel_id)

    messages_seen = 0
    threads_persisted = 0
    replies_added = 0
    members_matched = 0
    latest_ts = oldest
    known_threads_checked = 0
    # Threads created or grown this run, deduped by pk — the candidates for
    # the Phase 2 parse + auto-apply step below.
    touched_threads = {}

    try:
        if getattr(service, 'reply_user_token', 'test-double') == '':
            raise SlackAPIError(
                'SLACK_PLAN_SPRINTS_USER_TOKEN is not configured',
                method='conversations.replies',
                error_code='missing_reply_user_token',
            )
        messages = service.fetch_conversation_history(channel_id, oldest=oldest)
        roots_seen = set()
        for message in messages:
            messages_seen += 1
            if message.get("ts", "") > latest_ts:
                latest_ts = message["ts"]
            if not _is_member_message(message):
                continue
            root_ts = message.get('thread_ts') or message['ts']
            roots_seen.add(root_ts)
            if SlackThread.objects.filter(
                channel_id=channel_id,
                thread_ts=root_ts,
                privacy_erased=True,
            ).exists():
                continue
            with transaction.atomic():
                new_replies, created, matched, thread = _upsert_thread(
                    service, run, channel_id, message,
                )
            # ``new_replies`` already excludes the root on first capture
            # (``_upsert_thread`` only counts non-root messages for a created
            # thread). Count it for both first-capture and incremental runs so
            # ``replies_added`` reflects every reply row written this run.
            replies_added += new_replies
            if created:
                threads_persisted += 1
                if matched:
                    members_matched += 1
            if created or new_replies:
                touched_threads[thread.pk] = thread

        refresh_days = _positive_int_config(
            'PLAN_SPRINTS_THREAD_REFRESH_DAYS', THREAD_REFRESH_DAYS,
        )
        refresh_since = timezone.now() - timezone.timedelta(days=refresh_days)
        known_threads = (
            SlackThread.objects
            .filter(
                channel_id=channel_id,
                privacy_erased=False,
            )
            .filter(
                Q(plan__sprint__status='active')
                | Q(posted_at__gte=refresh_since)
            )
            .exclude(thread_ts__in=roots_seen)
            .select_related('member', 'plan__sprint', 'interview_note')
            .order_by('posted_at')
        )
        for thread in known_threads:
            known_threads_checked += 1
            member, plan = _resolve_member_and_plan(
                service, thread.slack_user_id,
            )
            _refresh_thread_match(thread, member, plan)
            with transaction.atomic():
                new_replies = _reread_thread_replies(service, run, thread)
            replies_added += new_replies
            if new_replies:
                touched_threads[thread.pk] = thread

        # Phase 2 (issue #890): parse + auto-apply progress for the threads
        # touched this run. The per-channel lease prevents overlapping daily,
        # API, and manual-reparse workers from racing the idempotency watermark.
        if touched_threads:
            candidates = [
                t for t in touched_threads.values()
                if t.member_id is not None and t.plan_id is not None
            ]
            if candidates:
                apply_progress_for_threads(candidates, ingest=run)
    except Exception as exc:
        return _fail_run(
            run, exc,
            messages_seen=messages_seen,
            threads_persisted=threads_persisted,
            replies_added=replies_added,
            members_matched=members_matched,
            known_threads_checked=known_threads_checked,
        )

    run.messages_seen = messages_seen
    run.threads_persisted = threads_persisted
    run.replies_added = replies_added
    run.members_matched = members_matched
    run.latest_ts = latest_ts
    run.known_threads_checked = known_threads_checked
    run.status = 'success'
    run.finished_at = timezone.now()
    run.lease_expires_at = None
    run.save()
    logger.info(
        "plan-sprints ingest complete%s: %s seen, %s new threads, %s new replies, %s matched",
        " (dry-run, rolled back)" if dry_run else "",
        messages_seen, threads_persisted, replies_added, members_matched,
    )
    return run
