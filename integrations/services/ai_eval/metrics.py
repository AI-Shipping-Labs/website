"""Alignment + deterministic metrics for the AI-eval suite (issue #812).

Three families of pure functions, all Django-independent and free of any
LLM/network/Logfire dependency so they are trivially unit-testable on
known inputs:

- :func:`alignment_metrics` -- judge-vs-human agreement (accuracy,
  precision, recall, confusion matrix) with ``fail`` as the POSITIVE
  class, because the thing we care about catching is real failures.
- The deterministic per-feature checks (:func:`onboarding_checks`,
  :func:`feedback_checks`) -- objective metrics that need no judge (e.g.
  "no internal persona name leaked", "themes ordered by prevalence").
- :func:`aggregate_report` -- rolls the judged + deterministic results
  into the ``eval_report.json`` shape (``% good`` overall + per category,
  callable-vs-judge cost/latency tracked separately, run metadata).

``fail`` is the positive class throughout the alignment math.
"""

# Internal persona codenames that must never reach the member.
_INTERNAL_PERSONA_NAMES = ('Alex', 'Priya', 'Sam', 'Taylor')

PASS = 'pass'
FAIL = 'fail'


# --- Alignment (judge vs human gold labels) ---


def confusion_matrix(human_labels, judge_labels):
    """Return TP/FP/TN/FN counts treating ``fail`` as the positive class.

    Both arguments are equal-length sequences of ``"pass"``/``"fail"``
    strings, aligned by index (same scenario). A prediction of ``fail`` on
    a true ``fail`` is a true positive (the judge caught a real failure).
    """
    if len(human_labels) != len(judge_labels):
        raise ValueError('human_labels and judge_labels must be the same length')
    tp = fp = tn = fn = 0
    for human, judge in zip(human_labels, judge_labels):
        human_fail = human == FAIL
        judge_fail = judge == FAIL
        if judge_fail and human_fail:
            tp += 1
        elif judge_fail and not human_fail:
            fp += 1
        elif not judge_fail and not human_fail:
            tn += 1
        else:  # judge pass, human fail
            fn += 1
    return {'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn}


def alignment_metrics(human_labels, judge_labels):
    """Compute accuracy/precision/recall + confusion matrix for one split.

    ``fail`` is the positive class. Precision and recall are ``None`` when
    their denominator is zero (no predicted/actual positives) rather than a
    misleading 0.0. Returns a dict with ``n``, ``accuracy``, ``precision``,
    ``recall``, and the ``confusion`` counts.
    """
    cm = confusion_matrix(human_labels, judge_labels)
    tp, fp, tn, fn = cm['tp'], cm['fp'], cm['tn'], cm['fn']
    n = tp + fp + tn + fn
    accuracy = (tp + tn) / n if n else None
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        'n': n,
        'accuracy': accuracy,
        'precision': precision,
        'recall': recall,
        'confusion': cm,
    }


def disagreement_rows(scenarios):
    """Return per-scenario rows where the judge and the human disagree.

    ``scenarios`` is an iterable of dicts each carrying ``id``,
    ``human_label``, ``judge_label`` and ``judge_reasoning``. Only the
    rows where the two labels differ are returned, so a human can inspect
    exactly where the judge diverges from the gold standard.
    """
    rows = []
    for scenario in scenarios:
        if scenario.get('human_label') != scenario.get('judge_label'):
            rows.append({
                'id': scenario.get('id'),
                'human_label': scenario.get('human_label'),
                'judge_label': scenario.get('judge_label'),
                'judge_reasoning': scenario.get('judge_reasoning'),
            })
    return rows


def align_by_split(judged_scenarios):
    """Split judged + labeled scenarios into dev/test and compute alignment.

    ``judged_scenarios`` is an iterable of dicts with ``id``, ``split``
    (``dev``/``test``), ``human_label``, ``judge_label``,
    ``judge_reasoning``. Scenarios without a human label are skipped (you
    cannot measure agreement without a gold label). Dev and test metrics
    are reported SEPARATELY so judge-prompt tuning on dev does not leak
    into the held-out test number.
    """
    by_split = {'dev': [], 'test': []}
    for scenario in judged_scenarios:
        human = scenario.get('human_label')
        judge = scenario.get('judge_label')
        if human not in (PASS, FAIL) or judge not in (PASS, FAIL):
            continue
        split = scenario.get('split') or 'dev'
        by_split.setdefault(split, []).append(scenario)

    report = {}
    for split, rows in by_split.items():
        report[split] = {
            'metrics': alignment_metrics(
                [r['human_label'] for r in rows],
                [r['judge_label'] for r in rows],
            ),
            'disagreements': disagreement_rows(rows),
        }
    return report


