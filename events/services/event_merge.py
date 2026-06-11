"""Duplicate-event merge engine (issue #881).

Fold an already-duplicated event PAIR — the same real session represented twice
because a pre-fix sync minted a ``origin='github', kind='workshop'`` row
alongside the operator-authored ``origin='studio'`` event — into a single
surviving event without losing registrations or recording data.

This mirrors ``accounts.services.account_merge`` in spirit (a structured
``MergePlan``, a mandatory ``dry_run`` that runs the whole algorithm and then
rolls back, a single ``CommunityAuditLog`` write on a real merge) but the carry
rules are event-specific:

- Registrations: move ``EventRegistration`` rows from the duplicate to the
  canonical event, de-duplicated by user. When a user is registered on both, the
  canonical row is kept but its ``registered_at`` is back-dated to the earliest
  of the two (so the earliest intent-to-attend survives). The duplicate's row is
  deleted (a relationship row, not canonical content — #864 governs content, not
  join rows).
- Content: copy recording / transcript / timestamps / materials / cover and the
  ``zoom_*`` fields from the duplicate onto the canonical event ONLY where the
  canonical field is empty. Operator-entered values are never clobbered.
- Workshop link: repoint ``Workshop.event`` (OneToOne) at the canonical event.
- Retire the duplicate WITHOUT a hard delete (#864 no-deletes policy): set
  ``status='cancelled'``, ``published=False``, and unlink any workshop reference.
  The retired duplicate disappears from public ``/events`` listings and detail
  (``HIDDEN_FROM_PUBLIC_STATUSES`` includes ``cancelled``) but the row survives
  for audit and reversal.

Idempotency: a pair where the duplicate is already cancelled+unpublished and no
workshop points at it is ``already_merged`` — the merge is a no-op and writes no
audit row.

``Event.save()`` enforces the origin invariant (#564): a ``studio`` event must
keep ``source_repo=''`` and a ``github`` event must keep it non-empty. We never
flip ``origin`` or ``source_repo`` on either side, so the invariant holds.
"""

import datetime as dt
import json
import logging

from django.db import transaction
from django.db.models.functions import TruncDate

from community.models import CommunityAuditLog
from events.models import Event, EventRegistration

logger = logging.getLogger(__name__)

AUDIT_ACTION = "merge_events"

# Content fields copied from the duplicate onto the canonical event ONLY when the
# canonical value is empty. These are displayable / recording fields — never the
# source-ownership fields (``origin``, ``source_repo``, ...) which would trip the
# ``Event.save()`` invariant, and never operational scheduling fields
# (``start_datetime``, ``status``) which the operator owns on the canonical row.
_CARRY_FIELDS = (
    "recording_url",
    "recording_s3_url",
    "recording_embed_url",
    "transcript_url",
    "transcript_text",
    "timestamps",
    "materials",
    "cover_image_url",
    "zoom_meeting_id",
    "zoom_join_url",
)


class EventMergeError(Exception):
    """Base class for event-merge guard-rail failures."""


class SelfMergeError(EventMergeError):
    """canonical and duplicate are the same event."""


