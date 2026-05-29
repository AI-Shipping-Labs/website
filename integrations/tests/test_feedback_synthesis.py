"""Tests for the Django-independent feedback synthesis callable (#805).

The LLM is mocked at the service boundary in every test (CI never hits a
live provider). The callable contract, gating, empty handling, error
surfacing, the trace hook, and Django-independence are covered here.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from integrations.services.feedback_synthesis import (
    FeedbackSynthesisEmpty,
    FeedbackSynthesisResult,
    FeedbackSynthesisUnavailable,
    SprintFeedbackInput,
    TraceSink,
    synthesize_feedback,
)
from integrations.services.llm import LLMError, LLMResult

_VALID_TOOL_INPUT = {
    'themes': [
        {'title': 'Pacing', 'summary': 'Too fast in week 2.', 'supporting_count': 3},
    ],
    'what_went_well': ['Office hours were useful.'],
    'what_to_improve': ['Slow down the mid-sprint ramp.'],
    'recommendations': [
        {'recommendation': 'Add a buffer week.', 'rationale': 'Members fell behind.'},
    ],
    'next_sprint_signal': 'Most plan to return.',
    'response_count': 0,
}


def _input(response_count=2):
    return SprintFeedbackInput(
        sprint_name='May Cohort',
        start_date='2026-05-01',
        duration_weeks=6,
        response_count=response_count,
        responses=[
            {'answers': [('How was it?', 'long_text', 'Great pacing')]},
            {'answers': [('Rate it', 'scale', '4')]},
        ],
    )


class SynthesizeFeedbackContractTest(SimpleTestCase):
    """The happy path: mocked tool_input -> validated result model."""

    def test_returns_validated_result_from_tool_input(self):
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ):
            result = synthesize_feedback(_input(response_count=2))

        self.assertIsInstance(result, FeedbackSynthesisResult)
        self.assertEqual(result.themes[0].title, 'Pacing')
        self.assertEqual(result.what_went_well, ['Office hours were useful.'])
        self.assertEqual(result.what_to_improve, ['Slow down the mid-sprint ramp.'])
        self.assertEqual(result.recommendations[0].recommendation, 'Add a buffer week.')
        self.assertEqual(result.next_sprint_signal, 'Most plan to return.')
        # The echoed count comes from the input, not the model payload.
        self.assertEqual(result.response_count, 2)

    def test_passes_tool_and_tool_choice_using_model_schema(self):
        schema = FeedbackSynthesisResult.model_json_schema()
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ) as mock_complete:
            synthesize_feedback(_input())

        _, kwargs = mock_complete.call_args
        self.assertEqual(len(kwargs['tools']), 1)
        self.assertEqual(kwargs['tools'][0]['input_schema'], schema)
        self.assertEqual(kwargs['tool_choice']['type'], 'tool')
        self.assertEqual(
            kwargs['tool_choice']['name'], kwargs['tools'][0]['name'],
        )


class SynthesizeFeedbackGatingTest(SimpleTestCase):
    def test_disabled_raises_unavailable_without_calling_complete(self):
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=False,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
        ) as mock_complete:
            with self.assertRaises(FeedbackSynthesisUnavailable):
                synthesize_feedback(_input())
        mock_complete.assert_not_called()

    def test_unavailable_is_an_llm_error_subclass(self):
        # Callers catching LLMError also catch the disabled case.
        self.assertTrue(issubclass(FeedbackSynthesisUnavailable, LLMError))

    def test_empty_input_raises_empty_without_calling_complete(self):
        empty = SprintFeedbackInput(
            sprint_name='May Cohort', response_count=0, responses=[],
        )
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
        ) as mock_complete:
            with self.assertRaises(FeedbackSynthesisEmpty):
                synthesize_feedback(empty)
        mock_complete.assert_not_called()


class SynthesizeFeedbackErrorTest(SimpleTestCase):
    def test_llm_error_propagates(self):
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            side_effect=LLMError('boom'),
        ):
            with self.assertRaises(LLMError):
                synthesize_feedback(_input())

    def test_missing_tool_input_raises_llm_error(self):
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(text='no tool used', tool_input=None),
        ):
            with self.assertRaises(LLMError):
                synthesize_feedback(_input())

    def test_invalid_tool_input_raises_llm_error(self):
        bad = {'themes': 'not a list'}
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(tool_input=bad),
        ):
            with self.assertRaises(LLMError):
                synthesize_feedback(_input())


class _RecordingSink(TraceSink):
    def __init__(self):
        self.request = None
        self.result = None
        self.latency = None
        self.parsed = None

    def on_request(self, *, system, messages, tool):
        self.request = {'system': system, 'messages': messages, 'tool': tool}

    def on_result(self, *, result, latency_seconds):
        self.result = result
        self.latency = latency_seconds

    def on_parsed(self, *, parsed):
        self.parsed = parsed


class SynthesizeFeedbackTraceTest(SimpleTestCase):
    def test_trace_sink_receives_request_result_and_parsed(self):
        sink = _RecordingSink()
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ):
            result = synthesize_feedback(_input(), trace=sink)

        self.assertIsNotNone(sink.request)
        self.assertIn('Sprint: May Cohort', sink.request['messages'][0]['content'])
        self.assertTrue(sink.request['system'])
        self.assertEqual(sink.request['tool']['name'], 'sprint_feedback_synthesis')
        self.assertIsNotNone(sink.result)
        self.assertIsNotNone(sink.latency)
        self.assertIs(sink.parsed, result)

    def test_trace_none_runs_without_error(self):
        with patch(
            'integrations.services.feedback_synthesis.llm.is_enabled',
            return_value=True,
        ), patch(
            'integrations.services.feedback_synthesis.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ):
            result = synthesize_feedback(_input(), trace=None)
        self.assertIsInstance(result, FeedbackSynthesisResult)


class FeedbackSynthesisImportIsolationTest(SimpleTestCase):
    """The synthesis module must stay Django-independent so #809 can wrap it."""

    def test_module_source_imports_no_django_or_app_models(self):
        import inspect

        from integrations.services import feedback_synthesis

        source = inspect.getsource(feedback_synthesis)
        forbidden = [
            'from django.db',
            'import django.db',
            'from django.http',
            'from plans',
            'import plans',
            'from questionnaires',
            'import questionnaires',
            'HttpRequest',
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, source,
                f'feedback_synthesis must not reference {needle!r}',
            )

    def test_loaded_module_has_no_django_db_dependency(self):
        import sys

        from integrations.services import feedback_synthesis

        # The module is importable; assert its own globals carry no Django
        # model/request handles (a defensive check beyond the source scan).
        module_globals = vars(feedback_synthesis)
        self.assertNotIn('models', module_globals)
        self.assertNotIn('HttpRequest', module_globals)
        # Sanity: the module is genuinely loaded.
        self.assertIn('integrations.services.feedback_synthesis', sys.modules)
