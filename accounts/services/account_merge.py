"""Account-merge engine (issue #841 / slice 840b).

Consolidate a *secondary* (merged-in) ``accounts.User`` into a *canonical*
(surviving) one: repoint every owned row, reconcile scalar profile / entitlement
fields by precedence, record the secondary email as an ``EmailAlias`` of
canonical (so future relay / billing webhooks route correctly via #840a's
resolver), then deactivate the secondary login. Irreversible data movement plus
billing, so the whole thing runs inside one ``transaction.atomic()`` and ships a
mandatory ``dry_run`` plan as the safety net.

Design notes
------------

The repoint pass walks ``user._meta.get_fields()`` (the SAME enumeration as
``accounts/tasks/purge_unverified_users.py:92``), so a plain ``ForeignKey(User)``
added by any future app is repointed without editing this engine.

Uniqueness collisions are NOT hard-coded as a static table. For each reverse
relation we DERIVE the unique key(s) that include the user field from the
model's ``unique_together`` and ``UniqueConstraint``s (honouring partial-index
conditions). When canonical already owns a row with the same key values
(excluding the user column), we keep canonical's row and drop / skip secondary's.
A few relations need bespoke logic and are listed in ``_SPECIAL_STRATEGIES``
(O2O on the PK, O2O on a non-PK column, the more-complete tie-break for course
progress). A relation that is neither a plain repointable FK nor handled here
raises rather than silently corrupting -- fail loud.

``dry_run`` runs the WHOLE algorithm against the real DB and then calls
``transaction.set_rollback(True)`` so nothing persists; the alias-creation and
audit-write steps are additionally skipped so a dry run is a guaranteed no-op
even if the outer transaction were ever committed by mistake.
"""

import json
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import EmailAlias, TierOverride
from accounts.services.email_resolution import normalize_email
from accounts.utils.tags import normalize_tags
from community.models import CommunityAuditLog

logger = logging.getLogger(__name__)

User = get_user_model()

AUDIT_ACTION = "merge_accounts"


# --------------------------------------------------------------------------- #
# Typed errors -> HTTP status codes in the view.
# --------------------------------------------------------------------------- #
class MergeError(Exception):
    """Base class for merge guard-rail failures."""


class SelfMergeError(MergeError):
    """canonical and secondary are the same account (-> 400)."""


class SubscriptionConflictError(MergeError):
    """Both sides have a distinct live subscription and force is False (-> 409)."""

    def __init__(self, canonical_sub, secondary_sub):
        self.canonical_sub = canonical_sub
        self.secondary_sub = secondary_sub
        super().__init__(
            "Both accounts have a live subscription; refusing to drop a paid "
            "sub without force."
        )


class StaffMergeRefused(MergeError):
    """Either side is staff / superuser and force is False (-> 409)."""


# --------------------------------------------------------------------------- #
# MergePlan: the structured summary returned to the view (serialized 1:1).
# --------------------------------------------------------------------------- #
class MergePlan:
    """Structured record of what a merge moved / reconciled.

    Built up by :func:`merge_accounts` and returned to the view. ``to_dict``
    is the wire shape documented in the issue.
    """

    def __init__(self, canonical, secondary, *, dry_run):
        self.canonical_email = canonical.email
        self.merge_email = secondary.email
        self.dry_run = dry_run
        self.already_merged = False
        self.moved = []  # list of {model, field, moved, dropped?, kept_canonical?, added?}
        self.reconciled = {}  # field -> change summary
        self.tier_overrides = {"deactivated": [], "kept_active": None}
        self.stripe = {}
        self.conflicts = []
        self.alias_created = None
        self.secondary_deactivated = False

    def record_move(self, model_label, field, **counts):
        entry = {"model": model_label, "field": field}
        entry.update(counts)
        self.moved.append(entry)

    def to_dict(self):
        return {
            "canonical_email": self.canonical_email,
            "merge_email": self.merge_email,
            "dry_run": self.dry_run,
            "already_merged": self.already_merged,
            "moved": self.moved,
            "reconciled": self.reconciled,
            "tier_overrides": self.tier_overrides,
            "stripe": self.stripe,
            "conflicts": self.conflicts,
            "alias_created": self.alias_created,
            "secondary_deactivated": self.secondary_deactivated,
        }


