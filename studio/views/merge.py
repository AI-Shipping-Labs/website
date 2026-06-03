"""Studio UI for the account-merge engine (issue #842 / slice 840c).

A staff-only, server-rendered surface on top of
``accounts.services.account_merge.merge_accounts`` so an operator can merge two
accounts from a screen, with a MANDATORY dry-run preview before the irreversible
commit. Mirrors the other in-process Studio views (``tier_overrides.py`` /
``users.py``): we call ``merge_accounts(...)`` directly, NOT the token-gated HTTP
API in ``api/views/user_merge.py``.

Three views:

- ``GET  /studio/users/merge/``         -> :func:`studio_user_merge`
- ``POST /studio/users/merge/preview``  -> :func:`studio_user_merge_preview`
- ``POST /studio/users/merge/confirm``  -> :func:`studio_user_merge_confirm`

Preview -> confirm safety. The destructive Confirm step must act on EXACTLY the
pair the operator previewed. The preview response embeds the resolved pks plus a
signed ``confirm_token`` over ``(canonical_pk, secondary_pk, force)`` (Django
``signing.dumps``, 10 min TTL). Confirm recomputes that token from the posted
pks/force and refuses any mismatch -- so a tampered hidden field, a stale form,
or an expired preview routes the operator back to step 1 instead of merging the
wrong rows. We carry pks (not emails) because a real merge scrubs the secondary's
email, so re-resolving by email could load the wrong row.

Dry-run stays a guaranteed no-op: the engine runs the whole algorithm against the
real DB then ``transaction.set_rollback(True)`` and skips the alias/audit writes,
so Preview persists nothing. Confirm passes ``dry_run=False`` and the engine owns
the single ``CommunityAuditLog`` + ``EmailAlias`` write; we never duplicate them.
"""

from django.contrib.auth import get_user_model
from django.core import signing
from django.shortcuts import render

from accounts.services.account_merge import (
    SelfMergeError,
    StaffMergeRefused,
    SubscriptionConflictError,
    merge_accounts,
)
from accounts.services.email_resolution import normalize_email
from studio.decorators import staff_required

User = get_user_model()

# Salt + TTL for the preview->confirm signed token.
_CONFIRM_SALT = "studio.user_merge.confirm"
_CONFIRM_MAX_AGE = 600  # 10 minutes


def _find_user(email):
    """Case-insensitive email lookup, mirroring the HTTP API (``user_merge.py``)."""
    if not email:
        return None
    return User.objects.filter(email__iexact=str(email).strip()).first()


def _sign_pair(canonical_pk, secondary_pk, force):
    """Sign the reviewed ``(canonical_pk, secondary_pk, force)`` triple."""
    return signing.dumps(
        {
            "canonical_pk": canonical_pk,
            "secondary_pk": secondary_pk,
            "force": bool(force),
        },
        salt=_CONFIRM_SALT,
    )


def _verify_pair(token, canonical_pk, secondary_pk, force):
    """Return True iff ``token`` is a valid, unexpired signature of the triple."""
    try:
        payload = signing.loads(token, salt=_CONFIRM_SALT, max_age=_CONFIRM_MAX_AGE)
    except signing.BadSignature:
        return False
    return (
        payload.get("canonical_pk") == canonical_pk
        and payload.get("secondary_pk") == secondary_pk
        and bool(payload.get("force")) == bool(force)
    )


def _actor_label(request):
    return f"studio:{request.user.email}"


def _conflict_context(exc):
    """Render-friendly dict for a ``SubscriptionConflictError``."""
    return {
        "type": "subscription",
        "canonical_subscription_id": exc.canonical_sub,
        "merge_subscription_id": exc.secondary_sub,
    }


def _resolve_pair(canonical_email, secondary_email):
    """Resolve both emails to users, returning ``(canonical, secondary, errors)``.

    ``errors`` is a dict of field -> message. The self-merge guard runs on the
    NORMALIZED email BEFORE any lookup, identically to the HTTP API
    (``user_merge.py:209``), so a typo'd same-email request fails fast and the
    same way whether or not the user exists.
    """
    errors = {}
    canonical_email = (canonical_email or "").strip()
    secondary_email = (secondary_email or "").strip()

    if not canonical_email:
        errors["canonical"] = "Enter the canonical (surviving) account email."
    if not secondary_email:
        errors["secondary"] = "Enter the secondary (merged-in) account email."
    if errors:
        return None, None, errors

    if normalize_email(canonical_email) == normalize_email(secondary_email):
        errors["self_merge"] = "Cannot merge an account into itself."
        return None, None, errors

    canonical = _find_user(canonical_email)
    if canonical is None:
        errors["canonical"] = f"No account found for {canonical_email}"
    secondary = _find_user(secondary_email)
    if secondary is None:
        errors["secondary"] = f"No account found for {secondary_email}"
    if errors:
        return None, None, errors

    return canonical, secondary, {}


def _base_context(*, canonical_email="", secondary_email=""):
    return {
        "canonical_email": canonical_email,
        "secondary_email": secondary_email,
        "errors": {},
        "plan": None,
        "conflict": None,
        "confirm_token": None,
        "canonical_user_id": None,
        "secondary_user_id": None,
        "force": False,
        "result": None,
        "already_merged": False,
        "merged_canonical": None,
    }


