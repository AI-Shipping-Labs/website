"""Django-facing glue for the AI onboarding interview (issues #804/#821).

The pure interview logic lives in :mod:`questionnaires.onboarding_ai`
(no ORM, no request). This module is the ONLY onboarding-AI code that
touches the database: it builds the persona catalog from ``Persona`` /
``Questionnaire`` rows, persists the chat transcript on an
``OnboardingConversation``, and -- on completion -- materializes the
target onboarding questionnaire onto the member's ``Response`` and writes
the extracted answers as standard #800 ``Answer`` rows (exactly what
#802's form produces).
"""

import hashlib
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from integrations.config import get_config
from integrations.services.llm import CancellationToken, LLMError, LLMTimeoutError
from questionnaires.models import (
    Answer,
    OnboardingConversation,
    OnboardingTurnAttempt,
    Persona,
    Response,
    ResponseQuestionOption,
)
from questionnaires.onboarding import (
    get_generic_onboarding_questionnaire,
    onboarding_ai_deadline_seconds,
    onboarding_ai_max_attempts,
)
from questionnaires.onboarding_ai import (
    OnboardingTurnResult,
    PersonaInfo,
    PersonaQuestion,
    TraceSink,
    run_onboarding_turn,
    stream_onboarding_turn,
)
from questionnaires.services import build_response_questions

# Persona signals that always route to the generic onboarding
# questionnaire (mirrors #802's none/multiple fallback rule).
_GENERIC_SIGNALS = frozenset({'blend', 'other'})

_TEXT_TYPES = frozenset({'text', 'long_text'})
_NUMBER_TYPES = frozenset({'scale', 'number'})
_CHOICE_TYPES = frozenset({'single_choice', 'multiple_choice'})

logger = logging.getLogger(__name__)