# --------------------------------------------------------------------------- #
# Special per-(model, field) collision strategies. Everything NOT listed here is
# handled generically: plain repoint, or generic unique-key collision keep-
# canonical-drop-secondary derived from the model's unique constraints.
# --------------------------------------------------------------------------- #
def _strategy_user_attribution(plan, related_model, field_name, canonical, secondary):
    """analytics.UserAttribution: O2O where ``user`` IS the PK.

    The PK is the user, so the row cannot be repointed. Keep canonical's if it
    exists; otherwise recreate canonical's attribution from secondary's field
    values. Either way delete secondary's.
    """
    sec = related_model.objects.filter(pk=secondary.pk).first()
    if sec is None:
        return
    canon = related_model.objects.filter(pk=canonical.pk).first()
    moved = 0
    if canon is None:
        # Recreate from values (PK FK means we cannot UPDATE the pk in place).
        values = {}
        for f in related_model._meta.fields:
            if f.primary_key:
                continue
            if f.name == "user":
                continue
            values[f.attname] = getattr(sec, f.attname)
        related_model.objects.create(user=canonical, **values)
        moved = 1
    sec.delete()
    plan.record_move(
        related_model._meta.label, field_name, moved=moved, dropped=1 - moved,
    )


def _strategy_crm_record(plan, related_model, field_name, canonical, secondary):
    """crm.CRMRecord: O2O on a non-PK ``user`` column.

    If canonical has none, repoint secondary's. If both exist, copy non-empty
    fields from secondary into canonical's record where canonical's are blank
    (field-level merge), then delete secondary's.
    """
    sec = related_model.objects.filter(user=secondary).first()
    if sec is None:
        return
    canon = related_model.objects.filter(user=canonical).first()
    if canon is None:
        sec.user = canonical
        sec.save(update_fields=["user"])
        plan.record_move(related_model._meta.label, field_name, moved=1)
        return

    filled = []
    update_fields = []
    for f in related_model._meta.fields:
        if f.primary_key or f.name in ("user", "created_at", "updated_at"):
            continue
        canon_val = getattr(canon, f.attname)
        sec_val = getattr(sec, f.attname)
        # Only fill canonical's BLANK fields from secondary's non-blank values.
        if (canon_val in (None, "")) and (sec_val not in (None, "")):
            setattr(canon, f.attname, sec_val)
            update_fields.append(f.name)
            filled.append(f.name)
    if update_fields:
        canon.save(update_fields=update_fields)
    sec.delete()
    plan.record_move(
        related_model._meta.label,
        field_name,
        moved=0,
        dropped=1,
        fields_filled=filled,
    )


def _strategy_course_progress(plan, related_model, field_name, canonical, secondary):
    """content.UserCourseProgress (user, unit): keep the MORE-COMPLETE row.

    Per unit: if canonical already has a row, keep it; but if canonical's is
    incomplete (``completed_at`` is None) and secondary's is complete, copy
    secondary's completion onto canonical's row. Drop secondary's colliding row.
    Non-colliding rows are repointed.
    """
    moved = 0
    dropped = 0
    canon_units = {
        p.unit_id: p for p in related_model.objects.filter(user=canonical)
    }
    for sec_row in related_model.objects.filter(user=secondary):
        canon_row = canon_units.get(sec_row.unit_id)
        if canon_row is None:
            sec_row.user = canonical
            sec_row.save(update_fields=["user"])
            canon_units[sec_row.unit_id] = sec_row
            moved += 1
            continue
        # Collision: keep canonical's row but adopt secondary's completion if
        # canonical's is incomplete and secondary's is complete.
        if canon_row.completed_at is None and sec_row.completed_at is not None:
            canon_row.completed_at = sec_row.completed_at
            canon_row.save(update_fields=["completed_at"])
        sec_row.delete()
        dropped += 1
    plan.record_move(related_model._meta.label, field_name, moved=moved, dropped=dropped)