# --- Deterministic per-feature checks (no judge needed) ---


def _pct(passed, total):
    """Return ``passed/total`` as a fraction, or ``None`` when total is 0."""
    return (passed / total) if total else None


def persona_name_leaked(assistant_message):
    """True if any internal persona codename appears in member-facing text."""
    text = assistant_message or ''
    return any(name in text for name in _INTERNAL_PERSONA_NAMES)


# Extraction fields that must be present + non-empty for a "complete"
# onboarding extraction. Mirrors the required scalar fields on
# OnboardingExtraction.
_REQUIRED_EXTRACTION_FIELDS = (
    'persona_signal', 'eng_comfort', 'ai_comfort', 'primary_goal',
    'goal_category', 'time_commitment_hours_per_week', 'time_profile',
    'main_blocker', 'project_stage', 'target_outcome', 'career_direction',
    'coding_agent_use', 'plan_horizon',
)


def extraction_complete(extraction):
    """True if every required extraction field is present and non-empty.

    A field is "missing" when absent, ``None``, or an empty string. Zero
    is a valid numeric value (e.g. ``time_commitment_hours_per_week``) and
    is NOT treated as missing.
    """
    if not extraction:
        return False
    for field in _REQUIRED_EXTRACTION_FIELDS:
        if field not in extraction:
            return False
        value = extraction[field]
        if value is None or value == '':
            return False
    return True


def onboarding_checks(output, expected=None):
    """Deterministic per-scenario checks for one onboarding output.

    Returns a dict of booleans (``None`` when not applicable to this
    scenario). ``output`` is the callable's parsed output dict; ``expected``
    is the optional ``meta.expected`` block (e.g. an unambiguous archetype
    or required extraction fields).
    """
    output = output or {}
    extraction = output.get('extraction')
    checks = {
        'no_persona_leak': not persona_name_leaked(output.get('assistant_message')),
        'extraction_complete': None,
        'correct_persona': None,
    }
    if output.get('is_complete') and extraction is not None:
        checks['extraction_complete'] = extraction_complete(extraction)
    if expected and expected.get('persona_signal') and extraction:
        checks['correct_persona'] = (
            extraction.get('persona_signal') == expected['persona_signal']
        )
    if expected and expected.get('required_fields') and extraction is not None:
        present = all(
            field in extraction
            and extraction[field] not in (None, '')
            for field in expected['required_fields']
        )
        # Fold an explicit required-fields expectation into completeness.
        checks['extraction_complete'] = bool(
            (checks['extraction_complete'] in (None, True)) and present
        )
    return checks


def _themes_ordered_by_prevalence(themes):
    """True if themes are sorted by ``supporting_count`` descending."""
    counts = [t.get('supporting_count', 0) or 0 for t in themes]
    return all(a >= b for a, b in zip(counts, counts[1:]))


def feedback_checks(output, expected=None):
    """Deterministic per-scenario checks for one feedback output.

    Returns a dict of booleans (``None`` when not applicable). ``expected``
    may carry ``input_terms`` (substrings every theme title must be
    grounded in), ``expects_next_sprint_signal`` (bool), or
    ``no_signal_expected`` (bool).
    """
    output = output or {}
    themes = output.get('themes') or []
    recommendations = output.get('recommendations') or []
    checks = {
        'theme_ranking_correct': _themes_ordered_by_prevalence(themes) if themes else None,
        'no_hallucinated_themes': None,
        'recommendations_actionable': None,
        'next_sprint_signal_correct': None,
    }

    if recommendations:
        # Actionable = both a recommendation and a rationale are present.
        checks['recommendations_actionable'] = all(
            (r.get('recommendation') or '').strip()
            and (r.get('rationale') or '').strip()
            for r in recommendations
        )

    if expected and 'input_terms' in expected and themes:
        terms = [t.lower() for t in expected['input_terms']]
        checks['no_hallucinated_themes'] = all(
            any(term in (theme.get('title', '') + ' ' + theme.get('summary', '')).lower()
                for term in terms)
            for theme in themes
        )

    if expected and expected.get('no_signal_expected'):
        checks['next_sprint_signal_correct'] = not (output.get('next_sprint_signal') or '').strip()
    elif expected and expected.get('expects_next_sprint_signal'):
        checks['next_sprint_signal_correct'] = bool(
            (output.get('next_sprint_signal') or '').strip()
        )
    return checks


CHECK_FUNCS = {
    'onboarding': onboarding_checks,
    'feedback': feedback_checks,
}


def deterministic_checks(callable_name, output, expected=None):
    """Dispatch to the per-assistant deterministic checks."""
    func = CHECK_FUNCS.get(callable_name)
    if func is None:
        return {}
    return func(output, expected)


