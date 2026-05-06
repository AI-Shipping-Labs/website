"""Studio CRUD for member-scoped InterviewNote rows (issue #459)."""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from plans.models import (
    KIND_CHOICES,
    VISIBILITY_CHOICES,
    InterviewNote,
    Plan,
)
from studio.decorators import staff_required

User = get_user_model()


def _normalize_choice(raw, choices, default):
    valid = {choice[0] for choice in choices}
    if raw in valid:
        return raw
    return default


def _normalize_kind(raw):
    return _normalize_choice(raw, KIND_CHOICES, 'intake')


def _normalize_visibility(raw):
    return _normalize_choice(raw, VISIBILITY_CHOICES, 'internal')


def _plans_for_member(user):
    return (
        Plan.objects
        .filter(member=user)
        .select_related('sprint')
        .order_by('-sprint__start_date', '-created_at')
    )


def _selected_plan_for_member(user, raw_plan_id):
    raw_plan_id = (raw_plan_id or '').strip()
    if not raw_plan_id:
        return None
    if not raw_plan_id.isdigit():
        return None
    return Plan.objects.filter(member=user, pk=int(raw_plan_id)).first()


def _member_detail_anchor(user):
    return f'/studio/users/{user.pk}/#member-notes'


def _render_form(
    request,
    *,
    detail_user,
    note,
    form_action,
    form_data,
    error='',
    status=200,
):
    return render(request, 'studio/users/note_form.html', {
        'detail_user': detail_user,
        'note': note,
        'form_action': form_action,
        'form_data': form_data,
        'member_plans': _plans_for_member(detail_user),
        'kind_choices': KIND_CHOICES,
        'visibility_choices': VISIBILITY_CHOICES,
        'error': error,
    }, status=status)


@staff_required
def member_note_create(request, user_id):
    """Create a member note, optionally tied to one of the member's plans."""
    detail_user = get_object_or_404(User, pk=user_id)
    selected_plan = _selected_plan_for_member(
        detail_user,
        request.GET.get('plan_id', ''),
    )

    if request.method != 'POST':
        return _render_form(
            request,
            detail_user=detail_user,
            note=None,
            form_action='create',
            form_data={
                'kind': 'intake',
                'visibility': 'internal',
                'body': '',
                'plan_id': str(selected_plan.pk) if selected_plan else '',
            },
        )

    form_data = {
        'kind': _normalize_kind(request.POST.get('kind', '')),
        'visibility': _normalize_visibility(request.POST.get('visibility', '')),
        'body': (request.POST.get('body') or '').strip(),
        'plan_id': (request.POST.get('plan_id') or '').strip(),
    }
    if not form_data['body']:
        return _render_form(
            request,
            detail_user=detail_user,
            note=None,
            form_action='create',
            form_data=form_data,
            error='Note body is required.',
            status=400,
        )

    plan = _selected_plan_for_member(detail_user, form_data['plan_id'])
    InterviewNote.objects.create(
        plan=plan,
        member=detail_user,
        kind=form_data['kind'],
        visibility=form_data['visibility'],
        body=form_data['body'],
        created_by=request.user if request.user.is_authenticated else None,
    )
    messages.success(request, 'Member note added.')
    return redirect(_member_detail_anchor(detail_user))


@staff_required
def member_note_edit(request, user_id, note_id):
    """Edit a member note; the URL user id must match the note owner."""
    detail_user = get_object_or_404(User, pk=user_id)
    note = get_object_or_404(
        InterviewNote.objects.select_related('member', 'plan'),
        pk=note_id,
        member=detail_user,
    )

    if request.method != 'POST':
        return _render_form(
            request,
            detail_user=detail_user,
            note=note,
            form_action='edit',
            form_data={
                'kind': note.kind,
                'visibility': note.visibility,
                'body': note.body,
                'plan_id': str(note.plan_id) if note.plan_id else '',
            },
        )

    form_data = {
        'kind': _normalize_choice(request.POST.get('kind', ''), KIND_CHOICES, note.kind),
        'visibility': _normalize_visibility(request.POST.get('visibility', '')),
        'body': (request.POST.get('body') or '').strip(),
        'plan_id': (request.POST.get('plan_id') or '').strip(),
    }
    if not form_data['body']:
        return _render_form(
            request,
            detail_user=detail_user,
            note=note,
            form_action='edit',
            form_data=form_data,
            error='Note body is required.',
            status=400,
        )

    note.plan = _selected_plan_for_member(detail_user, form_data['plan_id'])
    note.kind = form_data['kind']
    note.visibility = form_data['visibility']
    note.body = form_data['body']
    note.save()
    messages.success(request, 'Member note updated.')
    return redirect(_member_detail_anchor(detail_user))


@staff_required
@require_POST
def member_note_delete(request, user_id, note_id):
    """Delete a member note; the URL user id must match the note owner."""
    detail_user = get_object_or_404(User, pk=user_id)
    note = get_object_or_404(InterviewNote, pk=note_id, member=detail_user)
    note.delete()
    messages.success(request, 'Member note deleted.')
    return redirect(_member_detail_anchor(detail_user))