class TurnRequestError(Exception):
    """Safe, typed rejection of a logical turn (never includes content)."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class LogicalTurnOutcome:
    result: OnboardingTurnResult | None
    attempt_id: int
    replayed: bool = False


class _TurnTrace(TraceSink):
    """Content-free collector for terminal provider usage."""

    result = None

    def on_result(self, *, result, latency_seconds):
        self.result = result


def _message_hash(message):
    return hashlib.sha256(message.encode('utf-8')).hexdigest()


def _ms(start, end):
    if start is None or end is None:
        return None
    return max(0, round((end - start) * 1000))


def _datetime_ms(start, end):
    return max(0, round((end - start).total_seconds() * 1000))


def _deadline_timeout():
    return LLMTimeoutError('Onboarding generation exceeded its hard deadline')


def _deadline_now():
    """Monotonic deadline seam kept separate for deterministic clock tests."""
    return time.monotonic()


def _bounded_call(call, deadline, cancellation):
    """Run provider work off-thread and cancel its transport at ``deadline``."""
    results = queue.Queue(maxsize=1)
    started = threading.Event()
    finished = threading.Event()

    def worker():
        started.set()
        try:
            results.put(('result', call()))
        except BaseException as exc:  # noqa: BLE001 - re-raised on request thread
            results.put(('error', exc))
        finally:
            finished.set()

    threading.Thread(target=worker, daemon=True).start()
    started.wait(timeout=0.1)
    while True:
        remaining = deadline - _deadline_now()
        if remaining <= 0:
            cancellation.cancel()
            finished.wait(timeout=0.5)
            raise _deadline_timeout()
        try:
            kind, value = results.get(timeout=min(remaining, 0.05))
        except queue.Empty:
            continue
        if kind == 'error':
            raise value
        if _deadline_now() >= deadline:
            cancellation.cancel()
            finished.wait(timeout=0.5)
            raise _deadline_timeout()
        return value


def _bounded_iter(iterator, deadline, cancellation):
    """Iterate provider work under one absolute deadline with cleanup."""
    events = queue.Queue(maxsize=1)
    stop = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    def publish(item):
        while not stop.is_set():
            try:
                events.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def worker():
        started.set()
        try:
            for item in iterator:
                if not publish(('item', item)):
                    break
            publish(('done', None))
        except BaseException as exc:  # noqa: BLE001 - re-raised on request thread
            publish(('error', exc))
        finally:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()
            finished.set()

    threading.Thread(target=worker, daemon=True).start()
    started.wait(timeout=0.1)
    try:
        while True:
            remaining = deadline - _deadline_now()
            if remaining <= 0:
                cancellation.cancel()
                finished.wait(timeout=0.5)
                raise _deadline_timeout()
            try:
                kind, value = events.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if _deadline_now() >= deadline:
                cancellation.cancel()
                finished.wait(timeout=0.5)
                raise _deadline_timeout()
            if kind == 'item':
                yield value
            elif kind == 'error':
                raise value
            else:
                return
    finally:
        stop.set()


def _safe_provider_identity():
    return (
        str(get_config('LLM_PROVIDER', 'anthropic') or 'anthropic')[:32],
        str(get_config('LLM_MODEL', 'claude-sonnet-4-5') or '')[:120],
    )


def build_persona_catalog():
    """Build the ORM-free persona catalog the core callable consumes.

    One :class:`PersonaInfo` per active persona that has a
    ``default_questionnaire``, carrying the member-safe archetype +
    description + question spine. The internal persona ``name`` is
    deliberately excluded; the persona ``slug`` is reused as the routing
    ``signal`` (it matches the ``PersonaSignal`` enum values
    alex/priya/sam/taylor).
    """
    catalog = []
    personas = (
        Persona.objects
        .filter(is_active=True, default_questionnaire__isnull=False)
        .select_related('default_questionnaire')
        .prefetch_related('default_questionnaire__questions__options')
        .order_by('order', 'name')
    )
    for persona in personas:
        questions = [
            PersonaQuestion(
                prompt=q.prompt,
                question_type=q.question_type,
                options=[opt.label for opt in q.options.all()],
            )
            for q in persona.default_questionnaire.questions.all()
        ]
        catalog.append(PersonaInfo(
            signal=persona.slug,
            archetype=persona.archetype,
            description=persona.description,
            questions=questions,
        ))
    return catalog


def get_or_create_conversation(response):
    """Return the member's ``OnboardingConversation`` row, creating it."""
    conversation, _created = OnboardingConversation.objects.get_or_create(
        response=response,
    )
    return conversation


def resolve_target_questionnaire_for_signal(persona_signal):
    """Map an inferred ``persona_signal`` to the target questionnaire.

    Same fallback rule as #802: ``blend`` / ``other`` (or any unknown
    signal, or a persona without a ``default_questionnaire``) routes to
    the generic onboarding questionnaire. A recognised persona slug
    routes to that persona's ``default_questionnaire``.
    """
    generic = get_generic_onboarding_questionnaire()
    signal = (persona_signal or '').strip().lower()
    if signal in _GENERIC_SIGNALS or not signal:
        return generic
    persona = (
        Persona.objects
        .filter(slug=signal, is_active=True)
        .select_related('default_questionnaire')
        .first()
    )
    if persona is not None and persona.default_questionnaire is not None:
        return persona.default_questionnaire
    return generic


def _write_extracted_answers(response, extracted_answers):
    """Write the extracted answers as #800 ``Answer`` rows.

    Matches each :class:`ExtractedAnswer` against the response's
    materialized ``ResponseQuestion`` rows by prompt, then stores the
    value by question type (choice -> ``selected_options``, scale/number
    -> ``number_value``, text/long_text -> ``text_value``). Unmatched
    extracted answers are skipped so a prompt drift never raises.
    """
    rqs_by_prompt = {
        rq.prompt: rq
        for rq in response.response_questions.prefetch_related('options').all()
    }
    for extracted in extracted_answers or []:
        rq = rqs_by_prompt.get(extracted.prompt)
        if rq is None:
            continue
        answer, _ = Answer.objects.get_or_create(response=response, question=rq)
        qtype = rq.question_type
        if qtype in _NUMBER_TYPES:
            answer.text_value = ''
            answer.number_value = extracted.number_value
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()
        elif qtype in _CHOICE_TYPES:
            answer.text_value = ''
            answer.number_value = None
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            wanted = {label.strip().lower() for label in extracted.selected_labels}
            matched = [
                opt for opt in rq.options.all()
                if opt.label.strip().lower() in wanted
            ]
            if matched:
                answer.selected_options.set(
                    ResponseQuestionOption.objects.filter(
                        pk__in=[o.pk for o in matched],
                    ),
                )
            else:
                answer.selected_options.clear()
        else:  # text / long_text (and any unknown type as text)
            answer.number_value = None
            answer.text_value = extracted.text_value or ''
            answer.save(update_fields=['text_value', 'number_value', 'updated_at'])
            answer.selected_options.clear()


