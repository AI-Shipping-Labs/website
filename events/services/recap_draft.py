"""Recap auto-draft service (issue #1597).

Turns a stored transcript into a factual Markdown recap written to
``Event.recap_notes``. Per the product decision on the issue, the draft
publishes through the existing recap gate — ``recap_is_published`` is
``has_recap and is_past`` — so a drafted recap goes live on
``/events/<id>/<slug>/recap`` for a past published event without a manual
step. What stays manual is emailing registrants (the explicit
recap-ready notification).

Guards, in the order the issue states them:

- the LLM service must be enabled,
- ``RECORDING_RECAP_AUTO_DRAFT_ENABLED`` must be on (automatic path only),
- ``recap_notes`` must be empty — operator-authored notes always win and
  are never modified by the automatic path.

Regeneration is explicit only: the sync-transcript endpoint's
``redraft: true`` opt-in passes ``force=True``, which bypasses the toggle
and the empty-notes guard but still requires a configured LLM and a stored
transcript.

The generation core (:func:`draft_event_recap`) never raises for expected
failures (disabled feature, unconfigured LLM, LLM error, missing
transcript) — it logs and returns a status dict so the transcript task's
chain never reports a failure because of the recap step.
"""

import logging

from django.db import transaction

from integrations.config import recording_recap_auto_draft_enabled
from integrations.services import llm
from integrations.services.llm import LLMError

logger = logging.getLogger(__name__)

# Fixed character cap on the transcript fed to the LLM so a long call
# cannot overflow the context window. The head is kept (the opening frames
# the topic) and a truncation note is appended. Not Studio-configurable on
# purpose: it is a safety bound, not an operator knob.
RECAP_DRAFT_MAX_TRANSCRIPT_CHARS = 60_000

RECAP_DRAFT_TRUNCATION_NOTE = (
    '\n\n[Transcript truncated at the character limit — the remainder of '
    'the call is not included. Summarize only what is present.]'
)

# The system prompt is versioned as a module constant so prompt revisions
# are diffable. Guardrails (issue #1597): transcript facts only, no
# attendee PII, no invented claims or unsupported quotes.
RECAP_DRAFT_SYSTEM_PROMPT = (
    'You write factual recaps of AI Shipping Labs community calls '
    '(workshops, office hours, Q&A sessions). You are given the verbatim '
    'transcript of one recorded call. Write a recap in Markdown with:\n'
    '- A short title line ("## <topic>") naming what the call was about.\n'
    '- A "What we covered" section summarising the main topics and the '
    'flow of the call.\n'
    '- A "Key takeaways" section listing the concrete points, tools, '
    'decisions, or resources worth remembering.\n\n'
    'Hard rules:\n'
    '- Use only facts stated in the transcript. Never invent claims, '
    'numbers, links, project names, or quotes that are not in it. Short '
    'verbatim phrases from the transcript are fine; never fabricate an '
    'exact quotation or attribute a statement to someone who did not make '
    'it.\n'
    '- Include no attendee personal data: no email addresses, usernames, '
    'employers, locations, or other personal details. Refer to '
    'participants generically ("a member asked..."). Named hosts or '
    'speakers may be mentioned only to attribute presented content.\n'
    '- Write for members who missed the call. Plain, factual Markdown, no '
    'HTML. If the transcript is too sparse to cover a section, omit that '
    'section rather than padding it.'
)

RECAP_DRAFT_MAX_TOKENS = 2048

RECAP_DRAFT_SKIP_LLM_DISABLED = 'llm_not_configured'
RECAP_DRAFT_SKIP_MISSING_TRANSCRIPT = 'missing_transcript'
RECAP_DRAFT_SKIP_AUTO_DRAFT_DISABLED = 'recap_auto_draft_disabled'
RECAP_DRAFT_SKIP_NOTES_EXIST = 'recap_notes_exist'


def recap_draft_gate(event, force=False):
    """Return ``(allowed, reason)`` for recap drafting.

    ``reason`` is a stable skip code when not allowed:
    ``llm_not_configured``, ``missing_transcript``,
    ``recap_auto_draft_disabled`` (automatic path only),
    ``recap_notes_exist`` (automatic path only).
    """
    if not llm.is_enabled():
        return False, RECAP_DRAFT_SKIP_LLM_DISABLED
    if not event.transcript_text:
        return False, RECAP_DRAFT_SKIP_MISSING_TRANSCRIPT
    if force:
        return True, ''
    if not recording_recap_auto_draft_enabled():
        return False, RECAP_DRAFT_SKIP_AUTO_DRAFT_DISABLED
    if (event.recap_notes or '').strip():
        return False, RECAP_DRAFT_SKIP_NOTES_EXIST
    return True, ''


