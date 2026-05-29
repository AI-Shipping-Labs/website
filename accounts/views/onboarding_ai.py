"""Member-facing AI onboarding chat views (issue #804, non-streaming).

The conversational alternative to #802's form. A thin wrapper over the
pure interview core (:mod:`questionnaires.onboarding_ai`) via the ORM glue
in :mod:`questionnaires.services_onboarding_ai`:

- ``onboarding_chat`` (GET) renders the chat surface for the member's own
  in-progress AI onboarding conversation, seeded with a greeting.
- ``onboarding_chat_message`` (POST) runs one turn (standard request /
  response, NO streaming -- streaming is #806), persists it, and re-renders
  the chat (or redirects on completion).

On ``LLMError`` the member is routed to #802's form fallback for the SAME
onboarding ``Response`` with a friendly message -- never a 500. Internal
persona names are never surfaced.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from integrations.services.llm import LLMError
from questionnaires.onboarding import (
    ai_onboarding_available,
    get_onboarding_response,
)
from questionnaires.services import build_response_questions
from questionnaires.services_onboarding_ai import (
    get_or_create_ai_onboarding_response,
    run_member_turn,
)


def _conversation_messages(conversation):
    """Return the transcript as a list of ``{'role', 'content'}`` dicts."""
    transcript = conversation.transcript
    if not isinstance(transcript, list):
        return []
    return transcript


@login_required
def onboarding_chat(request):
    """Render the member's AI onboarding chat surface (GET).

    Routes away when the AI path is unavailable or the member has already
    submitted onboarding (mirrors #802's resume/complete behavior). Seeds
    the conversation with a deterministic greeting on first visit.
    """
    if not ai_onboarding_available():
        return redirect('onboarding_start')

    existing = get_onboarding_response(request.user)
    if existing is not None and existing.status == 'submitted':
        # Already onboarded: never restart a chat -- show #802's
        # completion confirmation.
        return redirect('onboarding_start')

    response, conversation = get_or_create_ai_onboarding_response(request.user)
    if response is None:
        # No onboarding questionnaire seeded at all: fall back to #802.
        return redirect('onboarding_start')

    # Seed the greeting on a brand-new conversation (no LLM call).
    if not _conversation_messages(conversation):
        run_member_turn(conversation, None)

    return render(request, 'accounts/onboarding_chat.html', {
        'conversation': conversation,
        'response': response,
        'chat_messages': _conversation_messages(conversation),
    })


@login_required
@require_POST
def onboarding_chat_message(request):
    """Run one member turn (POST) and re-render the chat or finish.

    Standard request/response (no streaming). On ``LLMError`` the member
    is routed to the #802 form fallback for the same onboarding response
    with a friendly message and no data loss.
    """
    if not ai_onboarding_available():
        return redirect('onboarding_start')

    existing = get_onboarding_response(request.user)
    if existing is not None and existing.status == 'submitted':
        return redirect('onboarding_start')

    response, conversation = get_or_create_ai_onboarding_response(request.user)
    if response is None:
        return redirect('onboarding_start')

    member_message = (request.POST.get('message') or '').strip()
    if not member_message:
        return render(request, 'accounts/onboarding_chat.html', {
            'conversation': conversation,
            'response': response,
            'chat_messages': _conversation_messages(conversation),
            'error': 'Please type a message.',
        }, status=400)

    try:
        result = run_member_turn(conversation, member_message)
    except LLMError:
        # Graceful fallback: keep the draft response, switch to the #802
        # form for the same response, never a 500.
        build_response_questions(response)
        messages.info(
            request,
            "Our assistant is unavailable right now -- let's switch to a "
            "quick form instead.",
        )
        return redirect('onboarding_fill', response_id=response.pk)

    if result.is_complete:
        messages.success(
            request,
            "Thanks -- we'll use this to prepare your plan.",
        )
        return redirect('home')

    return render(request, 'accounts/onboarding_chat.html', {
        'conversation': conversation,
        'response': response,
        'chat_messages': _conversation_messages(conversation),
    })


__all__ = [
    'onboarding_chat',
    'onboarding_chat_message',
]