def finalize_conversation(conversation, turn_result):
    """Persist a completed interview as #800 ``Response`` / ``Answer`` rows.

    Routes the inferred ``persona_signal`` to a target onboarding
    questionnaire (persona default or the generic fallback), repoints the
    member's onboarding ``Response`` at it if needed, materializes its
    question set via ``build_response_questions``, writes the extracted
    answers, stamps the internal signal Studio-side, and
    ``mark_submitted()``s the response. Returns the submitted response.
    """
    response = conversation.response
    extraction = turn_result.extraction
    persona_signal = extraction.persona_signal.value if extraction else ''

    target = resolve_target_questionnaire_for_signal(persona_signal)
    if target is not None and response.questionnaire_id != target.pk:
        # The member entered chat against the generic placeholder; repoint
        # to the inferred target and re-materialize its question set.
        # Safe because a draft AI response carries no member-entered
        # answers yet (the chat transcript is the only state).
        response.response_questions.all().delete()
        response.questionnaire = target
        response.save(update_fields=['questionnaire', 'updated_at'])

    build_response_questions(response)
    _write_extracted_answers(response, turn_result.answers)

    conversation.persona_signal = persona_signal
    conversation.save(update_fields=['persona_signal', 'updated_at'])

    response.mark_submitted()
    return response


def _enqueue_staff_notification(attempt_id):
    """Enqueue only after the member-state transaction commits."""
    try:
        from jobs.tasks import async_task, build_task_name  # noqa: PLC0415
        async_task(
            'questionnaires.tasks.send_onboarding_staff_notification',
            attempt_id,
            max_retries=3,
            retry_backoff=60,
            task_name=build_task_name(
                'Notify staff of onboarding',
                f'attempt #{attempt_id}',
                'onboarding completion',
            ),
        )
    except Exception:  # noqa: BLE001 -- member state is already committed
        logger.warning(
            'Failed to enqueue onboarding staff notification',
            extra={'onboarding_attempt_id': attempt_id},
        )


