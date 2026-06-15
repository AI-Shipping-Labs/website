"""Member-facing onboarding flow (issue #802).

A dedicated, idempotent, resumable ``/onboarding/`` flow surfaced by a
dashboard prompt banner. Two member-facing steps:

1. Self-identification — "How do you identify yourself?" with archetype
   descriptions (never internal persona names).
2. Fill-in / submit — reuses #803's shared ``questionnaires`` fill-in
   renderer keyed by the member's onboarding ``Response``.

No signup-flow changes and no new model field: onboarding completion is
derived from a submitted ``purpose='onboarding'`` ``Response``.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from community.models import CallHost
from crm.services.onboarding_notify import notify_staff_onboarding_submitted
from questionnaires.models import OnboardingConversation, Persona, Response
from questionnaires.onboarding import (
    ai_onboarding_available,
    can_access_onboarding,
    get_generic_onboarding_questionnaire,
    get_onboarding_response,
    reroute_onboarding_response,
    resolve_target_questionnaire,
    self_identification_options,
)
from questionnaires.services import (
    AnswerSaveError,
    build_response_form_rows,
    build_response_questions,
    find_unanswered_required,
    save_response_answers,
)


def _onboarding_gate_redirect(request):
    """Return a redirect to ``/`` when the member cannot access onboarding.

    Issue #982: onboarding is a paid-member benefit. An authenticated user
    whose effective tier is below ``LEVEL_BASIC`` (Free base, no active
    override) is sent back to the dashboard with an info message rather than
    being rendered the flow or hitting a 403/500. Returns ``None`` when the
    member is eligible, so the caller proceeds normally. Anonymous users are
    handled earlier by ``@login_required``.
    """
    if can_access_onboarding(request.user):
        return None
    messages.info(request, 'Onboarding is part of a paid membership.')
    return redirect('/')


@login_required
def onboarding_start(request):
    """Self-identification step (GET).

    Resumes the fill-in step when the member already has a draft
    onboarding response, and shows a completion confirmation when they
    have already submitted one. Otherwise renders the self-ID question.

    A member with a DRAFT response may return here with ``?change=1`` to
    re-open the self-ID picker and pick a different persona (#822); the
    current selection is pre-indicated. A SUBMITTED response always shows
    the completion confirmation — switching persona after submit stays a
    staff action.
    """
    gate = _onboarding_gate_redirect(request)
    if gate is not None:
        return gate

    existing = get_onboarding_response(request.user)

    if existing is not None and existing.status == 'submitted':
        return _render_onboarding_complete(request, existing)

    ai_available = ai_onboarding_available()
    wants_change = request.GET.get('change') == '1'

    if existing is not None and not wants_change:
        # Draft in flight, no explicit "change description" request: if the
        # member started the AI chat, resume it; otherwise resume the form
        # fill-in.
        if ai_available and OnboardingConversation.objects.filter(
            response=existing,
        ).exists():
            return redirect('onboarding_chat')
        return redirect('onboarding_questions')

    if existing is None:
        # No response yet: offer the conversational AI flow when available;
        # otherwise the form-first self-ID step (#802) unchanged.
        if ai_available:
            return redirect('onboarding_chat')

    options = self_identification_options()
    generic = get_generic_onboarding_questionnaire()
    # "Ready" means at least the generic questionnaire (the universal
    # fallback) is seeded; without it no selection can be satisfied.
    onboarding_ready = generic is not None or bool(options[:-2])
    return render(request, 'accounts/onboarding_start.html', {
        'options': options,
        'onboarding_ready': onboarding_ready,
        'current_selection': _current_self_id(existing),
        'is_changing': existing is not None,
    })


def _current_self_id(response):
    """The self-ID value matching ``response``'s questionnaire, or ``''``.

    Maps the draft's current onboarding questionnaire back to the self-ID
    option value so the picker can pre-select it. A persona questionnaire
    maps to that persona's pk; the generic fallback has no single value
    (it serves both "none" and "more than one"), so it returns ``''`` and
    no persona option is highlighted.
    """
    if response is None:
        return ''
    persona = (
        Persona.objects
        .filter(default_questionnaire=response.questionnaire, is_active=True)
        .first()
    )
    return str(persona.pk) if persona is not None else ''


@login_required
@require_POST
def onboarding_identify(request):
    """Record the self-ID selection and route to the fill-in step.

    Resolves the selection to a target onboarding questionnaire,
    ``get_or_create``s the member's response, materializes its question
    set, and redirects to the shared fill-in page.

    A member with a DRAFT response may re-pick a different persona (#822):
    the draft is re-routed to the new questionnaire, preserving answers to
    shared common-spine questions (matched by prompt). A SUBMITTED response
    is locked — switching persona after submit stays a staff action.
    """
    gate = _onboarding_gate_redirect(request)
    if gate is not None:
        return gate

    existing = get_onboarding_response(request.user)
    if existing is not None and existing.status == 'submitted':
        return redirect('onboarding_start')

    selection = (request.POST.get('self_id') or '').strip()
    target = resolve_target_questionnaire(selection)
    if target is None:
        return render(request, 'accounts/onboarding_start.html', {
            'options': self_identification_options(),
            'onboarding_ready': False,
            'current_selection': _current_self_id(existing),
            'is_changing': existing is not None,
        }, status=200)

    if existing is not None:
        # Re-pick from a draft: repoint the existing response to the newly
        # chosen questionnaire, preserving shared-spine answers by prompt.
        reroute_onboarding_response(existing, target)
        return redirect('onboarding_questions')

    response, _created = Response.objects.get_or_create(
        questionnaire=target,
        respondent=request.user,
        defaults={'status': 'draft'},
    )
    build_response_questions(response)
    return redirect('onboarding_questions')


def _get_member_onboarding_response_or_404(request, response_id):
    """Fetch the member's own onboarding response or 404.

    A member can only reach their own onboarding response; another
    member's response (or any non-onboarding response) is a 404, so no
    answers leak across members.
    """
    return get_object_or_404(
        Response.objects.select_related('questionnaire'),
        pk=response_id,
        respondent=request.user,
        questionnaire__purpose='onboarding',
    )


def _read_only_rows(response):
    """Build read-only Q&A rows for a submitted onboarding response."""
    answers_by_question = {
        a.question_id: a
        for a in response.answers.prefetch_related('selected_options').all()
    }
    rows = []
    for rq in response.response_questions.all():
        answer = answers_by_question.get(rq.pk)
        rows.append({
            'question': rq,
            'answer': answer,
            'is_answered': answer is not None and answer.display_value != '',
        })
    return rows


def _founder_booking_urls():
    """Founder booking URLs for the completion screen CTAs (#951).

    Reads the per-founder scheduler links from the Studio-editable
    ``CallHost`` store (the same source ``/request-a-call`` uses) rather
    than forking them into IntegrationSetting keys. A blank ``booking_url``
    yields ``''`` so the template hides that founder's CTA cleanly.
    """
    by_slug = {
        host.slug: (host.booking_url or '')
        for host in CallHost.objects.filter(slug__in=['valeria', 'alexey'])
    }
    return {
        'valeria_booking_url': by_slug.get('valeria', ''),
        'alexey_booking_url': by_slug.get('alexey', ''),
    }


def _render_onboarding_complete(request, response):
    """Render the end-of-onboarding completion screen for a submitted response.

    Shared by the self-ID resume path and both finish flows (form submit +
    AI chat). Surfaces the read-only answers plus the founder booking-call
    CTAs (#951).
    """
    context = {
        'response': response,
        'rows': _read_only_rows(response),
    }
    context.update(_founder_booking_urls())
    return render(request, 'accounts/onboarding_complete.html', context)


def _render_onboarding_fill(request, response):
    """Render / handle the fill-in page for an already-resolved response.

    Shared by the id-free member-facing route (:func:`onboarding_fill_current`)
    and the numeric back-compat route (:func:`onboarding_fill`). GET
    pre-fills existing answers; POST upserts answers (save draft). A
    submitted response is read-only — editing after submit is not offered
    to members. The save-draft success redirect lands on the id-free
    member-facing URL so the member never sees the DB id.
    """
    if request.method == 'POST':
        if response.status == 'submitted':
            messages.info(
                request,
                'Your onboarding was already submitted. Contact the team if '
                'you need to change an answer.',
            )
            return redirect('onboarding_start')
        try:
            save_response_answers(response, request.POST)
        except AnswerSaveError as exc:
            form_rows = build_response_form_rows(
                response, post_data=request.POST, field_errors=exc.field_errors,
            )
            return render(request, 'accounts/onboarding_fill.html', {
                'response': response,
                'form_rows': form_rows,
                'error': 'Please fix the highlighted answers.',
            }, status=400)
        messages.success(request, 'Saved. You can come back to finish.')
        return redirect('onboarding_questions')

    if response.status == 'submitted':
        return redirect('onboarding_start')

    form_rows = build_response_form_rows(response)
    return render(request, 'accounts/onboarding_fill.html', {
        'response': response,
        'form_rows': form_rows,
        'error': '',
    })


@login_required
def onboarding_fill_current(request, response_id=None):
    """Id-free member-facing fill-in page (``/onboarding/questions``).

    Resolves the *requester's own* onboarding ``Response`` server-side via
    :func:`get_onboarding_response`, so there is no DB id in the URL the
    member sees and no way to address another member's response. When the
    member has no onboarding draft yet, send them to self-identification
    rather than a 404 dead end. A ``response_id`` is accepted but ignored —
    the response is always the requester's own.
    """
    gate = _onboarding_gate_redirect(request)
    if gate is not None:
        return gate

    response = get_onboarding_response(request.user)
    if response is None:
        return redirect('onboarding_start')
    return _render_onboarding_fill(request, response)


@login_required
def onboarding_fill(request, response_id):
    """Numeric back-compat fill-in route (``/onboarding/<id>``).

    Member-facing links now use the id-free ``/onboarding/questions``
    route; this remains as a ``respondent``-scoped back-compat endpoint so
    bookmarked numeric URLs keep working. It is NOT a security boundary
    relaxation: :func:`_get_member_onboarding_response_or_404` 404s any
    response that is not the requester's own onboarding response.
    """
    gate = _onboarding_gate_redirect(request)
    if gate is not None:
        return gate

    response = _get_member_onboarding_response_or_404(request, response_id)
    return _render_onboarding_fill(request, response)


@login_required
@require_POST
def onboarding_submit(request, response_id):
    """Validate required answers, mark submitted, redirect with thanks."""
    gate = _onboarding_gate_redirect(request)
    if gate is not None:
        return gate

    response = _get_member_onboarding_response_or_404(request, response_id)

    if response.status == 'submitted':
        messages.info(request, 'Your onboarding was already submitted.')
        return redirect('onboarding_start')

    try:
        save_response_answers(response, request.POST)
    except AnswerSaveError as exc:
        form_rows = build_response_form_rows(
            response, post_data=request.POST, field_errors=exc.field_errors,
        )
        return render(request, 'accounts/onboarding_fill.html', {
            'response': response,
            'form_rows': form_rows,
            'error': 'Please fix the highlighted answers.',
        }, status=400)

    missing = find_unanswered_required(response)
    if missing:
        prompts = ', '.join(rq.prompt for rq in missing)
        form_rows = build_response_form_rows(response)
        return render(request, 'accounts/onboarding_fill.html', {
            'response': response,
            'form_rows': form_rows,
            'error': f'Please answer the required question(s): {prompts}',
        }, status=400)

    response.mark_submitted()
    notify_staff_onboarding_submitted(request.user)
    messages.success(
        request,
        "Thanks — we'll use this to prepare your plan.",
    )
    # Land on the end-of-onboarding completion screen (#951) so the new
    # member sees the founder booking CTAs. ``onboarding_start`` renders the
    # completion screen for a submitted response.
    return redirect('onboarding_start')
