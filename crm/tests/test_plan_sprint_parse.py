"""Tests for the Django-independent `#plan-sprints` parse callable (#890).

The LLM is mocked at the service boundary in every test — CI never hits a
live provider. Covers the gate, the structured-output contract, the
hallucinated-kind drop, and the Django-independence seam (mirrors the
feedback-synthesis import-isolation test).
"""

import inspect
from unittest.mock import patch

from django.test import SimpleTestCase

from crm.services.plan_sprint_parse import (
    PlanSprintParseInput,
    PlanSprintParseResult,
    PlanSprintParseUnavailable,
    parse_plan_sprint_thread,
)
from integrations.services.llm import LLMError, LLMResult

_VALID_TOOL_INPUT = {
    'completed_items': [
        {'item_kind': 'checkpoint', 'item_id': 1, 'confidence': 0.9},
        {'item_kind': 'deliverable', 'item_id': 2, 'confidence': 1.0},
    ],
    'summary': 'Finished the data pipeline and shipped the demo.',
    'blockers': ['Waiting on API access'],
}


def _input():
    return PlanSprintParseInput(
        member_name='Member One',
        plan_goal='Ship an LLM app',
        messages=[('Member One', '2026-05-02T10:00:00+00:00', 'Done with week 1')],
        plan_items=[
            {'item_kind': 'checkpoint', 'item_id': 1, 'description': 'Pipeline'},
            {'item_kind': 'deliverable', 'item_id': 2, 'description': 'Demo'},
        ],
    )


class ParsePlanSprintGateTest(SimpleTestCase):
    def test_disabled_llm_raises_unavailable_and_never_calls_complete(self):
        with patch(
            'crm.services.plan_sprint_parse.llm.is_enabled',
            return_value=False,
        ), patch(
            'crm.services.plan_sprint_parse.llm.complete',
        ) as mock_complete:
            with self.assertRaises(PlanSprintParseUnavailable):
                parse_plan_sprint_thread(_input())
        mock_complete.assert_not_called()


class ParsePlanSprintContractTest(SimpleTestCase):
    def test_returns_validated_result_from_tool_input(self):
        with patch(
            'crm.services.plan_sprint_parse.llm.is_enabled',
            return_value=True,
        ), patch(
            'crm.services.plan_sprint_parse.llm.complete',
            return_value=LLMResult(tool_input=dict(_VALID_TOOL_INPUT)),
        ):
            result = parse_plan_sprint_thread(_input())
        self.assertIsInstance(result, PlanSprintParseResult)
        self.assertEqual(len(result.completed_items), 2)
        self.assertEqual(result.completed_items[0].item_kind, 'checkpoint')
        self.assertEqual(result.completed_items[0].item_id, 1)
        self.assertEqual(result.summary, _VALID_TOOL_INPUT['summary'])
        self.assertEqual(result.blockers, ['Waiting on API access'])

    def test_drops_completion_with_unknown_item_kind(self):
        payload = dict(_VALID_TOOL_INPUT)
        payload['completed_items'] = [
            {'item_kind': 'mystery', 'item_id': 9, 'confidence': 1.0},
            {'item_kind': 'checkpoint', 'item_id': 1, 'confidence': 1.0},
        ]
        with patch(
            'crm.services.plan_sprint_parse.llm.is_enabled',
            return_value=True,
        ), patch(
            'crm.services.plan_sprint_parse.llm.complete',
            return_value=LLMResult(tool_input=payload),
        ):
            result = parse_plan_sprint_thread(_input())
        kinds = [c.item_kind for c in result.completed_items]
        self.assertEqual(kinds, ['checkpoint'])

    def test_no_tool_input_raises_llmerror(self):
        with patch(
            'crm.services.plan_sprint_parse.llm.is_enabled',
            return_value=True,
        ), patch(
            'crm.services.plan_sprint_parse.llm.complete',
            return_value=LLMResult(tool_input=None),
        ):
            with self.assertRaises(LLMError):
                parse_plan_sprint_thread(_input())


class ParsePlanSprintImportIsolationTest(SimpleTestCase):
    """The parse module must stay Django-independent (mirror #805)."""

    def test_module_source_imports_no_django_or_app_models(self):
        from crm.services import plan_sprint_parse

        source = inspect.getsource(plan_sprint_parse)
        forbidden = [
            'from django.db',
            'import django.db',
            'from django.http',
            'from plans',
            'import plans',
            'from crm.models',
            'import crm.models',
            'HttpRequest',
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, source,
                f'plan_sprint_parse must not reference {needle!r}',
            )

    def test_loaded_module_has_no_django_db_dependency(self):
        import sys

        from crm.services import plan_sprint_parse

        module_globals = vars(plan_sprint_parse)
        self.assertNotIn('models', module_globals)
        self.assertNotIn('HttpRequest', module_globals)
        self.assertIn('crm.services.plan_sprint_parse', sys.modules)