def _admit_turn(conversation, request_id, member_message, transport):
    """Reserve one bounded provider call without holding a lock during it."""
    try:
        parsed_request_id = uuid.UUID(str(request_id))
    except (TypeError, ValueError, AttributeError):
        raise TurnRequestError('invalid_request_id') from None
    if transport not in {'stream', 'non_stream'}:
        raise TurnRequestError('invalid_transport')

    now = timezone.now()
    lease = now + timedelta(seconds=onboarding_ai_deadline_seconds() + 5)
    digest = _message_hash(member_message)
    provider, model = _safe_provider_identity()

    try:
        with transaction.atomic():
            locked = OnboardingConversation.objects.select_for_update().get(
                pk=conversation.pk,
            )
            stale = locked.turn_attempts.filter(
                status='processing', lease_expires_at__lte=now,
            )
            stale.update(
                status='failed', outcome='failed', error_code='lease_expired',
                completed_at=now,
            )

            attempt = locked.turn_attempts.select_for_update().filter(
                request_id=parsed_request_id,
            ).first()
            if attempt is not None:
                if attempt.member_message_hash != digest:
                    raise TurnRequestError('altered_message')
                if attempt.status == 'succeeded':
                    return attempt, list(locked.transcript or []), True
                if attempt.status == 'processing':
                    raise TurnRequestError('busy')
                if attempt.error_code == 'conversation_advanced':
                    raise TurnRequestError('conversation_advanced')
                if attempt.provider_call_count >= onboarding_ai_max_attempts():
                    raise TurnRequestError('attempts_exhausted')
                if locked.turn_attempts.filter(status='processing').exclude(
                    pk=attempt.pk,
                ).exists():
                    raise TurnRequestError('busy')
                attempt.status = 'processing'
                attempt.fallback_used = (
                    attempt.fallback_used
                    or (attempt.transport == 'stream' and transport == 'non_stream')
                )
                attempt.transport = transport
                attempt.outcome = ''
                attempt.retry_count = attempt.provider_call_count
                attempt.provider_call_count += 1
                attempt.lease_expires_at = lease
                attempt.provider_started_at = now
                attempt.completed_at = None
                attempt.save()
                return attempt, list(locked.transcript or []), False

            if locked.turn_attempts.filter(status='processing').exists():
                raise TurnRequestError('busy')
            attempt = OnboardingTurnAttempt.objects.create(
                conversation=locked,
                request_id=parsed_request_id,
                member_message_hash=digest,
                admitted_version=locked.turn_version,
                transport=transport,
                status='processing',
                provider=provider,
                model=model,
                provider_call_count=1,
                retry_count=0,
                started_at=now,
                provider_started_at=now,
                lease_expires_at=lease,
            )
            return attempt, list(locked.transcript or []), False
    except IntegrityError:
        # A simultaneous tab won a uniqueness constraint. Re-read it through
        # the same typed path; it will resolve to busy/replay/altered.
        with transaction.atomic():
            attempt = OnboardingTurnAttempt.objects.select_for_update().filter(
                conversation=conversation,
                request_id=parsed_request_id,
            ).first()
            if attempt and attempt.member_message_hash != digest:
                raise TurnRequestError('altered_message') from None
            if attempt and attempt.status == 'succeeded':
                return attempt, list(conversation.transcript or []), True
        raise TurnRequestError('busy') from None


def _mark_turn_failed(
    attempt_id,
    error_code,
    tracker,
    first_delta=None,
    last_delta=None,
    *,
    recoverable=False,
):
    now = timezone.now()
    with transaction.atomic():
        current = OnboardingTurnAttempt.objects.select_for_update().get(
            pk=attempt_id,
        )
        if current.status != 'processing':
            return
        current.status = 'failed'
        current.outcome = 'failed'
        current.error_code = error_code
        current.first_delta_at = current.first_delta_at or first_delta
        current.last_delta_at = last_delta or current.last_delta_at
        current.completed_at = now
        current.timed_out = current.timed_out or error_code == 'timeout'
        current.disconnected = (
            current.disconnected or error_code == 'client_disconnect'
        )
        duration = _ms(tracker['provider'], time.monotonic())
        current.provider_duration_ms = (
            (current.provider_duration_ms or 0) + (duration or 0)
        )
        current.admission_to_provider_ms = (
            current.admission_to_provider_ms
            if current.admission_to_provider_ms is not None
            else _ms(tracker['admission'], tracker['provider'])
        )
        if current.ttft_ms is None and tracker.get('first_delta') is not None:
            current.ttft_ms = _ms(tracker['provider'], tracker['first_delta'])
        current.total_duration_ms = (
            (current.total_duration_ms or 0)
            + (_ms(tracker['admission'], time.monotonic()) or 0)
        )
        current.save()
    # A failed first stream that will automatically fall back is not a
    # terminal logical-turn outcome.  Keep its cause/timing on the durable
    # row, then emit once when the fallback succeeds or exhausts the budget.
    if not recoverable:
        _log_turn(attempt_id)


