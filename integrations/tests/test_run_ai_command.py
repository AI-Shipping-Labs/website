"""Tests for the standalone AI-eval runner command (issue #809).

Every test runs the command in mock mode (the default) so CI never hits a
live provider. They assert: a mock run of each callable writes
``output.json`` + a documented-shape ``trace.json``; the default makes no
network call; ``--live`` is gated on ``is_enabled()`` and never calls the
backend when disabled; ``--mock`` + ``--live`` is rejected; a callable
error writes a trace with the captured ``error`` and exits non-zero; the
API key never leaks into stdout or ``trace.json``; a malformed fixture
yields a clear file/field-named parse error; and ``--suite`` writes
per-fixture out-subdirs plus ``summary.json`` with a results table.
"""

import json
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

import integrations.services.ai_eval as _ai_eval

FIXTURES_DIR = Path(_ai_eval.__file__).resolve().parent / 'fixtures'
FEEDBACK_FIXTURE = FIXTURES_DIR / 'feedback' / 'sprint_basic.json'
ONBOARDING_FIXTURE = FIXTURES_DIR / 'onboarding' / 'mid_conversation.yaml'

_SECRET_KEY = 'sk-test-SECRET-KEY-DO-NOT-LEAK-12345'


def _load_trace(out_dir):
    return json.loads((Path(out_dir) / 'trace.json').read_text(encoding='utf-8'))


class RunAiMockRunTest(TestCase):
    """Single mock runs write output.json + a documented-shape trace.json."""

    def test_feedback_mock_run_writes_output_and_trace(self):
        out = Path(self._out())
        stdout = StringIO()
        call_command(
            'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
            '--out', str(out), stdout=stdout,
        )
        self.assertTrue((out / 'output.json').exists())
        self.assertTrue((out / 'trace.json').exists())
        output = json.loads((out / 'output.json').read_text())
        self.assertEqual(output['response_count'], 3)

        trace = _load_trace(out)
        for field in (
            'callable', 'provider', 'model', 'system_prompt', 'messages',
            'tool', 'raw_result', 'latency_seconds', 'parsed_output',
        ):
            self.assertIn(field, trace)
        self.assertEqual(trace['callable'], 'feedback')
        self.assertIn('input_schema', trace['tool'])
        self.assertIn('text', trace['raw_result'])
        self.assertIn('tool_name', trace['raw_result'])
        self.assertIn('tool_input', trace['raw_result'])
        self.assertIsNotNone(trace['parsed_output'])

    def test_onboarding_mock_run_writes_output_and_trace(self):
        out = Path(self._out())
        call_command(
            'run_ai', 'onboarding', '--input', str(ONBOARDING_FIXTURE),
            '--out', str(out), stdout=StringIO(),
        )
        self.assertTrue((out / 'output.json').exists())
        trace = _load_trace(out)
        self.assertEqual(trace['callable'], 'onboarding')
        self.assertIn('input_schema', trace['tool'])
        self.assertIsNotNone(trace['parsed_output'])

    def test_token_usage_is_null_when_result_lacks_usage(self):
        # The #799 LLMResult exposes no usage; the sink records null, not
        # an error.
        out = Path(self._out())
        call_command(
            'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
            '--out', str(out), stdout=StringIO(),
        )
        self.assertIsNone(_load_trace(out)['token_usage'])

    def _out(self):
        import tempfile
        return tempfile.mkdtemp(prefix='ai_eval_test_')


class RunAiDefaultModeTest(TestCase):
    """Default mode is mock and never touches the live backend."""

    def test_default_mode_makes_no_backend_call(self):
        import tempfile

        out = tempfile.mkdtemp(prefix='ai_eval_test_')
        # If the default path reached the real backend, get_backend would
        # be invoked. Patch it to blow up so any live call fails the test.
        with mock.patch(
            'integrations.services.llm.service.get_backend',
            side_effect=AssertionError('live backend must not be called in mock mode'),
        ):
            call_command(
                'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
                '--out', str(out), stdout=StringIO(),
            )
        self.assertTrue((Path(out) / 'output.json').exists())


