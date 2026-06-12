"""Studio AI assistant view (issue #872, Phase 1).

A single staff-only page at ``/studio/assistant/`` implementing the
propose -> confirm -> execute loop over exactly two hand-mapped CRM tools.

The model is called only at propose time (in
:func:`studio.services.assistant.propose_action`). At confirm time the
view replays the EXACT reviewed payload that round-trips through the
confirm form's hidden ``payload`` field — the model is never re-invoked.
Whitelist enforcement and member resolution run server-side at BOTH
propose and execute, so a non-whitelisted field or an unknown member is a
visible refusal, never a silent write.
"""

import json

from django.contrib.auth import get_user_model
from django.shortcuts import render

from crm.models import CRMRecord
from integrations.services import llm
from integrations.services.llm import LLMError
from plans.models import (
    KIND_CHOICES,
    VISIBILITY_CHOICES,
    InterviewNote,
)
from studio.decorators import staff_required
from studio.models import AssistantActionLog
from studio.services.assistant import (
    TOOL_ADD_NOTE,
    TOOL_UPDATE_PROFILE,
    AssistantUnavailable,
    WhitelistViolation,
    propose_action,
    validate_profile_keys,
)

User = get_user_model()

_VALID_KINDS = {value for value, _ in KIND_CHOICES}
_VALID_VISIBILITIES = {value for value, _ in VISIBILITY_CHOICES}


def _tool_label(tool_name):
    if tool_name == TOOL_ADD_NOTE:
        return 'Add member note'
    if tool_name == TOOL_UPDATE_PROFILE:
        return 'Update member profile'
    return tool_name or ''


def _resolve_member(email):
    """Resolve a target member by email (case-insensitive)."""
    if not email:
        return None
    return User.objects.filter(email__iexact=email.strip()).first()


def _base_context():
    return {
        'configured': llm.is_enabled(),
        'request_text': '',
        'proposal': None,
        'result': None,
        'error': None,
    }


def _build_proposal_view(proposal):
    """Turn a service proposal into a template-friendly dict + resolve target.

    Returns ``(view_dict, error_message)``. ``error_message`` is set when
    the proposal cannot proceed to a confirmable state (unknown member,
    missing CRM record); in that case ``view_dict`` is None and no confirm
    form is shown.
    """
    email = proposal.payload.get('member_email', '')
    member = _resolve_member(email)
    if member is None:
        return None, (
            f'No member found for "{email}". Please double-check the email '
            f'address — nothing was changed.'
        )

    if proposal.tool_name == TOOL_UPDATE_PROFILE:
        # The assistant does not auto-create CRM tracking in Phase 1.
        if not CRMRecord.objects.filter(user=member).exists():
            return None, (
                f'{member.email} is not tracked in the CRM yet. Use the '
                f'"Track in CRM" button on their profile first — nothing '
                f'was changed.'
            )
        # Re-validate the whitelist at propose time (defense in depth).
        validate_profile_keys(proposal.payload)

    changes = {
        key: value for key, value in proposal.payload.items()
        if key != 'member_email'
    }
    view = {
        'tool_name': proposal.tool_name,
        'tool_label': _tool_label(proposal.tool_name),
        'member_email': member.email,
        'changes': changes,
        # The exact payload the confirm step will replay, round-tripped as
        # JSON in a hidden field. Execute re-validates it server-side.
        'payload_json': json.dumps(proposal.payload),
    }
    return view, None


def _execute_add_note(actor, payload):
    """Create one InterviewNote from the reviewed payload."""
    email = payload.get('member_email', '')
    member = _resolve_member(email)
    if member is None:
        raise ValueError(f'No member found for "{email}".')

    kind = payload.get('kind', 'general')
    if kind not in _VALID_KINDS:
        kind = 'general'
    visibility = payload.get('visibility', 'internal')
    if visibility not in _VALID_VISIBILITIES:
        visibility = 'internal'
    body = (payload.get('body') or '').strip()
    if not body:
        raise ValueError('The note body is empty.')

    InterviewNote.objects.create(
        member=member,
        plan=None,
        visibility=visibility,
        kind=kind,
        body=body,
        created_by=actor,
    )
    return member, f'Added a {kind} note to {member.email}.'


