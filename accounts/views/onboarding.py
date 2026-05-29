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

from questionnaires.models import OnboardingConversation, Response
from questionnaires.onboarding import (
    ai_onboarding_available,
    get_generic_onboarding_questionnaire,
    get_onboarding_response,
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


@login_required
def onboarding_start(request):
    """Self-identification step (GET).

    Resumes the fill-in step when the member already has a draft
    onboarding response, and shows a completion confirmation when they
    have already submitted one. Otherwise renders the self-ID question.
    """
    existing = get_onboarding_response(request.user)

    if existing is not None and existing.status == 'submitted':
        rows = _read_only_rows(existing)
        return render(request, 'accounts/onboarding_complete.html', {
            'response': existing,
            'rows': rows,
        })

    ai_available = ai_onboarding_available()

    if existing is not None:
        # Draft in flight. If the member started the AI chat, resume it;
        # otherwise resume the form fill-in. Never re-ask self-ID.
        if ai_available and OnboardingConversation.objects.filter(
            response=existing,
        ).exists():
            return redirect('onboarding_chat')
        return redirect('onboarding_fill', response_id=existing.pk)

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
    })


@login_required
@require_POST
def onboarding_identify(request):
    """Record the self-ID selection and route to the fill-in step.

    Resolves the selection to a target onboarding questionnaire,
    ``get_or_create``s the member's response, materializes its question
    set, and redirects to the shared fill-in page.
    """
    # A member who already has a response never re-runs self-ID.
    existing = get_onboarding_response(request.user)
    if existing is not None:
        if existing.status == 'submitted':
            return redirect('onboarding_start')
        return redirect('onboarding_fill', response_id=existing.pk)

    selection = (request.POST.get('self_id') or '').strip()
    target = resolve_target_questionnaire(selection)
    if target is None:
        return render(request, 'accounts/onboarding_start.html', {
            'options': self_identification_options(),
            'onboarding_ready': False,
        }, status=200)

    response, _created = Response.objects.get_or_create(
        questionnaire=target,
        respondent=request.user,
        defaults={'status': 'draft'},
    )
    build_response_questions(response)
    return redirect('onboarding_fill', response_id=response.pk)


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


@login_required
def onboarding_fill(request, response_id):
    """Member fill-in / save page for their own onboarding response.

    Reuses the shared ``questionnaires`` renderer. GET pre-fills existing
    answers; POST upserts answers (save draft). A submitted response is
    read-only — editing after submit is not offered to members.
    """
    response = _get_member_onboarding_response_or_404(request, response_id)

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
        return redirect('onboarding_fill', response_id=response.pk)

    if response.status == 'submitted':
        return redirect('onboarding_start')

    form_rows = build_response_form_rows(response)
    return render(request, 'accounts/onboarding_fill.html', {
        'response': response,
        'form_rows': form_rows,
        'error': '',
    })


@login_required
@require_POST
def onboarding_submit(request, response_id):
    """Validate required answers, mark submitted, redirect with thanks."""
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
    messages.success(
        request,
        "Thanks — we'll use this to prepare your plan.",
    )
    # The authenticated dashboard is the ``home`` view's logged-in branch.
    return redirect('home')