def _log_turn(attempt_id):
    """Emit exactly one content-free terminal record for a logical attempt."""
    attempt = OnboardingTurnAttempt.objects.filter(pk=attempt_id).values(
        'conversation_id', 'transport', 'status', 'outcome', 'error_code',
        'provider', 'model', 'provider_call_count', 'retry_count',
        'fallback_used', 'timed_out', 'disconnected',
        'input_tokens', 'output_tokens', 'cache_read_tokens',
        'cache_write_tokens', 'admission_to_provider_ms', 'ttft_ms',
        'provider_duration_ms', 'persistence_tail_ms',
        'persistence_to_done_ms', 'total_duration_ms',
    ).first()
    if attempt is not None:
        logger.info(
            'onboarding_turn_terminal',
            extra={'onboarding_turn': {'attempt_id': attempt_id, **attempt}},
        )


def _apply_turn(attempt_id, member_message, result, trace, tracker, first_delta, last_delta):
    now = timezone.now()
    with transaction.atomic():
        attempt = (
            OnboardingTurnAttempt.objects.select_for_update()
            .select_related('conversation__response')
            .get(pk=attempt_id)
        )
        conversation = OnboardingConversation.objects.select_for_update().get(
            pk=attempt.conversation_id,
        )
        # Lock final artifacts in the same transaction and reject any state
        # advancement since admission before appending content.
        Response.objects.select_for_update().get(pk=conversation.response_id)
        if attempt.status != 'processing':
            raise TurnRequestError('not_processing')
        if conversation.turn_version != attempt.admitted_version:
            attempt.status = 'failed'
            attempt.outcome = 'failed'
            attempt.error_code = 'conversation_advanced'
            attempt.completed_at = now
            attempt.save()
            raise TurnRequestError('conversation_advanced')

        transcript = list(conversation.transcript or [])
        transcript.append({'role': 'user', 'content': member_message})
        transcript.append({'role': 'assistant', 'content': result.assistant_message})
        conversation.transcript = transcript
        conversation.turn_version = F('turn_version') + 1
        conversation.save(update_fields=['transcript', 'turn_version', 'updated_at'])

        if result.is_complete:
            finalize_conversation(conversation, result)
            attempt.notification_status = 'pending'

        persisted = time.monotonic()
        usage = trace.result
        attempt.status = 'succeeded'
        attempt.outcome = 'final' if result.is_complete else 'intermediate'
        if attempt.provider_call_count <= 1:
            attempt.error_code = ''
        attempt.first_delta_at = attempt.first_delta_at or first_delta
        attempt.last_delta_at = last_delta or attempt.last_delta_at
        attempt.completed_at = timezone.now()
        if attempt.admission_to_provider_ms is None:
            attempt.admission_to_provider_ms = _ms(
                tracker['admission'], tracker['provider'],
            )
        if attempt.ttft_ms is None:
            attempt.ttft_ms = _ms(
                tracker['provider'], tracker.get('first_delta'),
            )
        provider_duration = _ms(tracker['provider'], tracker['provider_done'])
        attempt.provider_duration_ms = (
            (attempt.provider_duration_ms or 0) + (provider_duration or 0)
        )
        attempt.persistence_tail_ms = _ms(
            tracker.get('last_delta') or tracker['provider_done'], persisted,
        )
        attempt.total_duration_ms = (
            (attempt.total_duration_ms or 0)
            + (_ms(tracker['admission'], persisted) or 0)
        )
        if usage is not None:
            attempt.input_tokens = usage.input_tokens
            attempt.output_tokens = usage.output_tokens
            attempt.cache_read_tokens = usage.cache_read_tokens
            attempt.cache_write_tokens = usage.cache_write_tokens
        attempt.save()
        if result.is_complete:
            transaction.on_commit(lambda: _enqueue_staff_notification(attempt.pk))

    done = time.monotonic()
    done_tail = _ms(persisted, done) or 0
    OnboardingTurnAttempt.objects.filter(pk=attempt_id).update(
        persistence_to_done_ms=done_tail,
        total_duration_ms=F('total_duration_ms') + done_tail,
    )
    _log_turn(attempt_id)
    return LogicalTurnOutcome(result=result, attempt_id=attempt_id)