def _strategy_email_address(plan, related_model, field_name, canonical, secondary):
    """allauth ``account.EmailAddress``: REPOINT, never drop verification state.

    The genuine row identity is ``(user, email)`` -- ``email`` names a distinct
    address. The model ALSO carries ``UniqueConstraint(user, primary) WHERE
    primary=True`` which is a per-user STATE invariant (one-primary-per-user),
    NOT a row identity. The generic walker mis-read that as a drop key and
    deleted the secondary's ``primary=True`` row on collision -- destroying the
    verification record for exactly the address being aliased to canonical.

    Correct handling (matches the spec table "REPOINT, skip rows whose email
    already exists on canonical"): repoint each secondary row whose ``email`` is
    not already owned by canonical; demote secondary's ``primary`` flag to
    ``False`` BEFORE repointing so canonical keeps its single primary and the
    one-primary-per-user index never sees two. Rows whose ``email`` canonical
    already owns are dropped (true ``(user, email)`` duplicate -- same address).
    """
    canon_emails = set(
        related_model.objects.filter(**{field_name: canonical}).values_list(
            "email", flat=True
        )
    )
    moved = 0
    dropped = 0
    for row in related_model.objects.filter(**{field_name: secondary}):
        if row.email in canon_emails:
            # True duplicate of the same address already verified on canonical.
            row.delete()
            dropped += 1
            continue
        update_fields = [field_name]
        if row.primary:
            # Demote: canonical keeps its single primary; preserve the row.
            row.primary = False
            update_fields.append("primary")
        setattr(row, field_name, canonical)
        row.save(update_fields=update_fields)
        canon_emails.add(row.email)
        moved += 1
    plan.record_move(related_model._meta.label, field_name, moved=moved, dropped=dropped)


def _strategy_email_log(plan, related_model, field_name, canonical, secondary):
    """``email_app.EmailLog``: PLAIN REPOINT -- preserve ALL delivery history.

    The spec table classifies ``EmailLog`` as plain repoint (delivery history).
    The model carries ``UniqueConstraint(campaign, user) WHERE campaign IS NOT
    NULL`` which the generic drop-walker would otherwise read as a drop key,
    silently deleting a real campaign-send record on collision.

    The DB partial index physically forbids two ``(same campaign, same user)``
    rows, so on the (anomalous) case where canonical already has a log for the
    SAME campaign as a secondary row, we still must not drop the secondary's
    delivery record. We repoint it but NULL its ``campaign`` FK so the row (its
    ``sent_at`` / ``ses_message_id`` / open + click history) survives without
    violating the partial unique index. The common case -- distinct campaigns or
    ``campaign IS NULL`` -- is a straight bulk repoint.
    """
    canon_campaigns = set(
        related_model.objects.filter(
            **{field_name: canonical}, campaign__isnull=False
        ).values_list("campaign_id", flat=True)
    )
    moved = 0
    for row in related_model.objects.filter(**{field_name: secondary}):
        update_fields = [field_name]
        if row.campaign_id is not None and row.campaign_id in canon_campaigns:
            # Same (campaign, user) would collide on canonical: keep the row as
            # history by clearing its campaign FK rather than dropping it.
            row.campaign = None
            update_fields.append("campaign")
        elif row.campaign_id is not None:
            canon_campaigns.add(row.campaign_id)
        setattr(row, field_name, canonical)
        row.save(update_fields=update_fields)
        moved += 1
    if moved:
        plan.record_move(related_model._meta.label, field_name, moved=moved)


