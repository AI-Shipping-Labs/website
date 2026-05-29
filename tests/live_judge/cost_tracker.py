"""Cost + usage tracking for the live LLM-judge set (issue #811).

Mirrors the documentation-agent reference ``cost_tracker.py``: every LLM
call (the assistant under test AND the judge) appends one JSONL record to
a temp file; at session end the per-model and total USD cost plus
lightweight run metrics (number of calls, % criteria passed) are printed.

Defensiveness: the #799 ``LLMResult`` does NOT expose token counts today
(the #809 trace sink documents this and reads usage defensively). This
tracker mirrors that -- when usage is unavailable it records a
zero/unknown-token entry and still emits the summary (total $0.00 when
tokens are unknown) rather than crashing. If/when ``LLMResult`` grows a
``usage`` attribute exposing ``input_tokens`` / ``output_tokens``, the
tracker reads it without further changes, and ``MODEL_PRICES`` turns the
counts into a non-zero cost.

This module imports neither Django nor any vendor SDK, and it never
imports or initializes Logfire (#813 owns that gating).
"""

import json
import tempfile
from pathlib import Path

# Per-million-token USD prices keyed by configured model name (lowercased).
# Kept ready for when LLMResult exposes usage; until then every cost is
# $0.00 because token counts come through as zero/unknown.
MODEL_PRICES = {
    'claude-sonnet-4-5': {'input': 3.00, 'output': 15.00},
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00},
    'glm-4.6': {'input': 0.60, 'output': 2.20},
    'glm-5.1': {'input': 0.60, 'output': 2.20},
}

COST_FILE = Path(tempfile.gettempdir()) / 'live_judge_cost_tracker.jsonl'


def cost_usd(model, input_tokens, output_tokens):
    """USD cost for a model's token usage; 0.0 when the model is unpriced."""
    prices = MODEL_PRICES.get((model or '').lower(), {'input': 0.0, 'output': 0.0})
    return (
        (input_tokens / 1_000_000) * prices['input']
        + (output_tokens / 1_000_000) * prices['output']
    )


def reset_cost_file():
    """Drop any cost file left over from a previous run."""
    COST_FILE.unlink(missing_ok=True)


def _read_usage(result):
    """Read ``(input_tokens, output_tokens)`` from an ``LLMResult`` defensively.

    The #799 ``LLMResult`` does not carry usage today, so this returns
    ``(0, 0)`` for it. If a future ``LLMResult`` grows a ``usage`` object
    (with ``input_tokens`` / ``output_tokens``) or those attributes
    directly, they are read here without crashing on absence.
    """
    if result is None:
        return 0, 0
    usage = getattr(result, 'usage', None)
    # ``usage`` may be a token-carrying object/attribute or a callable that
    # returns one (pydantic-ai style). Prefer reading token attributes
    # directly; only invoke it when it carries none of its own.
    if usage is not None and not _has_token_attrs(usage) and callable(usage):
        try:
            usage = usage()
        except Exception:
            usage = None
    source = usage if usage is not None else result
    return (
        _as_token_count(getattr(source, 'input_tokens', None)),
        _as_token_count(getattr(source, 'output_tokens', None)),
    )


def _has_token_attrs(obj):
    """True if ``obj`` exposes numeric ``input_tokens`` / ``output_tokens``."""
    for attr in ('input_tokens', 'output_tokens'):
        value = getattr(obj, attr, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False


def _as_token_count(value):
    """Coerce a token value to a non-negative int; 0 when unknown/non-numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


def capture_usage(model, result=None, *, criteria_total=0, criteria_passed=0):
    """Append one JSONL record for an LLM call.

    Args:
        model: the model name the call used (assistant or judge).
        result: the #799 ``LLMResult`` (or None); usage is read
            defensively and recorded as zero/unknown when unavailable.
        criteria_total: number of judge criteria scored on this call
            (0 for an assistant call that is not a judge run).
        criteria_passed: number of those criteria that passed.
    """
    input_tokens, output_tokens = _read_usage(result)
    entry = {
        'model': model,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'criteria_total': int(criteria_total),
        'criteria_passed': int(criteria_passed),
    }
    with open(COST_FILE, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry) + '\n')


def display_total_usage():
    """Print the per-model and total USD cost plus lightweight run metrics.

    Always prints a summary (even with zero calls or unknown tokens) so a
    skipped/no-key run still ends cleanly rather than crashing.
    """
    print()
    print('=== Live LLM-judge usage summary ===')

    if not COST_FILE.exists():
        print('LLM calls: 0')
        print('Total cost: $0.000000')
        return

    totals = {}
    call_count = 0
    criteria_total = 0
    criteria_passed = 0
    for line in COST_FILE.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        call_count += 1
        criteria_total += entry.get('criteria_total', 0)
        criteria_passed += entry.get('criteria_passed', 0)
        model = entry['model']
        bucket = totals.setdefault(model, {'input_tokens': 0, 'output_tokens': 0})
        bucket['input_tokens'] += entry['input_tokens']
        bucket['output_tokens'] += entry['output_tokens']

    total_cost = 0.0
    for model, tokens in totals.items():
        cost = cost_usd(model, tokens['input_tokens'], tokens['output_tokens'])
        print(
            f'{model}: ${cost:.6f} '
            f'(in={tokens["input_tokens"]}, out={tokens["output_tokens"]})'
        )
        total_cost += cost

    pass_pct = (
        (criteria_passed / criteria_total * 100.0) if criteria_total else 0.0
    )
    print(f'LLM calls: {call_count}')
    print(f'Criteria passed: {criteria_passed}/{criteria_total} ({pass_pct:.1f}%)')
    print(f'Total cost: ${total_cost:.6f}')


__all__ = [
    'MODEL_PRICES',
    'COST_FILE',
    'cost_usd',
    'reset_cost_file',
    'capture_usage',
    'display_total_usage',
]