def run_logical_member_turn(conversation, request_id, member_message, *, persona_catalog=None):
    """Run the bounded non-stream transport under the durable turn contract."""
    tracker = {'admission': time.monotonic()}
    attempt, transcript, replayed = _admit_turn(
        conversation, request_id, member_message, 'non_stream',
    )
    if replayed:
        return LogicalTurnOutcome(None, attempt.pk, replayed=True)
    tracker['provider'] = time.monotonic()
    deadline = _deadline_now() + onboarding_ai_deadline_seconds()
    trace = _TurnTrace()
    cancellation = CancellationToken()
    try:
        catalog = persona_catalog or build_persona_catalog()
        result = _bounded_call(
            lambda: run_onboarding_turn(
                transcript,
                member_message=member_message,
                persona_catalog=catalog,
                trace=trace,
                timeout_seconds=onboarding_ai_deadline_seconds(),
                cancellation=cancellation,
            ),
            deadline,
            cancellation,
        )
        tracker['provider_done'] = time.monotonic()
        return _apply_turn(
            attempt.pk, member_message, result, trace, tracker, None, None,
        )
    except TurnRequestError as exc:
        if exc.code in {'conversation_advanced', 'persistence_error'}:
            _mark_turn_failed(attempt.pk, exc.code, tracker)
        raise
    except LLMTimeoutError:
        _mark_turn_failed(attempt.pk, 'timeout', tracker)
        raise
    except LLMError:
        _mark_turn_failed(attempt.pk, 'provider_error', tracker)
        raise
    except Exception:
        _mark_turn_failed(attempt.pk, 'persistence_error', tracker)
        raise TurnRequestError('persistence_error') from None


def stream_logical_member_turn(conversation, request_id, member_message, *, persona_catalog=None):
    """Yield deltas then a logical outcome, closing provider work on exit."""
    tracker = {'admission': time.monotonic()}
    attempt, transcript, replayed = _admit_turn(
        conversation, request_id, member_message, 'stream',
    )
    if replayed:
        yield LogicalTurnOutcome(None, attempt.pk, replayed=True)
        return
    tracker['provider'] = time.monotonic()
    deadline = _deadline_now() + onboarding_ai_deadline_seconds()
    trace = _TurnTrace()
    cancellation = CancellationToken()
    core = stream_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog or build_persona_catalog(),
        trace=trace,
        timeout_seconds=onboarding_ai_deadline_seconds(),
        cancellation=cancellation,
    )
    result = None
    first_delta = None
    last_delta = None
    try:
        for item in _bounded_iter(core, deadline, cancellation):
            if isinstance(item, OnboardingTurnResult):
                result = item
                continue
            stamp = timezone.now()
            mono = time.monotonic()
            if first_delta is None:
                first_delta = stamp
                tracker['first_delta'] = mono
            last_delta = stamp
            tracker['last_delta'] = mono
            yield item
        tracker['provider_done'] = time.monotonic()
        if result is None:
            raise LLMError('LLM stream ended without a terminal result')
        yield _apply_turn(
            attempt.pk, member_message, result, trace, tracker,
            first_delta, last_delta,
        )
    except GeneratorExit:
        _mark_turn_failed(
            attempt.pk, 'client_disconnect', tracker, first_delta, last_delta,
        )
        raise
    except TurnRequestError as exc:
        if exc.code in {'conversation_advanced', 'persistence_error'}:
            _mark_turn_failed(
                attempt.pk, exc.code, tracker, first_delta, last_delta,
            )
        raise
    except LLMTimeoutError:
        _mark_turn_failed(
            attempt.pk, 'timeout', tracker, first_delta, last_delta,
        )
        raise
    except LLMError:
        _mark_turn_failed(
            attempt.pk,
            'provider_error',
            tracker,
            first_delta,
            last_delta,
            recoverable=(
                attempt.provider_call_count < onboarding_ai_max_attempts()
            ),
        )
        raise
    except Exception:
        _mark_turn_failed(
            attempt.pk, 'persistence_error', tracker, first_delta, last_delta,
        )
        raise TurnRequestError('persistence_error') from None
    finally:
        # ``_bounded_iter`` owns provider iteration and closes ``core`` on
        # its worker thread. Closing it here can race a blocked provider and
        # raise ``ValueError: generator already executing``.
        pass