# --- Report aggregation ---


def _rate(items):
    """Return ``(passed, total, fraction)`` over a list of bool-or-None.

    ``None`` entries are skipped (the check did not apply to that scenario).
    """
    applicable = [v for v in items if v is not None]
    total = len(applicable)
    passed = sum(1 for v in applicable if v)
    return passed, total, _pct(passed, total)


def aggregate_report(*, callable_name, scenarios, run_metadata):
    """Roll judged + checked per-scenario results into the report dict.

    ``scenarios`` is a list of dicts, one per fixture, each carrying:
    ``id``, ``category``, ``judge_label`` (``pass``/``fail``/``None``),
    ``checks`` (the deterministic-check dict), ``callable_latency_seconds``,
    ``judge_latency_seconds``, ``callable_token_usage``,
    ``judge_token_usage``, and ``status`` (``ok``/``error``).

    Produces ``% good`` overall + per category, the deterministic
    per-feature rates, callable-vs-judge cost + latency tracked separately,
    and the run metadata for experiment comparison.
    """
    judged = [s for s in scenarios if s.get('judge_label') in (PASS, FAIL)]
    good = sum(1 for s in judged if s['judge_label'] == PASS)
    pct_good_overall = _pct(good, len(judged))

    # Per-category % good.
    by_category = {}
    for scenario in judged:
        by_category.setdefault(scenario.get('category', 'uncategorized'), []).append(scenario)
    pct_good_by_category = {}
    for category, rows in sorted(by_category.items()):
        cat_good = sum(1 for s in rows if s['judge_label'] == PASS)
        pct_good_by_category[category] = {
            'percent_good': _pct(cat_good, len(rows)),
            'good': cat_good,
            'total': len(rows),
        }

    # Deterministic per-feature rates, aggregated across scenarios.
    check_keys = set()
    for scenario in scenarios:
        check_keys.update((scenario.get('checks') or {}).keys())
    deterministic = {}
    for key in sorted(check_keys):
        passed, total, fraction = _rate(
            [(s.get('checks') or {}).get(key) for s in scenarios]
        )
        deterministic[key] = {
            'percent': fraction, 'passed': passed, 'total': total,
        }

    return {
        'callable': callable_name,
        'run_metadata': run_metadata,
        'scenario_count': len(scenarios),
        'judged_count': len(judged),
        'percent_good_overall': pct_good_overall,
        'percent_good_by_category': pct_good_by_category,
        'deterministic_metrics': deterministic,
        'cost': _cost_summary(scenarios),
        'latency': _latency_summary(scenarios),
        'scenarios': scenarios,
    }


def _sum_usage(scenarios, key):
    """Sum a token-usage field across scenarios, defensively.

    Usage is recorded only when the #799 ``LLMResult`` exposes it; today it
    is ``None`` everywhere, so this returns ``None`` (surfaced as "usage
    unavailable") rather than a misleading zero when no scenario reported
    usage. Mirrors :class:`FileTraceSink`'s defensive behavior.
    """
    available = [s.get(key) for s in scenarios if s.get(key) is not None]
    if not available:
        return None
    total = {}
    for usage in available:
        if not isinstance(usage, dict):
            continue
        for token_key, value in usage.items():
            if isinstance(value, (int, float)):
                total[token_key] = total.get(token_key, 0) + value
    return total or None


def _cost_summary(scenarios):
    callable_usage = _sum_usage(scenarios, 'callable_token_usage')
    judge_usage = _sum_usage(scenarios, 'judge_token_usage')
    return {
        'callable_token_usage': callable_usage,
        'judge_token_usage': judge_usage,
        'note': (
            'usage unavailable: the LLMResult exposes no token usage today; '
            'totals are null until it does.'
        ),
    }


def _avg(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    return (sum(nums) / len(nums)) if nums else None


def _latency_summary(scenarios):
    return {
        'callable_avg_seconds': _avg(
            [s.get('callable_latency_seconds') for s in scenarios]
        ),
        'judge_avg_seconds': _avg(
            [s.get('judge_latency_seconds') for s in scenarios]
        ),
        'callable_total_seconds': sum(
            v for v in (s.get('callable_latency_seconds') for s in scenarios)
            if isinstance(v, (int, float))
        ),
        'judge_total_seconds': sum(
            v for v in (s.get('judge_latency_seconds') for s in scenarios)
            if isinstance(v, (int, float))
        ),
    }


__all__ = [
    'PASS', 'FAIL',
    'confusion_matrix', 'alignment_metrics', 'disagreement_rows',
    'align_by_split', 'persona_name_leaked', 'extraction_complete',
    'onboarding_checks', 'feedback_checks', 'deterministic_checks',
    'aggregate_report',
]
