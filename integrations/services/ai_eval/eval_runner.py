"""Eval + alignment orchestration for the AI-eval suite (issue #812).

This is the new layer ON TOP OF #809: the fixture-loading + callable run +
trace capture half is reused unchanged (``runner.run_callable`` +
:class:`FileTraceSink`); only the judge pass, the report aggregation, and
the judge-vs-human alignment are added here.

Two entry points, both Django-independent (they import only the eval
package, the two callables via ``runner``, and the LLM service) and free
of any Logfire import (#813):

- :func:`run_eval` -- run every dataset fixture through the callable, run
  the judge over each captured ``parsed_output``, and build the
  ``eval_report.json`` dict (``% good`` + per-category + deterministic
  metrics + callable-vs-judge cost/latency).
- :func:`run_alignment` -- run the judge over the LABELED scenarios and
  measure agreement against the gold labels (dev vs held-out test,
  reported separately, plus per-scenario disagreement rows).

The judge's usage/cost is read defensively off the ``LLMResult`` exactly
like :class:`FileTraceSink` does (today usage is ``None``; recorded as such
rather than crashing).
"""

from datetime import datetime, timezone

from integrations.services.ai_eval import dataset, judge, metrics, runner
from integrations.services.ai_eval.trace import FileTraceSink
from integrations.services.feedback_synthesis import (
    FeedbackSynthesisEmpty,
    FeedbackSynthesisUnavailable,
)
from integrations.services.llm import LLMError


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _extract_usage(result):
    """Read token usage off an ``LLMResult`` defensively (None if absent).

    Mirrors :meth:`FileTraceSink._extract_token_usage` so the judge cost is
    captured the moment the #799 ``LLMResult`` grows a ``usage`` attribute,
    and stays ``None`` (never raising) until then.
    """
    return FileTraceSink._extract_token_usage(result)


def _run_one_callable(callable_name, callable_input, *, provider, model, live):
    """Run one fixture through the callable, returning the result + sink + status.

    Reuses ``runner.run_callable`` + :class:`FileTraceSink` exactly as the
    #809 suite path does. Returns ``(output, sink, ok, error)`` where
    ``output`` is the callable's full return value as a JSON-able dict
    (the onboarding turn result -- ``assistant_message`` / ``is_complete`` /
    ``extraction`` -- or the feedback synthesis result), the shape the
    judge and the deterministic checks both read.
    """
    sink = FileTraceSink(
        callable_name=callable_name,
        provider=provider,
        model=model,
        timestamp_utc=_utc_now(),
    )

    def _invoke():
        result, _headline = runner.run_callable(
            callable_name, callable_input, trace=sink, model=model,
            source='<dataset>',
        )
        return result

    try:
        if live:
            result = _invoke()
        else:
            from integrations.services.ai_eval.mock_llm import patch_llm
            with patch_llm():
                result = _invoke()
    except (
        runner.FixtureError, LLMError,
        FeedbackSynthesisUnavailable, FeedbackSynthesisEmpty,
    ) as exc:
        return None, sink, False, {'type': type(exc).__name__, 'message': str(exc)}
    return result.model_dump(mode='json'), sink, True, None


def _run_judge_for(callable_name, callable_input, parsed_output, *, model, live):
    """Run the judge over one captured output. Returns ``(verdict, latency, usage, error)``.

    In mock mode the judge runs under the same #809 ``patch_llm`` stub,
    which returns a schema-valid canned verdict; on ``--live`` it hits the
    real provider.
    """
    def _invoke():
        return judge.run_judge(callable_name, callable_input, parsed_output, model=model)

    try:
        if live:
            verdict, latency, raw = _invoke()
        else:
            from integrations.services.ai_eval.mock_llm import patch_llm
            with patch_llm():
                verdict, latency, raw = _invoke()
    except LLMError as exc:
        return None, None, None, {'type': type(exc).__name__, 'message': str(exc)}
    return verdict, latency, _extract_usage(raw), None