def run_member_turn(conversation, member_message, *, persona_catalog=None):
    """Run one member turn: call the core, persist the transcript.

    Loads the persisted transcript, calls the pure
    :func:`run_onboarding_turn`, appends both the member message (when
    present) and the assistant reply to the transcript, and -- when the
    interview completes -- finalizes the onboarding response. Returns the
    :class:`OnboardingTurnResult`.

    Any :class:`~integrations.services.llm.LLMError` propagates to the
    caller (the view), which routes the member to the #802 form fallback.
    """
    if persona_catalog is None:
        persona_catalog = build_persona_catalog()
    transcript = conversation.transcript if isinstance(
        conversation.transcript, list,
    ) else []

    result = run_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog,
    )

    if member_message is not None:
        conversation.append_turn('user', member_message)
    conversation.append_turn('assistant', result.assistant_message)
    conversation.save(update_fields=['transcript', 'updated_at'])

    if result.is_complete:
        finalize_conversation(conversation, result)

    return result


def stream_member_turn(conversation, member_message, *, persona_catalog=None):
    """Stream one member turn: yield text deltas, then persist the turn.

    Streaming counterpart to :func:`run_member_turn` (issue #806). It is a
    generator: it yields incremental ``str`` text deltas as the assistant
    reply is produced, and finally yields the authoritative
    :class:`OnboardingTurnResult` (the LAST item).

    Persistence is IDENTICAL to :func:`run_member_turn` and happens only
    AFTER the authoritative result is assembled: the member message + the
    assistant reply are appended to the transcript, and on completion the
    response is finalized into the SAME #800 ``Response`` / ``Answer``
    rows. Because nothing is written until the stream completes, a
    mid-stream failure (which raises :class:`LLMError` before any write)
    leaves no partial state — so a retry via the v1 non-streaming endpoint
    is the first and only write and cannot create a duplicate turn or
    duplicate answers.

    Any :class:`~integrations.services.llm.LLMError` propagates to the
    caller (the streaming view), which signals the client to fall back.
    """
    if persona_catalog is None:
        persona_catalog = build_persona_catalog()
    transcript = conversation.transcript if isinstance(
        conversation.transcript, list,
    ) else []

    result = None
    for item in stream_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog,
    ):
        if isinstance(item, OnboardingTurnResult):
            result = item
        else:
            yield item

    # Persist only after the authoritative result is in hand (no partial
    # writes on a mid-stream failure -> no duplicate on a v1 retry).
    if member_message is not None:
        conversation.append_turn('user', member_message)
    conversation.append_turn('assistant', result.assistant_message)
    conversation.save(update_fields=['transcript', 'updated_at'])

    if result.is_complete:
        finalize_conversation(conversation, result)

    yield result


def get_or_create_ai_onboarding_response(user):
    """Return the member's onboarding ``Response`` for the AI chat path.

    Reuses any existing onboarding response (so chat and form share one
    response per member). When the member has none, creates a draft
    against the generic onboarding questionnaire as a placeholder; the
    final questionnaire is resolved from the inferred persona signal at
    completion. Returns ``(response, conversation)`` or ``(None, None)``
    when no onboarding questionnaire is seeded at all.
    """
    existing = (
        Response.objects
        .filter(respondent=user, questionnaire__purpose='onboarding')
        .select_related('questionnaire')
        .order_by('created_at')
        .first()
    )
    if existing is not None:
        return existing, get_or_create_conversation(existing)

    generic = get_generic_onboarding_questionnaire()
    if generic is None:
        return None, None
    response = Response.objects.create(
        questionnaire=generic,
        respondent=user,
        status='draft',
    )
    # Materialize the generic questions immediately so the "switch to the
    # form" fallback link works for an in-progress AI response (the
    # questionnaire is repointed + re-materialized at completion if the
    # inferred persona differs).
    build_response_questions(response)
    return response, get_or_create_conversation(response)
