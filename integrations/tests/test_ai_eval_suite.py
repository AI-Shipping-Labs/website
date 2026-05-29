"""Tests for the AI-eval suite: judge, alignment, datasets, runner (#812).

Everything runs under the #809 ``patch_llm`` mock (extended in #812 to also
return a schema-valid canned JUDGE verdict), so CI never hits a live
provider or needs a key. The tests cover:

- The judge pass: the judge produces a schema-valid structured verdict
  (reasoning + label) under the mock, with reasoning ordered before label.
- The alignment math: fed KNOWN human labels + KNOWN judge labels, the
  accuracy/precision/recall/confusion are exactly correct, with ``fail``
  as the positive class, and dev/test reported separately.
- The deterministic per-feature checks (persona-name leak, extraction
  completeness, theme ordering, hallucinated-theme grounding).
- The dataset loader: ``meta`` sidecar is read and stripped so the callable
  adapter input is unchanged; labels join by id.
- The runner end to end: ``--eval`` writes ``eval_report.json`` with
  ``% good`` + per-category + deterministic metrics + cost/latency;
  ``--align`` reports judge-vs-human metrics; ``--live`` is gated.
- No Logfire is imported or emitted during an eval run.
"""

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

import integrations.services.ai_eval as _ai_eval
from integrations.services.ai_eval import dataset, judge, metrics
from integrations.services.ai_eval.mock_llm import patch_llm

FIXTURES = Path(_ai_eval.__file__).resolve().parent / 'fixtures'
LABELS = Path(_ai_eval.__file__).resolve().parent / 'labels'
ONB_DATASET = FIXTURES / 'onboarding' / 'dataset'
FB_DATASET = FIXTURES / 'feedback' / 'dataset'


def _tmp():
    return Path(tempfile.mkdtemp(prefix='ai_eval_812_'))


# --- Alignment metric math (known inputs -> known outputs) ---


class AlignmentMetricsTest(TestCase):
    """Confusion / accuracy / precision / recall on hand-checked inputs."""

    def test_confusion_matrix_with_fail_as_positive(self):
        human = ['fail', 'fail', 'pass', 'pass', 'fail']
        judge_ = ['fail', 'pass', 'pass', 'fail', 'fail']
        # fail=positive: pairs ->
        #  (fail,fail)=TP, (fail,pass)=FN, (pass,pass)=TN,
        #  (pass,fail)=FP, (fail,fail)=TP
        cm = metrics.confusion_matrix(human, judge_)
        self.assertEqual(cm, {'tp': 2, 'fp': 1, 'tn': 1, 'fn': 1})

    def test_alignment_metrics_values(self):
        human = ['fail', 'fail', 'pass', 'pass', 'fail']
        judge_ = ['fail', 'pass', 'pass', 'fail', 'fail']
        m = metrics.alignment_metrics(human, judge_)
        self.assertEqual(m['n'], 5)
        # accuracy = (TP+TN)/n = (2+1)/5
        self.assertAlmostEqual(m['accuracy'], 0.6)
        # precision = TP/(TP+FP) = 2/3
        self.assertAlmostEqual(m['precision'], 2 / 3)
        # recall = TP/(TP+FN) = 2/3
        self.assertAlmostEqual(m['recall'], 2 / 3)

    def test_precision_recall_none_when_no_positive_predictions(self):
        # All pass -> no predicted positives, no actual positives.
        m = metrics.alignment_metrics(['pass', 'pass'], ['pass', 'pass'])
        self.assertEqual(m['accuracy'], 1.0)
        self.assertIsNone(m['precision'])
        self.assertIsNone(m['recall'])

    def test_align_by_split_reports_dev_and_test_separately(self):
        judged = [
            {'id': 'a', 'split': 'dev', 'human_label': 'fail', 'judge_label': 'fail', 'judge_reasoning': 'r'},
            {'id': 'b', 'split': 'dev', 'human_label': 'pass', 'judge_label': 'fail', 'judge_reasoning': 'r'},
            {'id': 'c', 'split': 'test', 'human_label': 'fail', 'judge_label': 'fail', 'judge_reasoning': 'r'},
        ]
        report = metrics.align_by_split(judged)
        self.assertEqual(report['dev']['metrics']['n'], 2)
        self.assertEqual(report['test']['metrics']['n'], 1)
        # The dev FP (b: human pass, judge fail) is a disagreement row.
        dev_disagreements = {r['id'] for r in report['dev']['disagreements']}
        self.assertEqual(dev_disagreements, {'b'})
        self.assertEqual(report['test']['disagreements'], [])

    def test_unlabeled_scenarios_excluded_from_alignment(self):
        judged = [
            {'id': 'a', 'split': 'dev', 'human_label': None, 'judge_label': 'pass', 'judge_reasoning': 'r'},
            {'id': 'b', 'split': 'dev', 'human_label': 'pass', 'judge_label': 'pass', 'judge_reasoning': 'r'},
        ]
        report = metrics.align_by_split(judged)
        self.assertEqual(report['dev']['metrics']['n'], 1)


