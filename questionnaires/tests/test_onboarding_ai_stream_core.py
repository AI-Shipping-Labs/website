"""Tests for the pure streaming onboarding core (issue #806).

``stream_onboarding_turn`` is the streaming counterpart to
``run_onboarding_turn``. It stays Django-independent: the LLM is mocked at
the ``integrations.services.llm`` boundary (``stream`` + ``complete``) and
the tests run as :class:`SimpleTestCase` (no DB).

Covers: streamed-delta assembly, the deterministic greeting turn (no model
call), that the final ``OnboardingTurnResult`` is equivalent to the
non-streaming ``run_onboarding_turn`` for the same input, and the
mid-stream / open error contract.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, tag

from integrations.services.llm import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    LLMError,
    LLMResult,
    StreamEvent,
)
from questionnaires.onboarding_ai import (
    GREETING,
    OnboardingExtraction,
    OnboardingTurnResult,
    TraceSink,
    run_onboarding_turn,
    stream_onboarding_turn,
)
from questionnaires.tests.test_onboarding_ai_core import CATALOG, VALID_EXTRACTION


def _scripted_stream(deltas, final_text, *, tool_input=None, tool_name=None):
    """Build a stream() side-effect yielding deltas then a done event.

    The terminal ``done`` event carries the assembled ``LLMResult`` (text
    plus, on the final turn, ``tool_input``) — mirroring the real backend,
    which streams text deltas then assembles a final message that includes
    any tool call. ``stream_onboarding_turn`` builds the authoritative
    result from THIS single generation (no second ``complete`` call).
    """
    def gen(messages, **kwargs):
        for d in deltas:
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text=d)
        yield StreamEvent(
            kind=STREAM_DONE,
            result=LLMResult(
                text=final_text, tool_input=tool_input, tool_name=tool_name,
            ),
        )
    return gen


@tag('core')
class StreamOnboardingTurnTest(SimpleTestCase):

    def test_opening_turn_greets_without_model_call(self):
        with patch('questionnaires.onboarding_ai.llm.stream') as mock_stream, \
             patch('questionnaires.onboarding_ai.llm.complete') as mock_comp:
            items = list(stream_onboarding_turn(
                [], member_message=None, persona_catalog=CATALOG,
            ))
        mock_stream.assert_not_called()
        mock_comp.assert_not_called()
        # Greeting yielded as a single delta, then the result.
        deltas = [i for i in items if isinstance(i, str)]
        result = items[-1]
        self.assertEqual(''.join(deltas), GREETING)
        self.assertIsInstance(result, OnboardingTurnResult)
        self.assertEqual(result.assistant_message, GREETING)
        self.assertFalse(result.is_complete)

    def test_streamed_deltas_assemble_to_assistant_reply(self):
        reply = 'Got it. What blocks your consistency?'
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(
                ['Got it. ', 'What blocks ', 'your consistency?'], reply,
            ),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
        ) as mock_complete:
            items = list(stream_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='I want to ship a RAG app',
                persona_catalog=CATALOG,
            ))
        # Single-generation contract (#821): an intermediate streaming turn
        # makes exactly ONE model generation (the stream) and no redundant
        # second ``complete`` round-trip.
        mock_complete.assert_not_called()
        deltas = [i for i in items if isinstance(i, str)]
        result = items[-1]
        self.assertEqual(''.join(deltas), reply)
        self.assertIsInstance(result, OnboardingTurnResult)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.assistant_message, reply)

    def test_final_result_matches_non_streaming_completion(self):
        # The final-turn generation returns the SAME text + tool call to
        # both paths -> identical result (the persisted answers therefore
        # match the non-streaming path). The streaming path derives this
        # from the single streamed generation's terminal ``done`` event;
        # the non-streaming path from ``complete`` — same helper, same shape.
        tool_result = LLMResult(
            text="Thanks, that's everything.",
            tool_input=dict(VALID_EXTRACTION),
            tool_name='record_onboarding',
        )
        transcript = [{'role': 'assistant', 'content': GREETING}]
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=tool_result,
        ):
            non_streaming = run_onboarding_turn(
                transcript, member_message='all answered',
                persona_catalog=CATALOG,
            )

        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(
                ['Thanks, '], "Thanks, that's everything.",
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
        ) as mock_complete:
            items = list(stream_onboarding_turn(
                transcript, member_message='all answered',
                persona_catalog=CATALOG,
            ))
        streamed_result = items[-1]

        # Even the completing turn makes no redundant second generation:
        # the tool call rode the SAME streamed generation.
        mock_complete.assert_not_called()
        self.assertTrue(streamed_result.is_complete)
        self.assertIsInstance(streamed_result.extraction, OnboardingExtraction)
        self.assertEqual(
            streamed_result.is_complete, non_streaming.is_complete,
        )
        self.assertEqual(
            streamed_result.extraction.model_dump(),
            non_streaming.extraction.model_dump(),
        )
        self.assertEqual(
            [a.model_dump() for a in streamed_result.answers],
            [a.model_dump() for a in non_streaming.answers],
        )

    def test_open_error_propagates_as_llmerror(self):
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=LLMError('stream open failed'),
        ):
            with self.assertRaises(LLMError):
                list(stream_onboarding_turn(
                    [{'role': 'assistant', 'content': GREETING}],
                    member_message='hi', persona_catalog=CATALOG,
                ))

    def test_mid_stream_error_surfaces_after_first_delta(self):
        def gen(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='first ')
            raise LLMError('LLM stream failed mid-response')

        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=gen,
        ):
            stream = stream_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='hi', persona_catalog=CATALOG,
            )
            first = next(stream)
            self.assertEqual(first, 'first ')
            with self.assertRaises(LLMError):
                next(stream)

    def test_intermediate_turn_makes_exactly_one_generation(self):
        # #821 root-cause fix: an intermediate (non-completing) streaming
        # turn must run exactly ONE model generation. We count every entry
        # into the LLM service (stream + complete); the count must be 1.
        calls = {'stream': 0, 'complete': 0}
        reply = 'Tell me more about your timeline.'

        def counting_stream(messages, **kwargs):
            calls['stream'] += 1
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text=reply)
            yield StreamEvent(
                kind=STREAM_DONE, result=LLMResult(text=reply),
            )

        def counting_complete(*args, **kwargs):
            calls['complete'] += 1
            return LLMResult(text=reply)

        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=counting_stream,
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=counting_complete,
        ):
            list(stream_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='I have variable hours',
                persona_catalog=CATALOG,
            ))
        self.assertEqual(calls['stream'], 1)
        self.assertEqual(calls['complete'], 0)
        self.assertEqual(calls['stream'] + calls['complete'], 1)

    def test_turn_latency_is_measured_via_trace_hook(self):
        # #821: per-turn server-side latency must be observable via the
        # existing TraceSink.on_result(latency_seconds=...) hook. Assert the
        # measurement exists and is a non-negative number (no flaky
        # wall-clock threshold).
        captured = {}

        class RecordingSink(TraceSink):
            def on_result(self, *, result, latency_seconds):
                captured['latency'] = latency_seconds

        reply = 'What does your week look like?'
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream([reply], reply),
        ):
            list(stream_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='hi', persona_catalog=CATALOG,
                trace=RecordingSink(),
            ))
        self.assertIn('latency', captured)
        self.assertIsInstance(captured['latency'], float)
        self.assertGreaterEqual(captured['latency'], 0.0)