def _strategy_enrollment(plan, related_model, field_name, canonical, secondary):
    """content.Enrollment: partial-unique (user, course) WHERE unenrolled_at IS NULL.

    Only ACTIVE enrollments collide. If canonical already has an active
    enrollment for a course and secondary also has one, soft-unenrol
    secondary's BEFORE repointing (so the partial index never sees two actives),
    then repoint it (history preserved, no IntegrityError). Inactive secondary
    rows and courses canonical lacks are repointed as-is.
    """
    now = timezone.now()
    canonical_active_courses = set(
        related_model.objects.filter(
            user=canonical, unenrolled_at__isnull=True
        ).values_list("course_id", flat=True)
    )
    moved = 0
    dropped = 0
    for sec_row in related_model.objects.filter(user=secondary):
        if (
            sec_row.unenrolled_at is None
            and sec_row.course_id in canonical_active_courses
        ):
            # Soft-unenrol secondary's active dup BEFORE repointing.
            sec_row.unenrolled_at = now
            sec_row.user = canonical
            sec_row.save(update_fields=["unenrolled_at", "user"])
            dropped += 1
        else:
            sec_row.user = canonical
            sec_row.save(update_fields=["user"])
            if sec_row.unenrolled_at is None:
                canonical_active_courses.add(sec_row.course_id)
            moved += 1
    plan.record_move(related_model._meta.label, field_name, moved=moved, dropped=dropped)


# Keyed by ``(app_label.ModelName, field_name)``.
#
# These cover the cases the generic unique-key walker cannot express correctly:
# the O2O collisions, the more-complete tie-break, the partial-active enrollment,
# and -- critically -- the two models whose ``user``-bearing unique constraint is
# NOT a row-identity drop key (allauth ``EmailAddress``'s per-user ``primary``
# STATE invariant, and ``EmailLog``'s campaign-history index the spec wants
# preserved as plain repoint). See ``_unique_keys_for`` for the general
# state-flag classification that backstops anything not enumerated here.
_SPECIAL_STRATEGIES = {
    ("analytics.UserAttribution", "user"): _strategy_user_attribution,
    ("crm.CRMRecord", "user"): _strategy_crm_record,
    ("content.UserCourseProgress", "user"): _strategy_course_progress,
    ("content.Enrollment", "user"): _strategy_enrollment,
    ("account.EmailAddress", "user"): _strategy_email_address,
    ("email_app.EmailLog", "user"): _strategy_email_log,
}


# --------------------------------------------------------------------------- #
# Generic unique-key collision handling.
# --------------------------------------------------------------------------- #
def _is_row_identity_key(related_model, other_fields):
    """Decide whether ``other_fields`` (the non-user part of a unique key) names
    a distinct ENTITY -- making the constraint a legitimate row-identity drop
    key -- rather than a per-user STATE/STATUS flag.

    A unique constraint like ``(user, course)`` or ``(user, email)`` identifies a
    distinct owned row: ``course`` is an entity FK, ``email`` a natural-key
    address. Dropping secondary's colliding row on merge is correct (canonical
    already owns the same entity).

    A constraint like ``(user, primary) WHERE primary=True`` is the OPPOSITE: a
    one-primary-per-user invariant. ``primary`` is a boolean STATE flag, not an
    entity. Treating it as a drop key destroys real rows (allauth's verification
    record). Such a constraint must NEVER drive a drop -- the owning model gets
    an explicit REPOINT strategy in ``_SPECIAL_STRATEGIES`` instead.

    Rule: the key is a row identity only if EVERY non-user field is either a
    relation (FK to another entity) or a non-boolean concrete field. If ANY
    non-user field is a ``BooleanField`` (a pure state flag), the constraint is
    a state invariant and is excluded from the drop walker.
    """
    if not other_fields:
        return False
    for fname in other_fields:
        try:
            f = related_model._meta.get_field(fname)
        except Exception:
            # Unknown field name -> be conservative, do not treat as drop key.
            return False
        if f.is_relation:
            continue
        if f.get_internal_type() == "BooleanField":
            return False
    return True