# --- Deterministic per-feature checks ---


class DeterministicChecksTest(TestCase):
    def test_persona_name_leak_detected(self):
        self.assertTrue(metrics.persona_name_leaked('You are clearly a Priya type.'))
        self.assertFalse(metrics.persona_name_leaked('You are an experienced engineer.'))

    def test_onboarding_no_persona_leak_check(self):
        leaked = metrics.onboarding_checks({'assistant_message': 'Hi Alex, ...'})
        self.assertFalse(leaked['no_persona_leak'])
        clean = metrics.onboarding_checks({'assistant_message': 'Hi there!'})
        self.assertTrue(clean['no_persona_leak'])

    def test_extraction_completeness(self):
        complete = {f: 1 for f in metrics._REQUIRED_EXTRACTION_FIELDS}
        self.assertTrue(metrics.extraction_complete(complete))
        incomplete = dict(complete)
        incomplete['primary_goal'] = ''
        self.assertFalse(metrics.extraction_complete(incomplete))

    def test_correct_persona_uses_expected(self):
        output = {'is_complete': True, 'assistant_message': 'ok',
                  'extraction': {'persona_signal': 'alex'}}
        checks = metrics.onboarding_checks(output, expected={'persona_signal': 'alex'})
        self.assertTrue(checks['correct_persona'])
        wrong = metrics.onboarding_checks(output, expected={'persona_signal': 'priya'})
        self.assertFalse(wrong['correct_persona'])

    def test_feedback_theme_ranking_check(self):
        ordered = {'themes': [{'supporting_count': 5}, {'supporting_count': 3}, {'supporting_count': 1}]}
        self.assertTrue(metrics.feedback_checks(ordered)['theme_ranking_correct'])
        misranked = {'themes': [{'supporting_count': 1}, {'supporting_count': 5}]}
        self.assertFalse(metrics.feedback_checks(misranked)['theme_ranking_correct'])

    def test_feedback_no_hallucinated_themes(self):
        output = {'themes': [{'title': 'Scoping', 'summary': 'scoping ran long'}]}
        grounded = metrics.feedback_checks(output, expected={'input_terms': ['scoping']})
        self.assertTrue(grounded['no_hallucinated_themes'])
        output2 = {'themes': [{'title': 'Pizza Friday', 'summary': 'free pizza'}]}
        hallucinated = metrics.feedback_checks(output2, expected={'input_terms': ['scoping']})
        self.assertFalse(hallucinated['no_hallucinated_themes'])

    def test_recommendations_actionable(self):
        ok = {'recommendations': [{'recommendation': 'Do X', 'rationale': 'because Y'}]}
        self.assertTrue(metrics.feedback_checks(ok)['recommendations_actionable'])
        bad = {'recommendations': [{'recommendation': 'Do X', 'rationale': ''}]}
        self.assertFalse(metrics.feedback_checks(bad)['recommendations_actionable'])

    def test_next_sprint_signal_correct(self):
        no_signal = metrics.feedback_checks(
            {'next_sprint_signal': ''}, expected={'no_signal_expected': True})
        self.assertTrue(no_signal['next_sprint_signal_correct'])
        leaked = metrics.feedback_checks(
            {'next_sprint_signal': 'most will return'}, expected={'no_signal_expected': True})
        self.assertFalse(leaked['next_sprint_signal_correct'])


# --- Judge pass under the mock ---


