"""CI-safe unit tests for the live-judge helpers (issue #811).

These cover the judge helper logic and the cost tracker with a MOCKED LLM
judge, so the helper code is exercised in the default suite WITHOUT any
live provider call. They live in a Django app ``tests/`` module (so
``manage.py test`` collects them), are NOT marked ``live_judge``, and never
hit the network -- ``llm.complete`` is patched.

The live scenario tests themselves (``tests/live_judge/test_*_judge.py``)
are the real-provider counterpart and are excluded from CI by the
``live_judge`` marker + their location outside any Django app and outside
``playwright_tests/``.
"""

import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from tests.live_judge import cost_tracker
from tests.live_judge.judge import (
    JudgeCriterion,
    JudgeFeedback,
    assert_criteria,
    render_output,
    resolve_judge_model,
)


def _fake_result(criteria):
    """Build a fake #799 LLMResult whose tool_input is a JudgeFeedback dump."""
    feedback = JudgeFeedback(criteria=criteria, feedback='summary')
    return mock.Mock(tool_input=feedback.model_dump(), text='')


class AssertCriteriaTest(SimpleTestCase):
    """The judge helper logic, with the LLM judge mocked out."""

    def test_passes_when_all_criteria_pass(self):
        result = _fake_result([
            JudgeCriterion(
                criterion_description='c1', judgement='ok', passed=True
            ),
            JudgeCriterion(
                criterion_description='c2', judgement='ok2', passed=True
            ),
        ])
        with mock.patch(
            'tests.live_judge.judge.llm.complete', return_value=result
        ) as complete:
            feedback = assert_criteria('output text', ['c1', 'c2'])
        self.assertEqual(len(feedback.criteria), 2)
        complete.assert_called_once()

    def test_raises_with_description_and_judgement_on_failure(self):
        result = _fake_result([
            JudgeCriterion(
                criterion_description='no persona leak',
                judgement='the message contains the name Taylor',
                passed=False,
            ),
        ])
        with mock.patch('tests.live_judge.judge.llm.complete', return_value=result):
            with self.assertRaises(AssertionError) as ctx:
                assert_criteria('Taylor', ['no persona leak'])
        message = str(ctx.exception)
        self.assertIn('no persona leak', message)
        self.assertIn('the message contains the name Taylor', message)

    def test_judge_uses_structured_output_tool_path(self):
        result = _fake_result([
            JudgeCriterion(criterion_description='c', judgement='j', passed=True),
        ])
        with mock.patch(
            'tests.live_judge.judge.llm.complete', return_value=result
        ) as complete:
            assert_criteria('out', ['c'])
        _, kwargs = complete.call_args
        # Structured-output tool path: a tool built from the schema is forced.
        self.assertEqual(len(kwargs['tools']), 1)
        self.assertIn('input_schema', kwargs['tools'][0])
        self.assertEqual(kwargs['tool_choice']['type'], 'tool')
        self.assertEqual(kwargs['tool_choice']['name'], kwargs['tools'][0]['name'])

    @override_settings(LLM_JUDGE_MODEL='glm-judge', LLM_MODEL='glm-assistant')
    def test_judge_model_resolves_from_judge_config(self):
        self.assertEqual(resolve_judge_model(), 'glm-judge')

    @override_settings(LLM_JUDGE_MODEL='', LLM_MODEL='glm-assistant')
    def test_judge_model_falls_back_to_llm_model(self):
        self.assertEqual(resolve_judge_model(), 'glm-assistant')

    @override_settings(LLM_JUDGE_MODEL='glm-judge', LLM_MODEL='glm-assistant')
    def test_resolved_judge_model_passed_to_complete(self):
        result = _fake_result([
            JudgeCriterion(criterion_description='c', judgement='j', passed=True),
        ])
        with mock.patch(
            'tests.live_judge.judge.llm.complete', return_value=result
        ) as complete:
            assert_criteria('out', ['c'])
        _, kwargs = complete.call_args
        self.assertEqual(kwargs['model'], 'glm-judge')

    def test_render_output_serializes_pydantic_model(self):
        feedback = JudgeFeedback(
            criteria=[
                JudgeCriterion(criterion_description='c', judgement='j', passed=True)
            ],
            feedback='holistic',
        )
        rendered = render_output(feedback)
        self.assertIn('holistic', rendered)
        self.assertIn('criteria', rendered)


class CostTrackerTest(SimpleTestCase):
    """Cost tracker JSONL append + defensive zero-usage summary."""

    def setUp(self):
        cost_tracker.reset_cost_file()
        self.addCleanup(cost_tracker.reset_cost_file)

    def test_capture_records_zero_usage_when_result_has_no_tokens(self):
        # The #799 LLMResult exposes no token usage; capture must not crash.
        result = mock.Mock(spec=[])  # no usage / input_tokens attributes
        cost_tracker.capture_usage(
            'glm-5.1', result, criteria_total=3, criteria_passed=2
        )
        lines = cost_tracker.COST_FILE.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry['model'], 'glm-5.1')
        self.assertEqual(entry['input_tokens'], 0)
        self.assertEqual(entry['output_tokens'], 0)
        self.assertEqual(entry['criteria_total'], 3)
        self.assertEqual(entry['criteria_passed'], 2)

    def test_capture_reads_usage_when_available(self):
        # Forward-compat: if LLMResult grows usage, the tracker reads it.
        usage = mock.Mock(input_tokens=100, output_tokens=50)
        result = mock.Mock(usage=usage)
        cost_tracker.capture_usage('claude-sonnet-4-5', result)
        entry = json.loads(cost_tracker.COST_FILE.read_text().splitlines()[0])
        self.assertEqual(entry['input_tokens'], 100)
        self.assertEqual(entry['output_tokens'], 50)

    def test_cost_usd_priced_and_unpriced(self):
        self.assertGreater(
            cost_tracker.cost_usd('claude-sonnet-4-5', 1_000_000, 0), 0
        )
        self.assertEqual(cost_tracker.cost_usd('unknown-model', 1_000_000, 0), 0.0)

    def test_display_summary_prints_calls_and_pass_pct(self):
        result = mock.Mock(spec=[])
        cost_tracker.capture_usage(
            'glm-5.1', result, criteria_total=4, criteria_passed=3
        )
        cost_tracker.capture_usage(
            'glm-5.1', result, criteria_total=2, criteria_passed=2
        )
        with mock.patch('builtins.print') as printed:
            cost_tracker.display_total_usage()
        output = '\n'.join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn('LLM calls: 2', output)
        # 5 of 6 criteria passed across the two recorded calls.
        self.assertIn('5/6', output)
        self.assertIn('Total cost:', output)

    def test_display_summary_no_file_does_not_crash(self):
        cost_tracker.reset_cost_file()
        with mock.patch('builtins.print') as printed:
            cost_tracker.display_total_usage()
        output = '\n'.join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn('Total cost: $0.000000', output)
        self.assertIn('LLM calls: 0', output)