def _unique_keys_for(related_model, field_name):
    """Return the row-identity unique key field-tuples that include ``field_name``.

    Combines ``Meta.unique_together`` and ``Meta.constraints`` (UniqueConstraint)
    whose field set contains the user FK. Each returned entry is
    ``(other_fields, condition)`` where ``other_fields`` excludes the user
    column (the part we compare on) and ``condition`` is a Q for partial
    constraints (or None). A model with no such key has no collision risk.

    Constraints whose non-user fields are pure STATE flags (e.g. a boolean
    ``primary`` / ``verified``) are EXCLUDED: they are per-user invariants, not
    row identities, and must be reconciled (via an explicit strategy), never
    used to drop rows. See ``_is_row_identity_key``.
    """
    keys = []
    for combo in related_model._meta.unique_together:
        if field_name in combo:
            others = tuple(f for f in combo if f != field_name)
            if _is_row_identity_key(related_model, others):
                keys.append((others, None))
    for constraint in related_model._meta.constraints:
        if constraint.__class__.__name__ != "UniqueConstraint":
            continue
        if field_name not in constraint.fields:
            continue
        others = tuple(f for f in constraint.fields if f != field_name)
        if not _is_row_identity_key(related_model, others):
            continue
        keys.append((others, getattr(constraint, "condition", None)))
    return keys


def _repoint_with_unique_keys(plan, related_model, field_name, keys, canonical, secondary):
    """Repoint secondary's rows, dropping any that would collide on a unique key.

    For each secondary row: if canonical already owns a row matching ANY derived
    unique key (same ``other_fields`` values, satisfying the partial condition
    where present), DROP the secondary row. Otherwise repoint it. Keep
    canonical's row on every collision.
    """
    moved = 0
    dropped = 0
    secondary_rows = list(related_model.objects.filter(**{field_name: secondary}))
    for row in secondary_rows:
        collides = False
        for others, condition in keys:
            lookup = {}
            for f in others:
                model_field = related_model._meta.get_field(f)
                if model_field.is_relation:
                    lookup[f + "_id"] = getattr(row, f + "_id")
                else:
                    lookup[f] = getattr(row, f)
            qs = related_model.objects.filter(**{field_name: canonical}, **lookup)
            if condition is not None:
                # Only collide when BOTH the canonical candidate AND this row
                # satisfy the partial condition (e.g. unenrolled_at IS NULL).
                if not _row_matches_condition(row, condition):
                    continue
                qs = qs.filter(condition)
            if qs.exists():
                collides = True
                break
        if collides:
            row.delete()
            dropped += 1
        else:
            setattr(row, field_name, canonical)
            row.save(update_fields=[field_name])
            moved += 1
    # ``kept_canonical`` == the count of canonical rows we kept on collision
    # (one per dropped secondary duplicate). Mirrors the documented plan shape
    # ``{model, field, moved, dropped, kept_canonical}``.
    plan.record_move(
        related_model._meta.label,
        field_name,
        moved=moved,
        dropped=dropped,
        kept_canonical=dropped,
    )


def _row_matches_condition(row, condition):
    """Best-effort check that a fetched row satisfies a simple Q condition.

    The conditions in use are flat ``isnull`` / equality checks on the row's own
    fields, so we evaluate the Q children directly. Unknown / nested conditions
    fall back to True (treat as colliding) to stay safe.
    """
    if not isinstance(condition, Q):
        return True
    for child in condition.children:
        if isinstance(child, Q):
            if not _row_matches_condition(row, child):
                return False
            continue
        key, expected = child
        if "__" in key:
            field, lookup = key.rsplit("__", 1)
        else:
            field, lookup = key, "exact"
        value = getattr(row, field, getattr(row, field + "_id", None))
        if lookup == "isnull":
            if (value is None) != bool(expected):
                return False
        elif lookup == "exact":
            if value != expected:
                return False
        else:
            return True
    return True


