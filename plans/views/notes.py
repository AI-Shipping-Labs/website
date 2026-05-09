"""Owner-only CRUD endpoints for participant week notes (issue #499).

A "participant note" is a member-authored ``WeekNote`` row attached
to one ``Week`` of one ``Plan``. They are deliberately distinct from
``InterviewNote`` records, which are staff-authored interview /
intake notes that must NEVER appear on member-facing surfaces.

URLs follow the sprint-scoped no-trailing-slash convention used
elsewhere in ``plans/urls.py``:

- ``POST /sprints/<slug>/plan/<plan_id>/weeks/<week_id>/notes`` -- create a
  note on the given week. Owner-only. Empty body returns HTTP 400 and
  does not create a row.
- ``POST /sprints/<slug>/plan/<plan_id>/week-notes/<note_id>`` (with
  ``_method=patch``) or ``PATCH ...`` -- update the body of a note.
  Author-only. Empty body returns 400.
- ``POST /sprints/<slug>/plan/<plan_id>/week-notes/<note_id>/delete`` or
  ``DELETE ...`` -- delete the note. Author-only.

Cross-plan / cross-author / non-owner attempts always return 404,
not 403, so plan IDs do not leak through error codes (matches the
style already used by :func:`my_plan_detail`).

Form posts redirect back to the sprint-scoped owner workspace with a
flash message; AJAX (``X-Requested-With: XMLHttpRequest``) callers get
JSON. The Playwright test exercises the redirect path because that is
what the textarea/button UI actually does.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from plans.models import Plan, Week, WeekNote


def _is_ajax(request) -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _owned_plan_or_404(plan_id, sprint_slug, user) -> Plan:
    """Return ``plan_id`` if it exists AND ``user`` is the owner.

    Other ownership states (different member, anonymous, missing) all
    surface as 404 -- matching ``my_plan_detail`` so the existence of
    plan IDs stays opaque.
    """
    return get_object_or_404(
        Plan.objects.filter(pk=plan_id, member=user, sprint__slug=sprint_slug),
    )


def _owner_workspace_url(plan: Plan) -> str:
    return reverse(
        'my_plan_detail',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )


def _serialize_note(note: WeekNote) -> dict:
    return {
        'id': note.pk,
        'week_id': note.week_id,
        'body': note.body,
        'created_at': note.created_at.isoformat(),
        'updated_at': note.updated_at.isoformat(),
    }


@login_required
@require_http_methods(['POST'])
def week_note_create(request, sprint_slug, plan_id, week_id):
    """Create a ``WeekNote`` on ``week_id``. Owner-only."""
    plan = _owned_plan_or_404(plan_id, sprint_slug, request.user)
    week = get_object_or_404(Week.objects.filter(pk=week_id, plan=plan))

    body = (request.POST.get('body') or '').strip()
    if not body:
        if _is_ajax(request):
            return JsonResponse(
                {'error': 'Body is required'}, status=400,
            )
        return HttpResponseBadRequest('Note body is required.')

    note = WeekNote.objects.create(
        week=week,
        body=body,
        author=request.user,
    )

    if _is_ajax(request):
        return JsonResponse(_serialize_note(note), status=201)

    messages.success(request, 'Note added.')
    return redirect(_owner_workspace_url(plan) + f'#week-{week.pk}')


@login_required
@require_http_methods(['POST', 'PATCH'])
def week_note_update(request, sprint_slug, plan_id, note_id):
    """Update the body of a participant ``WeekNote``. Author-only.

    Cross-author edits (a non-author plan owner trying to mutate a
    teammate's note) surface as 404. Note authorship is currently
    pinned to the plan owner -- the create endpoint refuses to let
    anyone else create a note on someone else's plan -- but we still
    re-check ``note.author_id == request.user.id`` in case the model
    is ever invoked from another surface.
    """
    plan = _owned_plan_or_404(plan_id, sprint_slug, request.user)
    note = _resolve_note_or_404(plan, note_id, request.user)

    body = _extract_body(request)
    if body is None:
        if _is_ajax(request):
            return JsonResponse(
                {'error': 'Body is required'}, status=400,
            )
        return HttpResponseBadRequest('Note body is required.')

    note.body = body
    note.save(update_fields=['body', 'updated_at'])

    if _is_ajax(request):
        return JsonResponse(_serialize_note(note))

    messages.success(request, 'Note updated.')
    return redirect(_owner_workspace_url(plan) + f'#week-{note.week_id}')


@login_required
@require_http_methods(['POST', 'DELETE'])
def week_note_delete(request, sprint_slug, plan_id, note_id):
    """Delete a participant ``WeekNote``. Author-only."""
    plan = _owned_plan_or_404(plan_id, sprint_slug, request.user)
    note = _resolve_note_or_404(plan, note_id, request.user)

    week_id = note.week_id
    note.delete()

    if _is_ajax(request):
        return JsonResponse({'ok': True})

    messages.success(request, 'Note deleted.')
    return redirect(_owner_workspace_url(plan) + f'#week-{week_id}')


def _resolve_note_or_404(plan: Plan, note_id, user) -> WeekNote:
    """Return ``note_id`` only if it belongs to ``plan`` AND ``user``."""
    note = (
        WeekNote.objects
        .filter(pk=note_id, week__plan=plan)
        .select_related('week')
        .first()
    )
    if note is None:
        raise Http404('Note not found')
    if note.author_id != user.id:
        # Non-author: hide existence to mirror ``my_plan_detail``.
        raise Http404('Note not found')
    return note


def _extract_body(request) -> str | None:
    """Return the trimmed body for an update request, or ``None`` if blank.

    Accepts both classic form posts (``application/x-www-form-urlencoded``)
    and JSON (for AJAX). PATCH requests come in with the body in
    ``request.body`` even when the content type is form-encoded
    because Django's ``request.POST`` only populates on POST. Reading
    both paths means the same view services either method.
    """
    if request.method == 'POST':
        # Standard form post -- ``request.POST`` already decoded the
        # body; further reads of ``request.body`` would raise
        # ``RawPostDataException``. Treat blank as "no body" and stop
        # here (the JSON branch only matters for AJAX clients which
        # send a non-form content type).
        body = (request.POST.get('body') or '').strip()
        return body or None
    # PATCH / DELETE come in without ``request.POST`` populated. Read
    # ``request.body`` directly and try urlencoded then JSON.
    raw = request.body or b''
    if not raw:
        return None
    text = raw.decode('utf-8', errors='replace')
    # Try urlencoded first.
    parsed = parse_qs(text, keep_blank_values=True)
    if 'body' in parsed:
        body = (parsed['body'][0] or '').strip()
        if body:
            return body
    # JSON fall-back for AJAX clients.
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        body = (data.get('body') or '').strip()
        if body:
            return body
    return None


@login_required
def week_note_methods_405(request, *args, **kwargs):
    """Reject GET on the note endpoints with a clear 405."""
    return HttpResponseNotAllowed(['POST', 'PATCH', 'DELETE'])