class JudgePassTest(TestCase):
    def test_judge_returns_schema_valid_verdict_under_mock(self):
        with patch_llm():
            verdict, latency, raw = judge.run_judge(
                'feedback',
                {'sprint_name': 'S'},
                {'themes': [], 'recommendations': []},
            )
        self.assertIsInstance(verdict, judge.JudgeVerdict)
        self.assertIn(verdict.label, (judge.JudgeLabel.PASS, judge.JudgeLabel.FAIL))
        self.assertIsNotNone(verdict.reasoning)
        self.assertIsNotNone(latency)

    def test_verdict_schema_orders_reasoning_before_label(self):
        props = list(judge.JudgeVerdict.model_json_schema()['properties'].keys())
        self.assertLess(props.index('reasoning'), props.index('label'))

    def test_judge_invalid_output_raises(self):
        from integrations.services.llm import LLMError, LLMResult

        def _bad_complete(*a, **k):
            return LLMResult(text='x', tool_input={'label': 'maybe'}, tool_name='record_verdict')

        with mock.patch('integrations.services.ai_eval.judge.llm.complete', _bad_complete):
            with self.assertRaises(LLMError):
                judge.run_judge('feedback', {}, {})


# --- Dataset + label loading ---


class DatasetLoaderTest(TestCase):
    def test_meta_is_stripped_from_callable_input(self):
        data = {'meta': {'id': 'x'}, 'sprint_name': 'S', 'responses': []}
        callable_input, meta = dataset.split_meta(data, source='t')
        self.assertNotIn('meta', callable_input)
        self.assertEqual(meta['id'], 'x')
        self.assertEqual(callable_input['sprint_name'], 'S')

    def test_missing_meta_raises(self):
        with self.assertRaises(dataset.DatasetError):
            dataset.split_meta({'sprint_name': 'S'}, source='t')

    def test_load_onboarding_dataset_parses_with_existing_adapter(self):
        from integrations.services.ai_eval import runner

        scenarios = dataset.load_dataset(ONB_DATASET)
        self.assertGreaterEqual(len(scenarios), 18)
        # Every callable_input must parse with the UNCHANGED #809 adapter.
        for scenario in scenarios:
            runner.build_onboarding_input(
                scenario['callable_input'], source=str(scenario['path']))

    def test_load_feedback_dataset_parses_with_existing_adapter(self):
        from integrations.services.ai_eval import runner

        scenarios = dataset.load_dataset(FB_DATASET)
        self.assertGreaterEqual(len(scenarios), 16)
        for scenario in scenarios:
            runner.build_feedback_input(
                scenario['callable_input'], source=str(scenario['path']))

    def test_datasets_include_expected_fail_scenarios(self):
        onb_cats = {s['meta'].get('category') for s in dataset.load_dataset(ONB_DATASET)}
        fb_cats = {s['meta'].get('category') for s in dataset.load_dataset(FB_DATASET)}
        self.assertIn('failure-injection', onb_cats)
        self.assertIn('failure-injection', fb_cats)

    def test_labels_load_and_join_by_id(self):
        labels = dataset.load_labels(LABELS / 'onboarding_labels.csv')
        scenario_ids = {s['meta']['id'] for s in dataset.load_dataset(ONB_DATASET)}
        # Every dataset scenario id is pre-listed in the label scaffold.
        self.assertTrue(scenario_ids.issubset(set(labels.keys())))
        # Both splits are present and carry pass+fail among example rows.
        splits = {row['split'] for row in labels.values()}
        self.assertEqual(splits, {'dev', 'test'})
        example_labels = {row['correctness_label'] for row in labels.values()
                          if row['correctness_label']}
        self.assertEqual(example_labels, {'pass', 'fail'})


# --- Runner end to end (mock) ---