# --------------------------------------------------------------------------- #
# Repoint pass over all reverse relations.
# --------------------------------------------------------------------------- #
def _repoint_relations(plan, canonical, secondary):
    """Walk reverse relations and repoint / reconcile each (issue #841)."""
    for field in canonical._meta.get_fields():
        if field.many_to_many:
            _repoint_m2m(plan, field, canonical, secondary)
            continue
        if not field.auto_created:
            continue
        if not (field.one_to_many or field.one_to_one):
            continue

        related_model = field.related_model
        field_name = field.remote_field.name
        model_label = related_model._meta.label

        special = _SPECIAL_STRATEGIES.get((model_label, field_name))
        if special is not None:
            special(plan, related_model, field_name, canonical, secondary)
            continue

        if field.one_to_one:
            # A non-special O2O on the user (PK or column) is unsafe to repoint
            # blindly -- fail loud so a future O2O is classified explicitly.
            raise MergeError(
                f"Unhandled one-to-one relation {model_label}.{field_name}; "
                "add an explicit collision strategy to account_merge."
            )

        keys = _unique_keys_for(related_model, field_name)
        if keys:
            _repoint_with_unique_keys(
                plan, related_model, field_name, keys, canonical, secondary
            )
        else:
            moved = related_model.objects.filter(**{field_name: secondary}).update(
                **{field_name: canonical}
            )
            if moved:
                plan.record_move(model_label, field_name, moved=moved)


def _repoint_m2m(plan, field, canonical, secondary):
    """Union secondary's M2M set into canonical's, then clear secondary's.

    Handles M2M declared ON User (``groups``, ``user_permissions``) via the
    field name, and reverse M2M (none on User today) via the accessor name.
    """
    if field.auto_created:
        accessor = field.get_accessor_name()
    else:
        accessor = field.name
    canon_manager = getattr(canonical, accessor)
    sec_manager = getattr(secondary, accessor)
    sec_objs = list(sec_manager.all())
    if not sec_objs:
        return
    existing = set(canon_manager.values_list("pk", flat=True))
    added = [o for o in sec_objs if o.pk not in existing]
    if added:
        canon_manager.add(*added)
    sec_manager.clear()
    plan.record_move(
        field.related_model._meta.label, accessor, added=len(added),
    )


# --------------------------------------------------------------------------- #
# Scalar reconciliation.
# --------------------------------------------------------------------------- #
_BOUNCE_RANK = {
    User.BounceState.NONE: 0,
    User.BounceState.SOFT: 1,
    User.BounceState.PERMANENT: 2,
}