def _is_empty(value):
    """Return True when ``value`` counts as an empty/unset event field.

    Empty strings, ``None``, and empty JSON lists/dicts are all "empty" so a
    blank canonical field is filled from the duplicate while a populated one is
    left alone.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


class EventMergePlan:
    """Structured record of what an event merge moves / fills / retires."""

    def __init__(self, canonical, duplicate, *, dry_run):
        self.canonical_id = canonical.pk
        self.canonical_title = canonical.title
        self.duplicate_id = duplicate.pk
        self.duplicate_title = duplicate.title
        self.dry_run = dry_run
        self.already_merged = False
        # list of {user_id, action: 'moved'|'kept_canonical', registered_at}
        self.registrations = []
        self.registrations_moved = 0
        self.registrations_deduped = 0
        # field -> value filled from the duplicate
        self.fields_filled = {}
        self.workshop_relinked = False
        self.duplicate_retired = False

    def to_dict(self):
        return {
            "canonical_id": self.canonical_id,
            "canonical_title": self.canonical_title,
            "duplicate_id": self.duplicate_id,
            "duplicate_title": self.duplicate_title,
            "dry_run": self.dry_run,
            "already_merged": self.already_merged,
            "registrations": self.registrations,
            "registrations_moved": self.registrations_moved,
            "registrations_deduped": self.registrations_deduped,
            "fields_filled": sorted(self.fields_filled.keys()),
            "workshop_relinked": self.workshop_relinked,
            "duplicate_retired": self.duplicate_retired,
        }


def find_duplicate_event_pairs():
    """Return candidate ``(studio_event, github_workshop_event)`` pairs.

    A candidate pair is two events with the SAME normalized title on the SAME
    calendar day where one is ``origin='studio'`` and the other is
    ``origin='github', kind='workshop'``. Reuses #880's title normalizer so the
    match key is identical to the dedup-on-sync heuristic.

    Pairs where the github event is already retired (cancelled + unpublished) are
    EXCLUDED — they have already been merged and should not clutter the review
    list. Returns a list of ``(canonical, duplicate)`` tuples, canonical first.
    """
    from integrations.services.github_sync.dispatchers.workshops import (
        _normalize_title_for_match,
    )

    studio_events = list(
        Event.objects.filter(origin="studio").annotate(
            start_date=TruncDate("start_datetime", tzinfo=dt.timezone.utc),
        )
    )
    github_events = list(
        Event.objects.filter(origin="github", kind="workshop")
        .exclude(status="cancelled", published=False)
        .annotate(
            start_date=TruncDate("start_datetime", tzinfo=dt.timezone.utc),
        )
    )

    # Index studio events by (date, normalized title).
    studio_index = {}
    for event in studio_events:
        key = (event.start_date, _normalize_title_for_match(event.title))
        if not key[1]:
            continue
        studio_index.setdefault(key, []).append(event)

    pairs = []
    for github_event in github_events:
        key = (
            github_event.start_date,
            _normalize_title_for_match(github_event.title),
        )
        if not key[1]:
            continue
        candidates = studio_index.get(key)
        # Only surface unambiguous 1:1 pairs — refuse to guess when more than one
        # studio event shares the title+date (mirrors #880's ambiguity refusal).
        if candidates and len(candidates) == 1:
            pairs.append((candidates[0], github_event))
    return pairs


def _is_already_merged(canonical, duplicate):
    """Return True when the pair is already merged.

    The duplicate is retired (cancelled + unpublished) and no workshop points at
    it. Re-running the merge on such a pair is a no-op.
    """
    from content.models import Workshop

    duplicate_retired = (
        duplicate.status == "cancelled" and not duplicate.published
    )
    workshop_on_duplicate = Workshop.objects.filter(event=duplicate).exists()
    return duplicate_retired and not workshop_on_duplicate


def merge_duplicate_events(
    canonical, duplicate, *, actor_label, actor=None, dry_run=True
):
    """Fold ``duplicate`` into ``canonical``, returning an :class:`EventMergePlan`.

    Runs inside a single ``transaction.atomic`` block. On ``dry_run`` the whole
    algorithm runs against the real DB and is then rolled back, so a preview
    persists nothing. On a real merge a single ``CommunityAuditLog`` row is
    written (when ``actor`` is a staff ``User`` — the audit row's ``user`` FK
    records who ran the merge). The management command may pass ``actor=None``;
    the audit summary is then only logged, not row-written. An already-merged
    pair short-circuits to a no-op plan.
    """
    if canonical.pk == duplicate.pk:
        raise SelfMergeError("Cannot merge an event into itself.")

    plan = EventMergePlan(canonical, duplicate, dry_run=dry_run)
    plan._actor = actor

    if _is_already_merged(canonical, duplicate):
        plan.already_merged = True
        return plan

    with transaction.atomic():
        _carry_registrations(canonical, duplicate, plan)
        _carry_content(canonical, duplicate, plan)
        _relink_workshop(canonical, duplicate, plan)
        _retire_duplicate(duplicate, plan)

        if dry_run:
            transaction.set_rollback(True)
        else:
            _write_audit(plan, canonical, actor_label)

    return plan


def _carry_registrations(canonical, duplicate, plan):
    """Move ``EventRegistration`` rows from duplicate to canonical, de-duped.

    A user registered on both keeps the canonical row, back-dated to the earliest
    ``registered_at`` of the two; the duplicate's row is deleted. A user only on
    the duplicate is repointed to the canonical event.
    """
    canonical_by_user = {
        reg.user_id: reg
        for reg in EventRegistration.objects.filter(event=canonical)
    }

    for dup_reg in EventRegistration.objects.filter(event=duplicate):
        existing = canonical_by_user.get(dup_reg.user_id)
        if existing is None:
            # Repoint to canonical. ``registered_at`` is auto_now_add, so a plain
            # update() preserves the stored timestamp (no save() re-stamp).
            EventRegistration.objects.filter(pk=dup_reg.pk).update(
                event=canonical
            )
            plan.registrations_moved += 1
            plan.registrations.append(
                {
                    "user_id": dup_reg.user_id,
                    "action": "moved",
                    "registered_at": dup_reg.registered_at.isoformat(),
                }
            )
        else:
            # De-dup: keep canonical, but preserve the earliest registered_at.
            earliest = min(existing.registered_at, dup_reg.registered_at)
            if earliest != existing.registered_at:
                EventRegistration.objects.filter(pk=existing.pk).update(
                    registered_at=earliest
                )
            EventRegistration.objects.filter(pk=dup_reg.pk).delete()
            plan.registrations_deduped += 1
            plan.registrations.append(
                {
                    "user_id": dup_reg.user_id,
                    "action": "kept_canonical",
                    "registered_at": earliest.isoformat(),
                }
            )


def _carry_content(canonical, duplicate, plan):
    """Copy recording / content / zoom fields into EMPTY canonical fields only."""
    changed = False
    for field in _CARRY_FIELDS:
        if not _is_empty(getattr(canonical, field)):
            continue
        dup_value = getattr(duplicate, field)
        if _is_empty(dup_value):
            continue
        setattr(canonical, field, dup_value)
        plan.fields_filled[field] = dup_value
        changed = True
    if changed:
        canonical.save()


def _relink_workshop(canonical, duplicate, plan):
    """Point any ``Workshop.event`` referencing the duplicate at the canonical.

    Uses ``update()`` so we don't re-run ``Workshop.save()`` (and its markdown
    render pipeline). The OneToOne is on the duplicate side; after the merge it
    must resolve to the canonical event so a re-sync (with #879/#880) finds and
    links to it instead of resurrecting the github row.
    """
    from content.models import Workshop

    workshops = Workshop.objects.filter(event=duplicate)
    count = workshops.count()
    if count:
        workshops.update(event=canonical)
        plan.workshop_relinked = True


def _retire_duplicate(duplicate, plan):
    """Cancel + unpublish the duplicate (no hard delete, #864).

    Uses ``update()`` to avoid re-running ``Event.save()`` — the github row is
    untouched on origin/source_repo, so the invariant is preserved either way,
    but update() keeps the retire a pure status flip.

    Resurrection guard (#881 §3): the github duplicate was minted with
    ``slug == workshop.slug``. The no-reference re-sync resolver
    (``_link_or_create_workshop_event``) resolves by ``Event.objects.filter(
    slug=workshop.slug)`` BEFORE falling through to #880's title+date heuristic.
    If we leave the retired duplicate holding that slug, the next sync would
    re-link the workshop to the retired row and undo the merge. We free the slug
    (suffix ``-merged-dup-<pk>``) so the slug-match misses the retired row and
    the heuristic links the workshop to the surviving canonical event instead.
    """
    retired_slug = f"{duplicate.slug}-merged-dup-{duplicate.pk}"
    # Cap to the model's slug length so the retire never trips a DB constraint.
    retired_slug = retired_slug[:300]
    Event.objects.filter(pk=duplicate.pk).update(
        status="cancelled",
        published=False,
        published_at=None,
        slug=retired_slug,
    )
    duplicate.status = "cancelled"
    duplicate.published = False
    duplicate.published_at = None
    duplicate.slug = retired_slug
    plan.duplicate_retired = True


def _write_audit(plan, canonical, actor_label):
    """Write exactly one CommunityAuditLog row summarizing the merge.

    There is no natural user SUBJECT for an event merge, so the ``user`` FK
    records the staff operator who ran it (passed via ``actor``). When no actor
    is available (e.g. the management command), we skip the FK-bound row but the
    summary is still logged.
    """
    summary = plan.to_dict()
    summary["actor_token"] = actor_label
    actor = getattr(plan, "_actor", None)
    if actor is None:
        logger.info("merge_events (no actor): %s", json.dumps(summary, default=str))
        return
    CommunityAuditLog.objects.create(
        user=actor,
        action=AUDIT_ACTION,
        details=json.dumps(summary, default=str),
    )
    plan.audit_written = True