@staff_required
def studio_user_merge(request):
    """``GET /studio/users/merge/`` -- the merge screen (two pickers, no preview).

    Reads the optional ``?canonical=`` / ``?secondary=`` query params to pre-seed
    the pickers (the user-detail "Merge accounts" action pre-fills canonical).
    """
    ctx = _base_context(
        canonical_email=request.GET.get("canonical", "").strip(),
        secondary_email=request.GET.get("secondary", "").strip(),
    )
    return render(request, "studio/users/merge.html", ctx)


@staff_required
def studio_user_merge_preview(request):
    """``POST .../preview`` -- dry-run the merge and render the FULL plan inline."""
    canonical_email = request.POST.get("canonical_email", "").strip()
    secondary_email = request.POST.get("secondary_email", "").strip()
    force = request.POST.get("force") == "1"

    ctx = _base_context(
        canonical_email=canonical_email, secondary_email=secondary_email
    )
    ctx["force"] = force

    canonical, secondary, errors = _resolve_pair(canonical_email, secondary_email)
    if errors:
        ctx["errors"] = errors
        return render(request, "studio/users/merge.html", ctx)

    try:
        plan = merge_accounts(
            canonical,
            secondary,
            actor_label=_actor_label(request),
            actor=request.user,
            dry_run=True,
            force=force,
        )
    except SelfMergeError:
        ctx["errors"] = {"self_merge": "Cannot merge an account into itself."}
        return render(request, "studio/users/merge.html", ctx)
    except SubscriptionConflictError as exc:
        ctx["conflict"] = _conflict_context(exc)
        ctx["canonical_user_id"] = canonical.pk
        ctx["secondary_user_id"] = secondary.pk
        # Sign the force=True path so Confirm can act on it ONLY after the
        # operator ticks the acknowledgement checkbox (which posts force=1).
        ctx["confirm_token"] = _sign_pair(canonical.pk, secondary.pk, True)
        return render(request, "studio/users/merge.html", ctx)
    except StaffMergeRefused:
        ctx["conflict"] = {"type": "staff"}
        ctx["canonical_user_id"] = canonical.pk
        ctx["secondary_user_id"] = secondary.pk
        ctx["confirm_token"] = _sign_pair(canonical.pk, secondary.pk, True)
        return render(request, "studio/users/merge.html", ctx)

    ctx["plan"] = plan.to_dict()
    ctx["already_merged"] = plan.already_merged
    ctx["canonical_user_id"] = canonical.pk
    ctx["secondary_user_id"] = secondary.pk
    # A clean (non-conflicting) merge signs force=False so the confirm cannot be
    # silently escalated to force; the force checkbox is never shown here.
    ctx["confirm_token"] = _sign_pair(canonical.pk, secondary.pk, False)
    return render(request, "studio/users/merge.html", ctx)


@staff_required
def studio_user_merge_confirm(request):
    """``POST .../confirm`` -- execute the real merge on the previewed pair."""
    try:
        canonical_pk = int(request.POST.get("canonical_user_id", ""))
        secondary_pk = int(request.POST.get("secondary_user_id", ""))
    except (TypeError, ValueError):
        return _confirm_expired(request)

    force = request.POST.get("force") == "1"
    token = request.POST.get("confirm_token", "")

    # Re-derive the signed token over the posted triple and reject any mismatch:
    # tampered hidden fields, a stale form, an expired preview, or a force flip.
    if not _verify_pair(token, canonical_pk, secondary_pk, force):
        return _confirm_expired(request)

    canonical = User.objects.filter(pk=canonical_pk).first()
    secondary = User.objects.filter(pk=secondary_pk).first()
    if canonical is None or secondary is None:
        return _confirm_expired(request)

    ctx = _base_context(
        canonical_email=canonical.email, secondary_email=secondary.email
    )
    ctx["force"] = force
    ctx["canonical_user_id"] = canonical_pk
    ctx["secondary_user_id"] = secondary_pk

    try:
        plan = merge_accounts(
            canonical,
            secondary,
            actor_label=_actor_label(request),
            actor=request.user,
            dry_run=False,
            force=force,
        )
    except SelfMergeError:
        ctx["errors"] = {"self_merge": "Cannot merge an account into itself."}
        return render(request, "studio/users/merge.html", ctx)
    except SubscriptionConflictError as exc:
        # A guard that did not fire at preview time now fires (race). Re-render
        # the conflict so the operator must re-acknowledge force.
        ctx["conflict"] = _conflict_context(exc)
        ctx["confirm_token"] = _sign_pair(canonical.pk, secondary.pk, True)
        return render(request, "studio/users/merge.html", ctx)
    except StaffMergeRefused:
        ctx["conflict"] = {"type": "staff"}
        ctx["confirm_token"] = _sign_pair(canonical.pk, secondary.pk, True)
        return render(request, "studio/users/merge.html", ctx)

    ctx["result"] = plan.to_dict()
    ctx["already_merged"] = plan.already_merged
    ctx["merged_canonical"] = canonical
    return render(request, "studio/users/merge.html", ctx)


def _confirm_expired(request):
    """Route a stale/tampered confirm back to step 1 with a clear message."""
    ctx = _base_context()
    ctx["errors"] = {
        "confirm": (
            "This merge preview expired or changed. Re-run the preview."
        )
    }
    return render(request, "studio/users/merge.html", ctx)