def _reconcile_scalars(plan, canonical, secondary):
    """Reconcile canonical <- secondary scalar fields by groomed precedence."""
    rec = plan.reconciled

    # tier: higher level wins.
    canon_level = canonical.tier.level if canonical.tier_id else 0
    sec_level = secondary.tier.level if secondary.tier_id else 0
    if sec_level > canon_level:
        rec["tier"] = {
            "from": canonical.tier.slug if canonical.tier_id else None,
            "to": secondary.tier.slug,
            "source": "secondary",
        }
        canonical.tier = secondary.tier

    # Billing identifiers: move secondary's only when canonical has no sub.
    if not canonical.subscription_id and secondary.subscription_id:
        if secondary.stripe_customer_id:
            canonical.stripe_customer_id = secondary.stripe_customer_id
        canonical.subscription_id = secondary.subscription_id
        canonical.billing_period_end = secondary.billing_period_end
        canonical.pending_tier = secondary.pending_tier
        plan.stripe = {
            "subscription_moved": secondary.subscription_id,
            "customer_moved": secondary.stripe_customer_id,
        }

    # email_verified / account_activated: OR.
    if secondary.email_verified and not canonical.email_verified:
        canonical.email_verified = True
        rec["email_verified"] = {"to": True}
    if secondary.account_activated and not canonical.account_activated:
        canonical.account_activated = True
        rec["account_activated"] = {"to": True}

    # tags: union, normalized.
    before = list(canonical.tags or [])
    unioned = normalize_tags(before + list(secondary.tags or []))
    added = [t for t in unioned if t not in before]
    if added:
        canonical.tags = unioned
        rec["tags"] = {"added": added}

    # Slack: keep canonical unless empty/falsey, then take secondary's (id +
    # checked_at carried together with the membership flag).
    if not canonical.slack_member and secondary.slack_member:
        canonical.slack_member = True
        canonical.slack_user_id = secondary.slack_user_id
        canonical.slack_checked_at = secondary.slack_checked_at
        rec["slack_member"] = {"to": True}
    elif not canonical.slack_user_id and secondary.slack_user_id:
        canonical.slack_user_id = secondary.slack_user_id
        if not canonical.slack_checked_at:
            canonical.slack_checked_at = secondary.slack_checked_at

    # Simple "keep canonical unless empty" string fields.
    for fname in ("first_name", "last_name", "preferred_timezone", "theme_preference"):
        if not getattr(canonical, fname) and getattr(secondary, fname):
            setattr(canonical, fname, getattr(secondary, fname))

    # import_metadata: shallow-merge (canonical keys win).
    if secondary.import_metadata:
        merged = dict(secondary.import_metadata)
        merged.update(canonical.import_metadata or {})
        canonical.import_metadata = merged

    # Bounce state: keep the WORSE of the two (a known-bad address stays bad).
    if _BOUNCE_RANK.get(secondary.bounce_state, 0) > _BOUNCE_RANK.get(
        canonical.bounce_state, 0
    ):
        canonical.bounce_state = secondary.bounce_state
        canonical.bounce_recorded_at = secondary.bounce_recorded_at
        canonical.last_bounce_diagnostic = secondary.last_bounce_diagnostic
        rec["bounce_state"] = {"to": canonical.bounce_state}
    canonical.soft_bounce_count = max(
        canonical.soft_bounce_count, secondary.soft_bounce_count
    )

    # unsubscribed: OR.
    if secondary.unsubscribed and not canonical.unsubscribed:
        canonical.unsubscribed = True
        rec["unsubscribed"] = {"to": True}

    canonical.save()