class RunAiLiveGatingTest(TestCase):
    """--live requires is_enabled() and never calls the network when off."""

    def test_live_with_disabled_llm_exits_nonzero_and_no_network(self):
        import tempfile

        out = tempfile.mkdtemp(prefix='ai_eval_test_')
        with mock.patch(
            'integrations.services.llm.is_enabled', return_value=False,
        ), mock.patch(
            'integrations.services.llm.service.get_backend',
            side_effect=AssertionError('network must not be called when disabled'),
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
                    '--live', '--out', str(out), stdout=StringIO(),
                )
        self.assertIn('LLM not configured', str(ctx.exception))

    def test_mock_and_live_together_is_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
                '--mock', '--live', stdout=StringIO(),
            )
        self.assertIn('mutually exclusive', str(ctx.exception))


class RunAiErrorPathTest(TestCase):
    """Callable errors write a trace with the error and exit non-zero."""

    def test_callable_error_writes_trace_with_error_and_exits_nonzero(self):
        import tempfile

        out = tempfile.mkdtemp(prefix='ai_eval_test_')
        # Force the callable to fail by making the mock complete raise the
        # LLM service's error inside the run.
        from integrations.services.llm import LLMError

        with mock.patch(
            'integrations.services.ai_eval.mock_llm.mock_complete',
            side_effect=LLMError('boom from provider'),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
                    '--out', str(out), stdout=StringIO(), stderr=StringIO(),
                )
        trace = _load_trace(out)
        self.assertIsNotNone(trace['error'])
        self.assertEqual(trace['error']['type'], 'LLMError')
        self.assertIn('boom from provider', trace['error']['message'])

    def test_malformed_fixture_gives_clear_parse_error(self):
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix='ai_eval_test_'))
        bad = tmp / 'bad.json'
        bad.write_text('{not valid json', encoding='utf-8')
        out = tmp / 'out'
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'run_ai', 'feedback', '--input', str(bad),
                '--out', str(out), stdout=StringIO(), stderr=StringIO(),
            )
        message = str(ctx.exception)
        self.assertIn('FixtureError', message)
        self.assertIn('bad.json', message)


class RunAiSecretScrubbingTest(TestCase):
    """The API key never appears in stdout or in trace.json."""

    def test_api_key_never_in_stdout_or_trace(self):
        import tempfile

        out = tempfile.mkdtemp(prefix='ai_eval_test_')
        stdout = StringIO()
        with self.settings(LLM_API_KEY=_SECRET_KEY):
            call_command(
                'run_ai', 'feedback', '--input', str(FEEDBACK_FIXTURE),
                '--out', str(out), stdout=stdout,
            )
        self.assertNotIn(_SECRET_KEY, stdout.getvalue())
        trace_text = (Path(out) / 'trace.json').read_text(encoding='utf-8')
        self.assertNotIn(_SECRET_KEY, trace_text)


class RunAiSuiteTest(TestCase):
    """--suite runs every fixture, writes per-fixture dirs + summary.json."""

    def test_feedback_suite_writes_subdirs_and_summary(self):
        import tempfile

        out = Path(tempfile.mkdtemp(prefix='ai_eval_test_'))
        stdout = StringIO()
        call_command(
            'run_ai', 'feedback',
            '--suite', str(FIXTURES_DIR / 'feedback'),
            '--out', str(out), stdout=stdout,
        )
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['callable'], 'feedback')
        fixtures = {r['fixture'] for r in summary['results']}
        self.assertIn('sprint_basic.json', fixtures)
        self.assertIn('sprint_minimal.yaml', fixtures)
        for row in summary['results']:
            self.assertEqual(row['status'], 'ok')
            self.assertTrue((Path(row['out_dir']) / 'output.json').exists())
            self.assertTrue((Path(row['out_dir']) / 'trace.json').exists())
        # The printed table lists each fixture.
        table = stdout.getvalue()
        self.assertIn('sprint_basic.json', table)
        self.assertIn('sprint_minimal.yaml', table)

    def test_onboarding_starter_fixtures_run_under_mock(self):
        import tempfile

        out = Path(tempfile.mkdtemp(prefix='ai_eval_test_'))
        call_command(
            'run_ai', 'onboarding',
            '--suite', str(FIXTURES_DIR / 'onboarding'),
            '--out', str(out), stdout=StringIO(),
        )
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        self.assertTrue(summary['results'])
        for row in summary['results']:
            self.assertEqual(row['status'], 'ok')
