"""Durable logical-turn contract for onboarding performance work (#821)."""

import hashlib
import uuid
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from integrations.services.llm import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    LLMError,
    LLMResult,
    LLMTimeoutError,
    StreamEvent,
)
from questionnaires.models import OnboardingTurnAttempt
from questionnaires.onboarding import (
    onboarding_ai_deadline_seconds,
    onboarding_ai_max_attempts,
)
from questionnaires.onboarding_ai import OnboardingTurnResult
from questionnaires.services_onboarding_ai import (
    TurnRequestError,
    get_or_create_ai_onboarding_response,
    run_logical_member_turn,
    stream_logical_member_turn,
)
from questionnaires.tasks import (
    reconcile_onboarding_staff_notifications,
    send_onboarding_staff_notification,
)

User = get_user_model()


@override_settings(
    LLM_API_KEY='fake',
    LLM_PROVIDER='anthropic',
    LLM_MODEL='test-model',
    ONBOARDING_AI_DEADLINE_SECONDS='25',
    ONBOARDING_AI_MAX_ATTEMPTS='2',
)
@tag('core')
class LogicalTurnContractTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=f'{uuid.uuid4()}@test.com')
        self.response, self.conversation = get_or_create_ai_onboarding_response(
            self.user,
        )
        # Preserve the production zero-call greeting baseline.
        self.conversation.transcript = [
            {'role': 'assistant', 'content': 'Welcome'},
        ]
        self.conversation.save(update_fields=['transcript'])

    def test_duplicate_success_replays_without_provider_call(self):
        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Next question'),
        ) as provider:
            first = run_logical_member_turn(
                self.conversation, request_id, 'my answer',
            )
            second = run_logical_member_turn(
                self.conversation, request_id, 'my answer',
            )
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(provider.call_count, 1)
        self.conversation.refresh_from_db()
        self.assertEqual(
            [turn['role'] for turn in self.conversation.transcript],
            ['assistant', 'user', 'assistant'],
        )

    def test_request_id_reuse_with_changed_message_is_rejected(self):
        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Next'),
        ):
            run_logical_member_turn(
                self.conversation, request_id, 'original answer',
            )
        with self.assertRaisesRegex(TurnRequestError, 'altered_message'):
            run_logical_member_turn(
                self.conversation, request_id, 'changed answer',
            )

    def test_inflight_second_tab_is_busy_before_provider_work(self):
        first_id = uuid.uuid4()

        def provider(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='first')
            yield StreamEvent(
                kind=STREAM_DONE, result=LLMResult(text='first done'),
            )

        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=provider,
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
        ) as second_provider:
            first = stream_logical_member_turn(
                self.conversation, first_id, 'first answer',
            )
            self.assertEqual(next(first), 'first')
            with self.assertRaisesRegex(TurnRequestError, 'busy'):
                run_logical_member_turn(
                    self.conversation, uuid.uuid4(), 'second answer',
                )
            second_provider.assert_not_called()
            list(first)

    def test_stream_failure_and_fallback_share_two_call_budget(self):
        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=LLMError('safe fake failure'),
        ):
            with self.assertRaises(LLMError):
                list(stream_logical_member_turn(
                    self.conversation, request_id, 'same answer',
                ))
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Recovered'),
        ) as provider:
            outcome = run_logical_member_turn(
                self.conversation, request_id, 'same answer',
            )
        self.assertFalse(outcome.replayed)
        provider.assert_called_once()
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.provider_call_count, 2)
        self.assertEqual(attempt.retry_count, 1)
        self.assertEqual(attempt.status, 'succeeded')

    def test_successful_fallback_preserves_stream_cause_timing_and_logs(self):
        request_id = uuid.uuid4()

        def failed_stream(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='private partial')
            raise LLMError('safe failure')

        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=failed_stream,
        ), patch(
            'questionnaires.services_onboarding_ai.logger.info',
        ) as terminal_log:
            with self.assertRaises(LLMError):
                list(stream_logical_member_turn(
                    self.conversation, request_id, 'private answer',
                ))
            terminal_log.assert_not_called()
            with patch(
                'questionnaires.onboarding_ai.llm.complete',
                return_value=LLMResult(text='private recovered'),
            ):
                run_logical_member_turn(
                    self.conversation, request_id, 'private answer',
                )

        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'succeeded')
        self.assertTrue(attempt.fallback_used)
        self.assertEqual(attempt.error_code, 'provider_error')
        self.assertIsNotNone(attempt.first_delta_at)
        self.assertIsNotNone(attempt.ttft_ms)
        self.assertGreaterEqual(attempt.total_duration_ms, 0)
        terminal_log.assert_called_once()
        final_payload = terminal_log.call_args.kwargs['extra']['onboarding_turn']
        self.assertEqual(final_payload['outcome'], 'intermediate')
        self.assertEqual(final_payload['error_code'], 'provider_error')
        self.assertTrue(final_payload['fallback_used'])
        self.assertNotIn('private', str(final_payload))

    def test_intermediate_and_final_terminal_payloads_have_wall_clock_total(self):
        with patch(
            'questionnaires.services_onboarding_ai.logger.info',
        ) as terminal_log, patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=OnboardingTurnResult(
                assistant_message='private intermediate', is_complete=False,
            ),
        ):
            run_logical_member_turn(
                self.conversation, uuid.uuid4(), 'private first',
            )
        intermediate = terminal_log.call_args.kwargs['extra']['onboarding_turn']
        self.assertEqual(intermediate['outcome'], 'intermediate')
        self.assertIsNotNone(intermediate['total_duration_ms'])
        self.assertNotIn('private', str(intermediate))

        terminal_log.reset_mock()
        with patch(
            'questionnaires.services_onboarding_ai.logger.info', terminal_log,
        ), patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=OnboardingTurnResult(
                assistant_message='private final', is_complete=True,
            ),
        ), patch(
            'questionnaires.services_onboarding_ai._enqueue_staff_notification',
        ):
            run_logical_member_turn(
                self.conversation, uuid.uuid4(), 'private second',
            )
        final_payload = terminal_log.call_args.kwargs['extra']['onboarding_turn']
        self.assertEqual(final_payload['outcome'], 'final')
        self.assertIsNotNone(final_payload['total_duration_ms'])
        self.assertNotIn('private', str(final_payload))

    def test_timeout_marks_safe_outcome_and_closes_provider_iteration(self):
        closed = []

        def provider(messages, **kwargs):
            try:
                yield StreamEvent(kind=STREAM_TEXT_DELTA, text='partial')
                raise LLMTimeoutError('safe timeout')
            finally:
                closed.append(True)

        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=provider,
        ):
            with self.assertRaises(LLMTimeoutError):
                list(stream_logical_member_turn(
                    self.conversation, request_id, 'answer',
                ))
        self.assertEqual(closed, [True])
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertTrue(attempt.timed_out)
        self.assertEqual(attempt.error_code, 'timeout')
        self.conversation.refresh_from_db()
        self.assertEqual(len(self.conversation.transcript), 1)

    def test_absolute_deadline_releases_never_finishing_stream_and_logs(self):
        provider_started = Event()
        provider_closed = Event()

        def never_finishes(messages, **kwargs):
            provider_started.set()
            try:
                cancellation = kwargs['cancellation']
                if not cancellation.wait(timeout=2):
                    raise AssertionError('deadline never cancelled provider stream')
                if False:
                    yield None
            finally:
                provider_closed.set()

        class FakeClock:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                if self.calls <= 3:
                    return 0.0
                self.assert_provider_started()
                return 30.0

            def assert_provider_started(self):
                if not provider_started.wait(timeout=1):
                    raise AssertionError('provider worker never started')

        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=never_finishes,
        ), patch(
            'questionnaires.services_onboarding_ai._deadline_now',
            side_effect=FakeClock(),
        ), patch(
            'questionnaires.services_onboarding_ai.logger.info',
        ) as terminal_log:
            with self.assertRaises(LLMTimeoutError):
                list(stream_logical_member_turn(
                    self.conversation, request_id, 'answer',
                ))
            self.assertTrue(
                provider_closed.is_set(),
                'provider stream must be closed before timeout returns',
            )

        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'timeout')
        self.assertTrue(attempt.timed_out)
        self.assertIsNotNone(attempt.admission_to_provider_ms)
        self.assertIsNotNone(attempt.total_duration_ms)
        terminal_log.assert_called_once()
        payload = terminal_log.call_args.kwargs['extra']['onboarding_turn']
        self.assertEqual(payload['error_code'], 'timeout')
        self.assertNotIn('answer', str(payload))

    def test_absolute_deadline_cancels_never_finishing_blocking_call(self):
        provider_started = Event()
        provider_finished = Event()

        def never_finishes(messages, **kwargs):
            provider_started.set()
            try:
                cancellation = kwargs['cancellation']
                if not cancellation.wait(timeout=2):
                    raise AssertionError('deadline never cancelled provider call')
                raise LLMTimeoutError('cancelled fake provider call')
            finally:
                provider_finished.set()

        class FakeClock:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                if self.calls <= 3:
                    return 0.0
                if not provider_started.wait(timeout=1):
                    raise AssertionError('provider worker never started')
                return 30.0

        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=never_finishes,
        ), patch(
            'questionnaires.services_onboarding_ai._deadline_now',
            side_effect=FakeClock(),
        ):
            with self.assertRaises(LLMTimeoutError):
                run_logical_member_turn(
                    self.conversation, request_id, 'answer',
                )
            self.assertTrue(
                provider_finished.is_set(),
                'provider call must finish before timeout returns',
            )

        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'timeout')

    def test_client_disconnect_closes_provider_and_releases_lease(self):
        closed = []

        def provider(messages, **kwargs):
            try:
                yield StreamEvent(kind=STREAM_TEXT_DELTA, text='partial')
                yield StreamEvent(
                    kind=STREAM_DONE, result=LLMResult(text='done'),
                )
            finally:
                closed.append(True)

        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=provider,
        ):
            stream = stream_logical_member_turn(
                self.conversation, request_id, 'answer',
            )
            self.assertEqual(next(stream), 'partial')
            stream.close()
        self.assertEqual(closed, [True])
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'failed')
        self.assertTrue(attempt.disconnected)
        self.assertEqual(attempt.error_code, 'client_disconnect')

    def test_disconnect_emits_content_free_terminal_telemetry(self):
        def provider(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='private partial')
            yield StreamEvent(
                kind=STREAM_DONE, result=LLMResult(text='private done'),
            )

        request_id = uuid.uuid4()
        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=provider,
        ), patch(
            'questionnaires.services_onboarding_ai.logger.info',
        ) as terminal_log:
            stream = stream_logical_member_turn(
                self.conversation, request_id, 'private answer',
            )
            next(stream)
            stream.close()

        terminal_log.assert_called_once()
        payload = terminal_log.call_args.kwargs['extra']['onboarding_turn']
        self.assertEqual(payload['error_code'], 'client_disconnect')
        self.assertTrue(payload['disconnected'])
        self.assertIsNotNone(payload['ttft_ms'])
        self.assertIsNotNone(payload['total_duration_ms'])
        self.assertNotIn('private', str(payload))

    def test_exhausted_budget_does_not_make_third_call(self):
        request_id = uuid.uuid4()
        for expected_count in (1, 2):
            with patch(
                'questionnaires.onboarding_ai.llm.complete',
                side_effect=LLMError('safe fake failure'),
            ):
                with self.assertRaises(LLMError):
                    run_logical_member_turn(
                        self.conversation, request_id, 'same answer',
                    )
            self.assertEqual(
                OnboardingTurnAttempt.objects.get(
                    request_id=request_id,
                ).provider_call_count,
                expected_count,
            )
        with patch('questionnaires.onboarding_ai.llm.complete') as provider:
            with self.assertRaisesRegex(TurnRequestError, 'attempts_exhausted'):
                run_logical_member_turn(
                    self.conversation, request_id, 'same answer',
                )
        provider.assert_not_called()

    def test_version_change_prevents_partial_persistence(self):
        def provider(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='partial')
            yield StreamEvent(
                kind=STREAM_DONE, result=LLMResult(text='complete'),
            )

        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=provider,
        ):
            request_id = uuid.uuid4()
            stream = stream_logical_member_turn(
                self.conversation, request_id, 'answer',
            )
            self.assertEqual(next(stream), 'partial')
            self.conversation.turn_version += 1
            self.conversation.save(update_fields=['turn_version'])
            with self.assertRaisesRegex(
                TurnRequestError, 'conversation_advanced',
            ):
                list(stream)
        self.conversation.refresh_from_db()
        self.assertEqual(len(self.conversation.transcript), 1)
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'conversation_advanced')
        with self.assertRaisesRegex(
            TurnRequestError, 'conversation_advanced',
        ):
            list(stream_logical_member_turn(
                self.conversation, request_id, 'answer',
            ))

    def test_finalization_exception_rolls_back_and_same_id_recovers(self):
        request_id = uuid.uuid4()
        final = OnboardingTurnResult(
            assistant_message='Done', is_complete=True,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=final,
        ), patch(
            'questionnaires.services_onboarding_ai.finalize_conversation',
            side_effect=RuntimeError('injected persistence failure'),
        ):
            with self.assertRaisesRegex(TurnRequestError, 'persistence_error'):
                run_logical_member_turn(
                    self.conversation, request_id, 'answer',
                )

        self.conversation.refresh_from_db()
        self.response.refresh_from_db()
        self.assertEqual(self.conversation.transcript, [
            {'role': 'assistant', 'content': 'Welcome'},
        ])
        self.assertEqual(self.response.status, 'draft')
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'persistence_error')

        recovered = OnboardingTurnResult(
            assistant_message='Recovered', is_complete=False,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=recovered,
        ):
            outcome = run_logical_member_turn(
                self.conversation, request_id, 'answer',
            )
        self.assertFalse(outcome.replayed)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'succeeded')
        self.assertEqual(attempt.provider_call_count, 2)
        self.conversation.refresh_from_db()
        self.assertEqual(
            [row['role'] for row in self.conversation.transcript],
            ['assistant', 'user', 'assistant'],
        )

    def test_attempt_telemetry_has_usage_but_no_onboarding_content(self):
        message = 'SECRET MEMBER ANSWER'
        request_id = uuid.uuid4()
        result = LLMResult(
            text='SECRET ASSISTANT TEXT',
            input_tokens=11,
            output_tokens=7,
            cache_read_tokens=3,
            cache_write_tokens=2,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete', return_value=result,
        ) as provider:
            run_logical_member_turn(
                self.conversation, request_id, message,
            )
        kwargs = provider.call_args.kwargs
        self.assertGreater(kwargs['timeout_seconds'], 24)
        self.assertLessEqual(kwargs['timeout_seconds'], 25)
        self.assertEqual(kwargs['max_retries'], 0)
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(
            (attempt.input_tokens, attempt.output_tokens,
             attempt.cache_read_tokens, attempt.cache_write_tokens),
            (11, 7, 3, 2),
        )
        self.assertEqual(attempt.model, 'test-model')
        self.assertIsNotNone(attempt.total_duration_ms)
        stored = ' '.join(str(value) for value in attempt.__dict__.values())
        self.assertNotIn(message, stored)
        self.assertNotIn(result.text, stored)
        self.assertEqual(len(attempt.member_message_hash), 64)

    def test_final_notification_is_post_commit_and_worker_is_idempotent(self):
        final = OnboardingTurnResult(
            assistant_message='Done', is_complete=True,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=final,
        ), patch(
            'questionnaires.services_onboarding_ai._enqueue_staff_notification',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                outcome = run_logical_member_turn(
                    self.conversation, uuid.uuid4(), 'answer',
                )
            enqueue.assert_not_called()
            self.response.refresh_from_db()
            self.assertEqual(self.response.status, 'submitted')
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            enqueue.assert_called_once_with(outcome.attempt_id)

        with patch(
            'crm.services.onboarding_notify.notify_staff_onboarding_submitted',
        ) as notify:
            first = send_onboarding_staff_notification(outcome.attempt_id)
            second = send_onboarding_staff_notification(outcome.attempt_id)
        self.assertEqual(first['status'], 'succeeded')
        self.assertEqual(second['reason'], 'already_succeeded')
        notify.assert_called_once()

    def test_final_notification_failure_remains_retryable(self):
        final = OnboardingTurnResult(
            assistant_message='Done', is_complete=True,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=final,
        ), patch(
            'questionnaires.services_onboarding_ai._enqueue_staff_notification',
        ):
            outcome = run_logical_member_turn(
                self.conversation, uuid.uuid4(), 'answer',
            )

        notify_path = (
            'crm.services.onboarding_notify.notify_staff_onboarding_submitted'
        )
        with patch(notify_path, side_effect=RuntimeError('provider down')):
            with self.assertRaisesRegex(RuntimeError, 'provider down'):
                send_onboarding_staff_notification(outcome.attempt_id)
        attempt = OnboardingTurnAttempt.objects.get(pk=outcome.attempt_id)
        self.assertEqual(attempt.notification_status, 'failed')

        with patch(notify_path) as notify:
            retried = send_onboarding_staff_notification(outcome.attempt_id)
        self.assertEqual(retried['status'], 'succeeded')
        notify.assert_called_once()
        attempt.refresh_from_db()
        self.assertEqual(attempt.notification_attempt_count, 2)

    def test_enqueue_failure_is_recovered_by_durable_reconciler(self):
        final = OnboardingTurnResult(
            assistant_message='Done', is_complete=True,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=final,
        ), patch(
            'jobs.tasks.async_task', side_effect=RuntimeError('queue down'),
        ), self.captureOnCommitCallbacks(execute=True):
            outcome = run_logical_member_turn(
                self.conversation, uuid.uuid4(), 'answer',
            )

        attempt = OnboardingTurnAttempt.objects.get(pk=outcome.attempt_id)
        self.assertEqual(attempt.notification_status, 'pending')
        self.assertEqual(attempt.notification_attempt_count, 0)
        with patch(
            'crm.services.onboarding_notify.notify_staff_onboarding_submitted',
        ) as notify:
            result = reconcile_onboarding_staff_notifications()
        self.assertEqual(result, {'eligible': 1, 'delivered': 1, 'failed': 0})
        notify.assert_called_once_with(self.user)
        attempt.refresh_from_db()
        self.assertEqual(attempt.notification_status, 'succeeded')

    def test_stale_notification_processing_lease_is_reclaimed(self):
        final = OnboardingTurnResult(
            assistant_message='Done', is_complete=True,
        )
        with patch(
            'questionnaires.services_onboarding_ai.run_onboarding_turn',
            return_value=final,
        ), patch(
            'questionnaires.services_onboarding_ai._enqueue_staff_notification',
        ):
            outcome = run_logical_member_turn(
                self.conversation, uuid.uuid4(), 'answer',
            )
        OnboardingTurnAttempt.objects.filter(pk=outcome.attempt_id).update(
            notification_status='processing',
            notification_attempt_count=1,
            notification_lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        with patch(
            'crm.services.onboarding_notify.notify_staff_onboarding_submitted',
        ) as notify:
            result = reconcile_onboarding_staff_notifications()

        self.assertEqual(result['delivered'], 1)
        notify.assert_called_once()
        attempt = OnboardingTurnAttempt.objects.get(pk=outcome.attempt_id)
        self.assertEqual(attempt.notification_status, 'succeeded')
        self.assertEqual(attempt.notification_attempt_count, 2)
        self.assertIsNone(attempt.notification_lease_expires_at)

    def test_stale_lease_can_be_retried(self):
        request_id = uuid.uuid4()
        now = timezone.now()
        OnboardingTurnAttempt.objects.create(
            conversation=self.conversation,
            request_id=request_id,
            member_message_hash=(
                hashlib.sha256(b'answer').hexdigest()
            ),
            admitted_version=0,
            transport='stream',
            status='processing',
            provider_call_count=1,
            started_at=now - timedelta(minutes=1),
            lease_expires_at=now - timedelta(seconds=1),
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Recovered'),
        ):
            run_logical_member_turn(
                self.conversation, request_id, 'answer',
            )
        attempt = OnboardingTurnAttempt.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'succeeded')
        self.assertEqual(attempt.provider_call_count, 2)

    def test_database_rejects_two_processing_attempts(self):
        now = timezone.now()
        defaults = dict(
            conversation=self.conversation,
            member_message_hash='a' * 64,
            admitted_version=0,
            transport='stream',
            status='processing',
            started_at=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        OnboardingTurnAttempt.objects.create(
            request_id=uuid.uuid4(), **defaults,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OnboardingTurnAttempt.objects.create(
                request_id=uuid.uuid4(), **defaults,
            )

    @override_settings(
        ONBOARDING_AI_DEADLINE_SECONDS='999',
        ONBOARDING_AI_MAX_ATTEMPTS='999',
    )
    def test_runtime_limits_are_clamped(self):
        self.assertEqual(onboarding_ai_deadline_seconds(), 28)
        self.assertEqual(onboarding_ai_max_attempts(), 3)

    @override_settings(
        ONBOARDING_AI_DEADLINE_SECONDS='bad',
        ONBOARDING_AI_MAX_ATTEMPTS='bad',
    )
    def test_invalid_runtime_limits_use_safe_defaults(self):
        self.assertEqual(onboarding_ai_deadline_seconds(), 25)
        self.assertEqual(onboarding_ai_max_attempts(), 2)