# --------------------------------------------------------------------------- #
# TierOverride reconciliation (one-active invariant).
# --------------------------------------------------------------------------- #
def _reconcile_tier_overrides(plan, canonical):
    """Collapse canonical's active overrides to ONE and revoke redundant ones."""
    now = timezone.now()
    active = list(
        TierOverride.objects.filter(
            user=canonical, is_active=True, expires_at__gt=now
        ).select_related("override_tier")
    )
    if not active:
        return

    # Among all actives, pick the keeper: highest override level, tie-break on
    # later expiry.
    def sort_key(o):
        return (o.override_tier.level, o.expires_at)

    active.sort(key=sort_key, reverse=True)
    keeper = active[0]
    for other in active[1:]:
        other.is_active = False
        other.save(update_fields=["is_active"])
        plan.tier_overrides["deactivated"].append(
            {
                "id": other.id,
                "override_tier": other.override_tier.slug,
                "reason": "superseded_by_higher_override",
            }
        )

    # Redundant-after-paid: if canonical's real tier now meets or exceeds the
    # surviving override's level, the override is courtesy fat -- revoke it
    # (mirrors ``_apply_stripe_subscription_tier``).
    canon_level = canonical.tier.level if canonical.tier_id else 0
    if keeper.override_tier.level <= canon_level:
        keeper.is_active = False
        keeper.save(update_fields=["is_active"])
        plan.tier_overrides["deactivated"].append(
            {
                "id": keeper.id,
                "override_tier": keeper.override_tier.slug,
                "reason": "redundant_after_paid",
            }
        )
    else:
        plan.tier_overrides["kept_active"] = {
            "id": keeper.id,
            "override_tier": keeper.override_tier.slug,
        }


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #
def merge_accounts(
    canonical, secondary, *, actor_label, actor=None, dry_run=False, force=False
):
    """Merge ``secondary`` into ``canonical`` and return a :class:`MergePlan`.

    ``actor_label`` is the short string attributed in the audit ``details``;
    ``actor`` (optional) is the operator ``User`` recorded as the alias
    ``created_by``.

    The whole operation runs in one ``transaction.atomic()``. On ``dry_run`` the
    real algorithm executes against the real DB and is then rolled back via
    ``transaction.set_rollback(True)``; the alias-create and audit-write steps
    are additionally skipped, so a dry run is a guaranteed no-op.

    Raises ``SelfMergeError`` / ``SubscriptionConflictError`` /
    ``StaffMergeRefused`` (the view maps them to 400 / 409).
    """
    plan = MergePlan(canonical, secondary, dry_run=dry_run)

    with transaction.atomic():
        # --- Guard rails -------------------------------------------------- #
        if canonical.pk == secondary.pk:
            raise SelfMergeError("Cannot merge an account into itself.")

        # Idempotent no-op: secondary already a deactivated alias of canonical.
        sec_email_norm = normalize_email(secondary.email)
        if (
            not secondary.is_active
            and EmailAlias.objects.filter(
                user=canonical, email=sec_email_norm
            ).exists()
        ):
            plan.already_merged = True
            return plan

        if (canonical.is_staff or canonical.is_superuser
                or secondary.is_staff or secondary.is_superuser) and not force:
            raise StaffMergeRefused(
                "Refusing to merge into / from a staff account without force."
            )

        canon_sub = canonical.subscription_id or ""
        sec_sub = secondary.subscription_id or ""
        if canon_sub and sec_sub and canon_sub != sec_sub:
            if not force:
                raise SubscriptionConflictError(canon_sub, sec_sub)
            # force: keep canonical's sub, record the dropped one.
            plan.conflicts.append(
                {
                    "type": "dual_subscription",
                    "kept_subscription_id": canon_sub,
                    "dropped_subscription_id": sec_sub,
                }
            )

        # --- Repoint owned rows ------------------------------------------- #
        _repoint_relations(plan, canonical, secondary)

        # --- Scalar reconciliation ---------------------------------------- #
        _reconcile_scalars(plan, canonical, secondary)

        # --- TierOverride one-active invariant ---------------------------- #
        _reconcile_tier_overrides(plan, canonical)

        # --- Alias + deactivate secondary (mutations skipped on dry_run) -- #
        if not dry_run:
            _register_alias(plan, canonical, secondary, actor)
            # Clear secondary's billing identifiers that moved to canonical so a
            # future webhook can't resolve the dead row ahead of canonical.
            secondary.is_active = False
            if plan.stripe.get("subscription_moved"):
                secondary.stripe_customer_id = ""
                secondary.subscription_id = ""
            secondary.save(
                update_fields=["is_active", "stripe_customer_id", "subscription_id"]
            )
            plan.secondary_deactivated = True

            _write_audit(plan, canonical, actor_label)

        if dry_run:
            transaction.set_rollback(True)

    return plan


def _register_alias(plan, canonical, secondary, actor):
    """Record secondary's email as an EmailAlias of canonical (reuse #840a path).

    Stores the email NORMALIZED (the #840a tester flagged that app-layer
    normalization is the only enforcement when writing the alias row directly
    via the ORM).
    """
    normalized = normalize_email(secondary.email)
    if not normalized:
        return
    # Skip if it's already a primary email of canonical (impossible post-guard)
    # or already an alias of canonical.
    if normalized == normalize_email(canonical.email):
        return
    if EmailAlias.objects.filter(user=canonical, email=normalized).exists():
        plan.alias_created = normalized
        return
    EmailAlias.objects.create(
        user=canonical,
        email=normalized,
        source=EmailAlias.SOURCE_MERGE,
        note=f"Account merge: {secondary.email} -> {canonical.email}",
        created_by=actor,
    )
    plan.alias_created = normalized


def _write_audit(plan, canonical, actor_label):
    """Write exactly one CommunityAuditLog row summarizing the merge."""
    summary = plan.to_dict()
    summary["actor_token"] = actor_label
    CommunityAuditLog.objects.create(
        user=canonical,
        action=AUDIT_ACTION,
        details=json.dumps(summary, default=str),
    )
