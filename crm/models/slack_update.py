"""Models for inbound `#plan-sprints` Slack ingestion (issue #889, Phase 1).

Phase 1 is the inbound-Slack primitive only: a daily job reads the
`#plan-sprints` channel, persists every conversation thread (root +
all replies) verbatim, incrementally appends new replies to threads it
already captured on earlier runs, and links each thread to the
authoring member and (when resolvable) their active-sprint plan.

No meaning is parsed and no plan progress is mutated in Phase 1 — that
is Phase 2 (issue #890). Everything here is pure capture + matching +
read-only surfacing, staff-only like the rest of the CRM.
"""

from django.conf import settings
from django.db import models

INGEST_STATUS_CHOICES = [
    ('running', 'Running'),
    ('success', 'Success'),
    ('error', 'Error'),
]


class SlackChannelIngest(models.Model):
    """One row per daily `#plan-sprints` ingest run.

    Lets staff confirm the job ran and see what each run added. Counts
    are recorded as the run progresses so a crashed run still shows how
    far it got before ``status`` flipped to ``error``.
    """

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    channel_id = models.CharField(max_length=64, blank=True, default='')
    # The Slack ts window pulled this run (strings — Slack ts are
    # "<seconds>.<microseconds>" and are compared lexically/numerically).
    oldest_ts = models.CharField(max_length=64, blank=True, default='')
    latest_ts = models.CharField(max_length=64, blank=True, default='')
    messages_seen = models.IntegerField(default=0)
    threads_persisted = models.IntegerField(default=0)
    # NEW replies appended to already-known threads this run.
    replies_added = models.IntegerField(default=0)
    members_matched = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=INGEST_STATUS_CHOICES,
        default='running',
    )
    error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['-started_at']),
        ]

    def __str__(self):
        return f'SlackChannelIngest({self.channel_id} @ {self.started_at:%Y-%m-%d %H:%M})'


class SlackThread(models.Model):
    """One row per thread root captured from `#plan-sprints`.

    A standalone (un-replied) message is still a thread of one: its
    ``thread_ts`` equals the message ts and it has a single root
    :class:`SlackMessage`.

    ``member`` / ``plan`` are resolved at ingest time from the root
    author's ``slack_user_id``. Both stay null when the author does not
    match a local user — the thread is never dropped so staff still see
    updates from people we could not auto-match.
    """

    channel_id = models.CharField(max_length=64)
    thread_ts = models.CharField(max_length=64)
    slack_user_id = models.CharField(max_length=64, blank=True, default='')
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='slack_threads',
    )
    plan = models.ForeignKey(
        'plans.Plan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='slack_threads',
    )
    posted_at = models.DateTimeField()
    permalink = models.URLField(max_length=600, blank=True, default='')
    reply_count = models.IntegerField(default=0)
    ingest = models.ForeignKey(
        SlackChannelIngest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='first_captured_threads',
    )
    last_seen_ingest = models.ForeignKey(
        SlackChannelIngest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='last_seen_threads',
    )

    class Meta:
        ordering = ['-posted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['channel_id', 'thread_ts'],
                name='unique_slack_thread_per_channel',
            ),
        ]
        indexes = [
            models.Index(fields=['member', '-posted_at']),
            models.Index(fields=['plan']),
        ]

    def __str__(self):
        return f'SlackThread({self.channel_id}/{self.thread_ts})'

    @property
    def root_message(self):
        """The first (oldest) message in the thread, or None.

        Templates cannot index a prefetched RelatedManager
        (``thread.messages.0`` silently resolves to empty), so this
        property exposes the root message for snippet/author rendering.
        Honours any prefetched ``messages`` cache to avoid an extra
        query per thread.
        """
        messages = list(self.messages.all())
        return messages[0] if messages else None


class SlackMessage(models.Model):
    """One row per individual message in a thread (root + every reply)."""

    thread = models.ForeignKey(
        SlackThread,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    ts = models.CharField(max_length=64)
    slack_user_id = models.CharField(max_length=64, blank=True, default='')
    author_display = models.CharField(max_length=255, blank=True, default='')
    text = models.TextField(blank=True, default='')
    posted_at = models.DateTimeField()
    is_root = models.BooleanField(default=False)
    first_seen_ingest = models.ForeignKey(
        SlackChannelIngest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='first_captured_messages',
    )

    class Meta:
        ordering = ['posted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['thread', 'ts'],
                name='unique_slack_message_per_thread',
            ),
        ]
        indexes = [
            models.Index(fields=['thread', 'posted_at']),
        ]

    def __str__(self):
        return f'SlackMessage({self.thread_id}/{self.ts})'