class RunAiEvalModeTest(TestCase):
    def test_eval_writes_report_with_metrics(self):
        out = _tmp()
        stdout = StringIO()
        call_command(
            'run_ai', 'feedback', '--eval',
            '--suite', str(FB_DATASET), '--out', str(out), stdout=stdout,
        )
        report = json.loads((out / 'eval_report.json').read_text())
        self.assertEqual(report['callable'], 'feedback')
        self.assertEqual(report['percent_good_overall'], 1.0)  # mock judge -> pass
        self.assertIn('percent_good_by_category', report)
        self.assertIn('deterministic_metrics', report)
        self.assertIn('cost', report)
        self.assertIn('latency', report)
        self.assertEqual(report['run_metadata']['mode'], 'mock')
        self.assertEqual(
            report['run_metadata']['judge_prompt_version'],
            judge.JUDGE_PROMPT_VERSION,
        )
        # Cost is "usage unavailable" defensively (no usage on LLMResult).
        self.assertIsNone(report['cost']['callable_token_usage'])
        self.assertIsNone(report['cost']['judge_token_usage'])
        # Printed table surfaces % good.
        self.assertIn('% good', stdout.getvalue())

    def test_eval_writes_per_fixture_artifacts(self):
        out = _tmp()
        call_command(
            'run_ai', 'onboarding', '--eval',
            '--suite', str(ONB_DATASET), '--out', str(out), stdout=StringIO(),
        )
        # One subdir per scenario id with output.json + trace.json.
        self.assertTrue((out / 'onb-persona-alex' / 'output.json').exists())
        self.assertTrue((out / 'onb-persona-alex' / 'trace.json').exists())

    def test_eval_requires_suite(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('run_ai', 'feedback', '--eval', stdout=StringIO())
        self.assertIn('--suite', str(ctx.exception))

    def test_eval_default_mode_makes_no_backend_call(self):
        out = _tmp()
        with mock.patch(
            'integrations.services.llm.service.get_backend',
            side_effect=AssertionError('live backend must not be called in mock eval'),
        ):
            call_command(
                'run_ai', 'feedback', '--eval',
                '--suite', str(FB_DATASET), '--out', str(out), stdout=StringIO(),
            )
        self.assertTrue((out / 'eval_report.json').exists())

    def test_eval_live_gated_on_is_enabled(self):
        with mock.patch('integrations.services.llm.is_enabled', return_value=False):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'run_ai', 'feedback', '--eval', '--live',
                    '--suite', str(FB_DATASET), stdout=StringIO(),
                )
        self.assertIn('LLM not configured', str(ctx.exception))


class RunAiAlignModeTest(TestCase):
    def test_align_reports_judge_vs_human_metrics(self):
        out = _tmp()
        stdout = StringIO()
        call_command(
            'run_ai', 'onboarding', '--align',
            '--suite', str(ONB_DATASET),
            '--labels', str(LABELS / 'onboarding_labels.csv'),
            '--out', str(out), stdout=stdout,
        )
        report = json.loads((out / 'alignment_report.json').read_text())
        self.assertEqual(report['callable'], 'onboarding')
        self.assertIn('alignment', report)
        # The mock judge always returns pass; against the example fail
        # labels that produces false negatives the report must surface.
        dev = report['alignment']['dev']['metrics']
        self.assertGreaterEqual(dev['confusion']['fn'], 1)
        self.assertGreater(report['labeled_count'], 0)
        # Disagreement rows are emitted for inspection.
        self.assertTrue(report['alignment']['dev']['disagreements'])
        text = stdout.getvalue()
        self.assertIn('fail = positive class', text)
        self.assertIn('confusion', text)

    def test_align_requires_labels(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'run_ai', 'onboarding', '--align',
                '--suite', str(ONB_DATASET), stdout=StringIO(),
            )
        self.assertIn('--labels', str(ctx.exception))

    def test_eval_and_align_mutually_exclusive(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'run_ai', 'feedback', '--eval', '--align',
                '--suite', str(FB_DATASET), stdout=StringIO(),
            )
        self.assertIn('mutually exclusive', str(ctx.exception))


class EvalNoLogfireTest(TestCase):
    """An eval run must not import or initialize Logfire (#813)."""

    def test_no_logfire_module_loaded_during_eval(self):
        # Drop any pre-loaded logfire so we can detect a fresh import.
        for name in list(sys.modules):
            if name == 'logfire' or name.startswith('logfire.'):
                del sys.modules[name]
        out = _tmp()
        call_command(
            'run_ai', 'feedback', '--eval',
            '--suite', str(FB_DATASET), '--out', str(out), stdout=StringIO(),
        )
        loaded = [n for n in sys.modules if n == 'logfire' or n.startswith('logfire.')]
        self.assertEqual(loaded, [], f'eval must not import logfire, got {loaded}')

    def test_eval_modules_do_not_import_logfire(self):
        # The docstrings mention the #813 constraint by name; what matters
        # is that no module actually imports or calls logfire.
        from integrations.services.ai_eval import eval_runner
        for module in (judge, metrics, dataset, eval_runner):
            src = Path(module.__file__).read_text(encoding='utf-8')
            self.assertNotIn('import logfire', src,
                             f'{module.__name__} must not import logfire')
            self.assertNotIn('logfire.', src,
                             f'{module.__name__} must not call logfire')
