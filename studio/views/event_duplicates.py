"""Studio UI for the duplicate-event merge engine (issue #881).

A staff-only, server-rendered surface on top of
``events.services.event_merge`` so an operator can fold an already-duplicated
event pair (a ``origin='studio'`` event and the ``origin='github',
kind='workshop'`` sync artifact for the same session) into a single surviving
event, with a MANDATORY preview before the irreversible commit.

Mirrors ``studio/views/merge.py`` (account merge, #842): we call the service
directly (no HTTP API), and the destructive Confirm step is guarded by a signed
``confirm_token`` over ``(canonical_pk, duplicate_pk)`` (Django ``signing.dumps``,
10 min TTL). Confirm recomputes that token from the posted pks and refuses any
mismatch — so a tampered hidden field, a stale form, or an expired preview routes
the operator back to the review list instead of merging the wrong rows. We carry
pks (not titles) because the merge cancels + unpublishes the duplicate, so
re-resolving by title+date could load the wrong row.

Dry-run stays a guaranteed no-op: the engine runs the whole algorithm then
``transaction.set_rollback(True)`` and skips the audit write, so Preview persists
nothing. Confirm passes ``dry_run=False`` and the engine owns the single
``CommunityAuditLog`` write.

Three views:

- ``GET  /studio/events/duplicates/``         -> :func:`event_duplicates_list`
- ``POST /studio/events/duplicates/preview``  -> :func:`event_duplicates_preview`
- ``POST /studio/events/duplicates/confirm``  -> :func:`event_duplicates_confirm`
"""

from django.core import signing
from django.shortcuts import render

from events.models import Event
from events.services.event_merge import (
    SelfMergeError,
    find_duplicate_event_pairs,
    merge_duplicate_events,
)
from studio.decorators import staff_required

# Salt + TTL for the preview->confirm signed token.
_CONFIRM_SALT = "studio.event_merge.confirm"
_CONFIRM_MAX_AGE = 600  # 10 minutes


def _sign_pair(canonical_pk, duplicate_pk):
    """Sign the reviewed ``(canonical_pk, duplicate_pk)`` pair."""
    return signing.dumps(
        {"canonical_pk": canonical_pk, "duplicate_pk": duplicate_pk},
        salt=_CONFIRM_SALT,
    )


def _verify_pair(token, canonical_pk, duplicate_pk):
    """Return True iff ``token`` is a valid, unexpired signature of the pair."""
    try:
        payload = signing.loads(token, salt=_CONFIRM_SALT, max_age=_CONFIRM_MAX_AGE)
    except signing.BadSignature:
        return False
    return (
        payload.get("canonical_pk") == canonical_pk
        and payload.get("duplicate_pk") == duplicate_pk
    )


def _actor_label(request):
    return f"studio:{request.user.email}"


def _candidate_rows():
    """Return render-friendly candidate rows for the duplicates list."""
    rows = []
    for canonical, duplicate in find_duplicate_event_pairs():
        rows.append(
            {
                "canonical": canonical,
                "duplicate": duplicate,
                "canonical_registrations": canonical.registration_count,
                "duplicate_registrations": duplicate.registration_count,
            }
        )
    return rows


@staff_required
def event_duplicates_list(request):
    """``GET /studio/events/duplicates/`` -- the review list of candidate pairs."""
    ctx = {
        "candidates": _candidate_rows(),
        "errors": {},
        "plan": None,
        "result": None,
        "confirm_token": None,
        "canonical_id": None,
        "duplicate_id": None,
    }
    return render(request, "studio/events/duplicates.html", ctx)


def _resolve_pair(canonical_id, duplicate_id):
    """Resolve both ids to events, returning ``(canonical, duplicate, errors)``."""
    errors = {}
    try:
        canonical_pk = int(canonical_id)
        duplicate_pk = int(duplicate_id)
    except (TypeError, ValueError):
        return None, None, {"resolve": "Pick a candidate pair to merge."}

    canonical = Event.objects.filter(pk=canonical_pk).first()
    duplicate = Event.objects.filter(pk=duplicate_pk).first()
    if canonical is None or duplicate is None:
        errors["resolve"] = "One of the events no longer exists."
        return None, None, errors
    if canonical.pk == duplicate.pk:
        errors["resolve"] = "Cannot merge an event into itself."
        return None, None, errors
    return canonical, duplicate, {}


@staff_required
def event_duplicates_preview(request):
    """``POST .../preview`` -- dry-run the merge and render the FULL plan inline."""
    canonical, duplicate, errors = _resolve_pair(
        request.POST.get("canonical_id"), request.POST.get("duplicate_id")
    )
    ctx = {
        "candidates": _candidate_rows(),
        "errors": errors,
        "plan": None,
        "result": None,
        "confirm_token": None,
        "canonical_id": request.POST.get("canonical_id"),
        "duplicate_id": request.POST.get("duplicate_id"),
    }
    if errors:
        return render(request, "studio/events/duplicates.html", ctx)

    try:
        plan = merge_duplicate_events(
            canonical,
            duplicate,
            actor_label=_actor_label(request),
            actor=request.user,
            dry_run=True,
        )
    except SelfMergeError:
        ctx["errors"] = {"resolve": "Cannot merge an event into itself."}
        return render(request, "studio/events/duplicates.html", ctx)

    ctx["plan"] = plan.to_dict()
    ctx["preview_canonical"] = canonical
    ctx["preview_duplicate"] = duplicate
    ctx["canonical_id"] = canonical.pk
    ctx["duplicate_id"] = duplicate.pk
    ctx["confirm_token"] = _sign_pair(canonical.pk, duplicate.pk)
    return render(request, "studio/events/duplicates.html", ctx)


@staff_required
def event_duplicates_confirm(request):
    """``POST .../confirm`` -- execute the real merge on the previewed pair."""
    try:
        canonical_pk = int(request.POST.get("canonical_id", ""))
        duplicate_pk = int(request.POST.get("duplicate_id", ""))
    except (TypeError, ValueError):
        return _confirm_expired(request)

    token = request.POST.get("confirm_token", "")
    if not _verify_pair(token, canonical_pk, duplicate_pk):
        return _confirm_expired(request)

    canonical = Event.objects.filter(pk=canonical_pk).first()
    duplicate = Event.objects.filter(pk=duplicate_pk).first()
    if canonical is None or duplicate is None:
        return _confirm_expired(request)

    try:
        plan = merge_duplicate_events(
            canonical,
            duplicate,
            actor_label=_actor_label(request),
            actor=request.user,
            dry_run=False,
        )
    except SelfMergeError:
        return _confirm_expired(request)

    ctx = {
        "candidates": _candidate_rows(),
        "errors": {},
        "plan": None,
        "result": plan.to_dict(),
        "result_canonical": canonical,
        "confirm_token": None,
        "canonical_id": None,
        "duplicate_id": None,
    }
    return render(request, "studio/events/duplicates.html", ctx)


def _confirm_expired(request):
    """Route a stale/tampered confirm back to the review list with a message."""
    ctx = {
        "candidates": _candidate_rows(),
        "errors": {
            "confirm": "This merge preview expired or changed. Re-run the preview."
        },
        "plan": None,
        "result": None,
        "confirm_token": None,
        "canonical_id": None,
        "duplicate_id": None,
    }
    return render(request, "studio/events/duplicates.html", ctx)
