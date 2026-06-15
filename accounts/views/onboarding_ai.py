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

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from integrations.services.llm import LLMError
from questionnaires.onboarding import (
    ai_onboarding_available,
    ai_onboarding_streaming_enabled,
    can_access_onboarding,
    get_onboarding_response,
)
from questionnaires.services import build_response_questions
from questionnaires.services_onboarding_ai import (
    get_or_create_ai_onboarding_response,
    run_member_turn,
    stream_member_turn,
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
    # Issue #982: onboarding is paid-only. A Free / expired-override member
    # is sent to the dashboard before any conversation is created or any
    # LLM turn runs.
    if not can_access_onboarding(request.user):
        messages.info(request, 'Onboarding is part of a paid membership.')
        return redirect('/')

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
        'streaming_enabled': ai_onboarding_streaming_enabled(),
    })


def _sse(event, data):
    """Format one Server-Sent Event frame (a named event + JSON data)."""
    payload = json.dumps(data)
    return f'event: {event}\ndata: {payload}\n\n'


@login_required
@require_POST
def onboarding_chat_stream(request):
    """Stream one member turn token-by-token over Server-Sent Events (#806).

    Transport-only upgrade of :func:`onboarding_chat_message`. Same access
    control (login-required; the member can only stream into THEIR OWN
    onboarding conversation — there is no conversation id in the URL, so
    another member's conversation is unreachable; an already-submitted
    member is sent to the v1 completion confirmation). The persisted #800
    artifacts are IDENTICAL to the non-streaming path.

    The response is a ``StreamingHttpResponse`` with
    ``Content-Type: text/event-stream`` and anti-buffering headers
    (``Cache-Control: no-cache``, ``X-Accel-Buffering: no``). SSE frames:

    - ``delta``: ``{"text": "..."}`` — the next chunk of assistant text.
    - ``done``: ``{"complete": bool, "redirect": "<url>"|null}`` — the
      turn finished; on completion ``redirect`` is the v1 thank-you
      destination.
    - ``fallback``: ``{"reason": "..."}`` — the stream could not start /
      failed; the client must re-issue the SAME message via the v1
      non-streaming endpoint (``onboarding_chat_message``). No data was
      persisted, so the retry is the first and only write.

    If streaming is disabled, the LLM is unavailable, or the member is
    ineligible, the client should never call this endpoint; we still guard
    here and emit a ``fallback`` so a stray call degrades cleanly.
    """
    # Issue #982: onboarding is paid-only. A Free / expired-override member
    # gets a ``fallback`` frame and no LLM turn runs / nothing is persisted.
    if not can_access_onboarding(request.user):
        return _stream_response(
            iter([_sse('fallback', {'reason': 'not-eligible'})]),
        )

    if not ai_onboarding_streaming_enabled():
        # Streaming off / LLM disabled: tell the client to use the v1 path.
        return _stream_response(
            iter([_sse('fallback', {'reason': 'streaming-disabled'})]),
        )

    existing = get_onboarding_response(request.user)
    if existing is not None and existing.status == 'submitted':
        return _stream_response(
            iter([_sse('done', {
                'complete': True,
                'redirect': reverse('onboarding_start'),
            })]),
        )

    response, conversation = get_or_create_ai_onboarding_response(request.user)
    if response is None:
        return _stream_response(
            iter([_sse('fallback', {'reason': 'no-questionnaire'})]),
        )

    member_message = (request.POST.get('message') or '').strip()
    if not member_message:
        return _stream_response(
            iter([_sse('fallback', {'reason': 'empty-message'})]),
        )

    def event_stream():
        try:
            gen = stream_member_turn(conversation, member_message)
            result = None
            for item in gen:
                # The generator yields str deltas then a final
                # OnboardingTurnResult (the only non-str item).
                if isinstance(item, str):
                    yield _sse('delta', {'text': item})
                else:
                    result = item
        except LLMError:
            # Open/mid-stream failure: nothing persisted. Tell the client
            # to retry the SAME message via the v1 non-streaming endpoint
            # (which routes to the #802 form fallback on a hard LLMError).
            yield _sse('fallback', {'reason': 'stream-error'})
            return
        if result is not None and result.is_complete:
            # The redirect target is the end-of-onboarding completion screen
            # (#951) with the founder booking CTAs. The flash message cannot
            # be set here (the streaming response headers are already sent),
            # so the completion screen carries its own thank-you copy and the
            # submitted state is the durable signal -- the persisted
            # artifacts match the non-streaming path either way.
            yield _sse('done', {
                'complete': True,
                'redirect': reverse('onboarding_start'),
            })
        else:
            yield _sse('done', {'complete': False, 'redirect': None})

    return _stream_response(event_stream())


def _stream_response(generator):
    """Wrap a generator in an SSE ``StreamingHttpResponse`` with headers."""
    resp = StreamingHttpResponse(
        generator, content_type='text/event-stream',
    )
    # Discourage proxy buffering so deltas arrive incrementally. nginx
    # honours X-Accel-Buffering; CloudFront may still buffer (the client
    # fallback is the safety net) -- see _docs/integrations/llm.md.
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@login_required
@require_POST
def onboarding_chat_message(request):
    """Run one member turn (POST) and re-render the chat or finish.

    Standard request/response (no streaming). On ``LLMError`` the member
    is routed to the #802 form fallback for the same onboarding response
    with a friendly message and no data loss.
    """
    # Issue #982: onboarding is paid-only. A Free / expired-override member
    # is redirected away before any LLM turn runs.
    if not can_access_onboarding(request.user):
        messages.info(request, 'Onboarding is part of a paid membership.')
        return redirect('/')

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
        return redirect('onboarding_questions')

    if result.is_complete:
        messages.success(
            request,
            "Thanks -- we'll use this to prepare your plan.",
        )
        # Land on the end-of-onboarding completion screen (#951) with the
        # founder booking CTAs; ``onboarding_start`` renders it for a
        # submitted response.
        return redirect('onboarding_start')

    return render(request, 'accounts/onboarding_chat.html', {
        'conversation': conversation,
        'response': response,
        'chat_messages': _conversation_messages(conversation),
    })


__all__ = [
    'onboarding_chat',
    'onboarding_chat_message',
    'onboarding_chat_stream',
]