def build_recap_draft_user_message(event):
    """Render the user message: event context plus the bounded transcript."""
    transcript = event.transcript_text or ''
    if len(transcript) > RECAP_DRAFT_MAX_TRANSCRIPT_CHARS:
        transcript = (
            transcript[:RECAP_DRAFT_MAX_TRANSCRIPT_CHARS]
            + RECAP_DRAFT_TRUNCATION_NOTE
        )
    lines = [
        f'Event title: {event.title}',
        f'Event date: {event.start_datetime.date().isoformat()}',
        '',
        'Transcript follows:',
        '',
        transcript,
    ]
    return '\n'.join(lines)


def draft_event_recap(event, force=False):
    """Draft a recap from the stored transcript into ``recap_notes``.

    Saves through the full model save so ``recap_notes_html`` re-renders
    (that is what makes ``has_recap`` — and the public recap page — live
    for a past published event). Returns a status dict and never raises
    for expected failures.
    """
    allowed, reason = recap_draft_gate(event, force=force)
    if not allowed:
        logger.info(
            'Recap draft skipped for event "%s" (id=%s): %s',
            event.title, event.id, reason,
        )
        return {'status': 'skipped', 'reason': reason, 'event_id': event.id}

    messages = [
        {'role': 'user', 'content': build_recap_draft_user_message(event)},
    ]
    try:
        result = llm.complete(
            messages,
            system=RECAP_DRAFT_SYSTEM_PROMPT,
            max_tokens=RECAP_DRAFT_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.error(
            'Recap draft LLM call failed for event "%s" (id=%s): %s',
            event.title, event.id, exc,
        )
        return {'status': 'error', 'reason': 'llm_error', 'event_id': event.id}

    recap = (result.text or '').strip()
    if not recap:
        logger.error(
            'Recap draft LLM returned empty output for event "%s" (id=%s)',
            event.title, event.id,
        )
        return {'status': 'error', 'reason': 'empty_output', 'event_id': event.id}

    # The LLM call can take long enough for an operator to save recap notes
    # after the initial gate. Re-read under a row lock before writing so the
    # automatic path never replaces those newer notes. Explicit ``force``
    # redrafts intentionally bypass this final guard.
    with transaction.atomic():
        current_event = type(event).objects.select_for_update().get(pk=event.pk)
        if not force and (current_event.recap_notes or '').strip():
            logger.info(
                'Recap draft skipped after generation for event "%s" '
                '(id=%s): operator notes appeared while the LLM ran',
                current_event.title,
                current_event.id,
            )
            return {
                'status': 'skipped',
                'reason': RECAP_DRAFT_SKIP_NOTES_EXIST,
                'event_id': current_event.id,
            }
        current_event.recap_notes = recap
        current_event.save()
    logger.info(
        'Drafted recap (%d chars) into recap_notes for event "%s" (id=%s, '
        'force=%s)',
        len(recap), current_event.title, current_event.id, force,
    )
    return {
        'status': 'drafted',
        'event_id': current_event.id,
        'characters': len(recap),
        'forced': force,
    }


def enqueue_recap_draft_task(event, *, source, force=False):
    """Enqueue the recap-draft task when its gates allow it.

    Best-effort by contract: a closed gate is a logged no-op that returns
    ``None``, so the transcript chain never fails because of the recap
    step. The task re-checks the gates at run time, so notes an operator
    saves between enqueue and run are never overwritten.
    """
    from jobs.tasks import async_task, build_task_name

    allowed, reason = recap_draft_gate(event, force=force)
    if not allowed:
        logger.info(
            'Recap draft task not enqueued for event "%s" (id=%s): %s',
            event.title, event.id, reason,
        )
        return None
    return async_task(
        'jobs.tasks.recap_draft.draft_event_recap',
        event.id,
        force,
        max_retries=1,
        retry_backoff=300,
        timeout=600,
        task_name=build_task_name(
            'Draft event recap',
            f'event #{event.id} {event.title}',
            source,
        ),
    )