def _execute_update_profile(actor, payload):
    """Update whitelisted CRMRecord fields from the reviewed payload."""
    # Re-validate the whitelist at execute time — never trust the round-trip.
    validate_profile_keys(payload)

    email = payload.get('member_email', '')
    member = _resolve_member(email)
    if member is None:
        raise ValueError(f'No member found for "{email}".')

    record = CRMRecord.objects.filter(user=member).first()
    if record is None:
        raise ValueError(f'{member.email} is not tracked in the CRM.')

    applied = []
    if 'status' in payload:
        status = payload['status']
        if status not in {'active', 'archived'}:
            raise ValueError(f'Invalid status "{status}".')
        record.status = status
        applied.append(f'status -> {status}')
    if 'persona' in payload:
        record.persona = (payload['persona'] or '').strip()[:120]
        applied.append('persona')
    if 'summary' in payload:
        record.summary = (payload['summary'] or '').strip()
        applied.append('summary')
    if 'next_steps' in payload:
        record.next_steps = (payload['next_steps'] or '').strip()
        applied.append('next steps')

    if not applied:
        raise ValueError('No whitelisted fields to update.')

    record.save()
    return member, f'Updated {member.email}: {", ".join(applied)}.'


def _execute(actor, tool_name, payload):
    """Dispatch a confirmed action to its mapped write path.

    Returns ``(member, message)`` on success; raises ``ValueError`` /
    ``WhitelistViolation`` on a refused or failed execute.
    """
    if tool_name == TOOL_ADD_NOTE:
        return _execute_add_note(actor, payload)
    if tool_name == TOOL_UPDATE_PROFILE:
        return _execute_update_profile(actor, payload)
    raise ValueError(f'Unknown tool "{tool_name}".')


@staff_required
def assistant(request):
    """Render and drive the propose -> confirm -> execute loop."""
    context = _base_context()

    if request.method != 'POST':
        return render(request, 'studio/assistant.html', context)

    # Not-configured: submitting must never 500.
    if not context['configured']:
        context['error'] = (
            'The assistant is not configured. Add an LLM API key in '
            'Studio settings to enable it.'
        )
        return render(request, 'studio/assistant.html', context)

    action = request.POST.get('action', 'propose')

    if action == 'confirm':
        return _handle_confirm(request, context)
    return _handle_propose(request, context)


def _handle_propose(request, context):
    request_text = (request.POST.get('request_text') or '').strip()
    context['request_text'] = request_text
    if not request_text:
        context['error'] = 'Please type a request first.'
        return render(request, 'studio/assistant.html', context)

    try:
        proposal = propose_action(request_text)
    except AssistantUnavailable:
        context['configured'] = False
        context['error'] = (
            'The assistant is not configured. Add an LLM API key in '
            'Studio settings to enable it.'
        )
        return render(request, 'studio/assistant.html', context)
    except WhitelistViolation as exc:
        context['error'] = (
            'The proposed change touches a field I am not allowed to '
            f'update ({", ".join(exc.offending_keys)}). Nothing was '
            'changed.'
        )
        return render(request, 'studio/assistant.html', context)
    except LLMError as exc:
        context['error'] = f'The assistant could not respond: {exc}'
        return render(request, 'studio/assistant.html', context)

    if not proposal.is_action:
        context['result'] = {'declined': True, 'message': proposal.message}
        return render(request, 'studio/assistant.html', context)

    view, error = _build_proposal_view(proposal)
    if error is not None:
        context['error'] = error
        return render(request, 'studio/assistant.html', context)

    context['proposal'] = view
    return render(request, 'studio/assistant.html', context)


def _handle_confirm(request, context):
    tool_name = request.POST.get('tool_name', '')
    raw_payload = request.POST.get('payload', '')
    context['request_text'] = (request.POST.get('request_text') or '').strip()

    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
    except (ValueError, TypeError):
        context['error'] = 'The proposed action could not be replayed.'
        return render(request, 'studio/assistant.html', context)

    target_email = payload.get('member_email', '')
    try:
        member, message = _execute(request.user, tool_name, payload)
    except WhitelistViolation as exc:
        context['error'] = (
            'Refused: the action touches a field I am not allowed to '
            f'update ({", ".join(exc.offending_keys)}). Nothing was '
            'changed.'
        )
        AssistantActionLog.objects.create(
            actor=request.user,
            tool_name=tool_name,
            payload=payload,
            target_member=_resolve_member(target_email),
            target_email=target_email,
            outcome=AssistantActionLog.OUTCOME_ERROR,
            message=str(exc),
        )
        return render(request, 'studio/assistant.html', context)
    except ValueError as exc:
        context['error'] = f'Could not complete the action: {exc}'
        AssistantActionLog.objects.create(
            actor=request.user,
            tool_name=tool_name,
            payload=payload,
            target_member=_resolve_member(target_email),
            target_email=target_email,
            outcome=AssistantActionLog.OUTCOME_ERROR,
            message=str(exc),
        )
        return render(request, 'studio/assistant.html', context)

    AssistantActionLog.objects.create(
        actor=request.user,
        tool_name=tool_name,
        payload=payload,
        target_member=member,
        target_email=member.email,
        outcome=AssistantActionLog.OUTCOME_SUCCESS,
        message=message,
    )
    context['result'] = {'declined': False, 'message': message}
    return render(request, 'studio/assistant.html', context)
