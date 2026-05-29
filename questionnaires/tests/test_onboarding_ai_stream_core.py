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
    run_onboarding_turn,
    stream_onboarding_turn,
)
from questionnaires.tests.test_onboarding_ai_core import CATALOG, VALID_EXTRACTION


def _scripted_stream(deltas, final_text):
    """Build a stream() side-effect yielding deltas then a done event."""
    def gen(messages, **kwargs):
        for d in deltas:
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text=d)
        yield StreamEvent(
            kind=STREAM_DONE, result=LLMResult(text=final_text),
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
            return_value=LLMResult(text=reply),
        ):
            items = list(stream_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='I want to ship a RAG app',
                persona_catalog=CATALOG,
            ))
        deltas = [i for i in items if isinstance(i, str)]
        result = items[-1]
        self.assertEqual(''.join(deltas), reply)
        self.assertIsInstance(result, OnboardingTurnResult)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.assistant_message, reply)

    def test_final_result_matches_non_streaming_completion(self):
        # Same scripted final-turn output to both paths -> identical result
        # (the persisted answers therefore match the non-streaming path).
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
            side_effect=_scripted_stream(['Thanks, '], "Thanks, that's everything."),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=tool_result,
        ):
            items = list(stream_onboarding_turn(
                transcript, member_message='all answered',
                persona_catalog=CATALOG,
            ))
        streamed_result = items[-1]

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