# Plan-item kinds an auto-applied progress change can target. Kept in sync
# with ``crm.services.plan_sprint_parse.ITEM_KINDS``.
PROGRESS_ITEM_KIND_CHOICES = [
    ('checkpoint', 'Checkpoint'),
    ('deliverable', 'Deliverable'),
    ('next_step', 'Next step'),
]


class IngestedProgressEvent(models.Model):
    """One current row per thread for an LLM auto-apply attempt (issue #890).

    Phase 2 parses a captured :class:`SlackThread` and auto-applies the
    parsed completions to the member's plan items. This row is the
    provenance root for one thread's auto-apply: it carries the staff-facing
    parsed context (``summary`` / ``blockers``) and the idempotency watermark
    (``source_message_ts``), and owns the individual
    :class:`AppliedProgressChange` rows.

    There is at most ONE current event per thread — re-parses on later runs
    UPDATE this single row via ``update_or_create(thread=...)``. The
    watermark, not a second row, is what makes daily re-runs idempotent.
    Deleting the thread cascades the event; staff undo restores each change
    before deleting the event.
    """

    thread = models.ForeignKey(
        SlackThread,
        on_delete=models.CASCADE,
        related_name='progress_events',
    )
    plan = models.ForeignKey(
        'plans.Plan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ingested_progress_events',
    )
    ingest = models.ForeignKey(
        SlackChannelIngest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='progress_events',
    )
    applied_at = models.DateTimeField(auto_now=True)
    summary = models.TextField(blank=True, default='')
    blockers = models.JSONField(default=list, blank=True)
    model_name = models.CharField(max_length=120, blank=True, default='')
    # The latest SlackMessage.ts in the thread at apply time. The idempotency
    # watermark: when it is unchanged on a later run, parse + apply are
    # skipped entirely (no LLM call, no mutation).
    source_message_ts = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-applied_at']
        constraints = [
            models.UniqueConstraint(
                fields=['thread'],
                name='unique_progress_event_per_thread',
            ),
        ]

    def __str__(self):
        return f'IngestedProgressEvent(thread={self.thread_id})'


class AppliedProgressChange(models.Model):
    """One row per individual plan-item ``null -> now`` flip (issue #890).

    A change row is created ONLY when the auto-apply actually flipped the
    item (it was ``done_at IS NULL`` and we set it). Items already done
    (by a human or an earlier ingest) are skipped and NEVER recorded as our
    change — which is what makes "undo touches only its own changes" true:
    a human-completed item can never be un-done by an undo because it was
    never recorded here.

    ``previous_done_at`` is the item's ``done_at`` before this change (always
    ``None`` for a recorded change) and is what an undo restores. The three
    item FKs use ``SET_NULL`` so the audit row survives if the plan item is
    later hard-deleted; reversal then no-ops for that row.
    """

    event = models.ForeignKey(
        IngestedProgressEvent,
        on_delete=models.CASCADE,
        related_name='changes',
    )
    item_kind = models.CharField(
        max_length=20,
        choices=PROGRESS_ITEM_KIND_CHOICES,
    )
    checkpoint = models.ForeignKey(
        'plans.Checkpoint',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='applied_progress_changes',
    )
    deliverable = models.ForeignKey(
        'plans.Deliverable',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='applied_progress_changes',
    )
    next_step = models.ForeignKey(
        'plans.NextStep',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='applied_progress_changes',
    )
    previous_done_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['applied_at', 'id']
        indexes = [
            models.Index(fields=['event']),
        ]

    def __str__(self):
        return f'AppliedProgressChange({self.item_kind} event={self.event_id})'

    @property
    def item(self):
        """The mutated plan item, or None if it was hard-deleted later."""
        if self.item_kind == 'checkpoint':
            return self.checkpoint
        if self.item_kind == 'deliverable':
            return self.deliverable
        if self.item_kind == 'next_step':
            return self.next_step
        return None

    @property
    def item_description(self):
        """Short label of the mutated item for staff display."""
        item = self.item
        return item.description if item is not None else '(deleted item)'