def run_eval(callable_name, dataset_dir, *, provider, model, live):
    """Run the full eval: callable + judge over a dataset, build the report.

    Returns ``(report, per_fixture_outputs)`` where ``report`` is the
    :func:`metrics.aggregate_report` dict and ``per_fixture_outputs`` maps
    each scenario id to its callable ``parsed_output`` + trace dict (so the
    command can write per-fixture artifacts the way #809 does).
    """
    scenarios = dataset.load_dataset(dataset_dir)
    rows = []
    outputs = {}
    for scenario in scenarios:
        meta = scenario['meta']
        callable_input = scenario['callable_input']
        output, sink, ok, error = _run_one_callable(
            callable_name, callable_input, provider=provider, model=model, live=live,
        )
        parsed_output = output

        judge_label = None
        judge_latency = None
        judge_usage = None
        judge_reasoning = None
        judge_failure_category = None
        judge_error = None
        if ok:
            verdict, judge_latency, judge_usage, judge_error = _run_judge_for(
                callable_name, callable_input, parsed_output, model=model, live=live,
            )
            if verdict is not None:
                judge_label = verdict.label.value
                judge_reasoning = verdict.reasoning
                judge_failure_category = verdict.failure_category or None

        checks = metrics.deterministic_checks(
            callable_name, parsed_output, meta.get('expected'),
        ) if ok else {}

        rows.append({
            'id': meta['id'],
            'category': meta.get('category', 'uncategorized'),
            'phrasing': meta.get('phrasing'),
            'source': meta.get('source'),
            'status': 'ok' if ok else 'error',
            'error': error or judge_error,
            'judge_label': judge_label,
            'judge_reasoning': judge_reasoning,
            'judge_failure_category': judge_failure_category,
            'checks': checks,
            'callable_latency_seconds': sink.latency_seconds,
            'judge_latency_seconds': judge_latency,
            'callable_token_usage': sink.token_usage,
            'judge_token_usage': judge_usage,
        })
        outputs[meta['id']] = {
            'parsed_output': parsed_output,
            'trace': sink.to_dict(),
        }

    run_metadata = {
        'provider': provider,
        'model': model,
        'dataset_dir': str(dataset_dir),
        'mode': 'live' if live else 'mock',
        'timestamp_utc': _utc_now(),
        'judge_prompt_version': judge.JUDGE_PROMPT_VERSION,
    }
    report = metrics.aggregate_report(
        callable_name=callable_name, scenarios=rows, run_metadata=run_metadata,
    )
    return report, outputs


def run_alignment(callable_name, dataset_dir, labels_path, *, provider, model, live):
    """Run the judge over labeled scenarios and measure judge-vs-human agreement.

    Joins each dataset scenario to its gold label by ``id``, runs the
    judge, and computes dev/test alignment metrics SEPARATELY (no tuning
    leakage) plus per-scenario disagreement rows. Scenarios without a gold
    label are skipped from the metrics (you cannot measure agreement
    without a gold standard) but reported in ``unlabeled_ids``.
    """
    scenarios = dataset.load_dataset(dataset_dir)
    labels = dataset.load_labels(labels_path)

    judged = []
    unlabeled_ids = []
    for scenario in scenarios:
        meta = scenario['meta']
        scenario_id = meta['id']
        label_row = labels.get(scenario_id)
        human_label = (label_row or {}).get('correctness_label') or None
        split = (label_row or {}).get('split') or 'dev'

        output, sink, ok, error = _run_one_callable(
            callable_name, scenario['callable_input'],
            provider=provider, model=model, live=live,
        )
        verdict = None
        if ok:
            verdict, _latency, _usage, _err = _run_judge_for(
                callable_name, scenario['callable_input'], output,
                model=model, live=live,
            )

        if human_label not in (metrics.PASS, metrics.FAIL):
            unlabeled_ids.append(scenario_id)
            continue

        judged.append({
            'id': scenario_id,
            'split': split,
            'human_label': human_label,
            'judge_label': verdict.label.value if verdict is not None else None,
            'judge_reasoning': verdict.reasoning if verdict is not None else None,
        })

    alignment = metrics.align_by_split(judged)
    return {
        'callable': callable_name,
        'dataset_dir': str(dataset_dir),
        'labels_path': str(labels_path),
        'judge_prompt_version': judge.JUDGE_PROMPT_VERSION,
        'mode': 'live' if live else 'mock',
        'alignment': alignment,
        'labeled_count': len(judged),
        'unlabeled_ids': unlabeled_ids,
    }


__all__ = ['run_eval', 'run_alignment']
