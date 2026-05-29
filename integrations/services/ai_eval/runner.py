"""Fixture parsing + callable dispatch for the AI-eval harness (#809).

This module is the per-callable adapter layer: it loads a JSON/YAML
fixture, validates it into the chosen callable's plain input
(``SprintFeedbackInput`` for ``feedback``; ``transcript`` /
``member_message`` / ``persona_catalog`` for ``onboarding``), invokes the
callable with a ``trace`` sink, and produces a couple of headline summary
fields for the run table. It imports neither Django models nor the request
layer -- only the two pure callables -- so it stays as wrappable as they
are.

Malformed fixtures raise :class:`FixtureError` with a clear, file/field
message; the command turns that into a non-zero exit with no stack trace.
"""

import json
from pathlib import Path

from integrations.services import feedback_synthesis
from questionnaires import onboarding_ai


class FixtureError(Exception):
    """A fixture could not be parsed/validated into the callable's input."""


# Callable identifiers accepted as the positional argument.
FEEDBACK = 'feedback'
ONBOARDING = 'onboarding'
CALLABLES = (FEEDBACK, ONBOARDING)


def load_fixture(path):
    """Parse a JSON or YAML fixture file into a plain dict.

    The format is chosen by suffix (``.json`` vs ``.yaml`` / ``.yml``);
    anything else is parsed as YAML (a JSON superset). Raises
    :class:`FixtureError` naming the file on read/parse failure.
    """
    path = Path(path)
    if not path.exists():
        raise FixtureError(f'Fixture not found: {path}')
    raw = path.read_text(encoding='utf-8')
    suffix = path.suffix.lower()
    try:
        if suffix == '.json':
            data = json.loads(raw)
        else:
            import yaml

            data = yaml.safe_load(raw)
    except Exception as exc:
        raise FixtureError(f'Could not parse fixture {path}: {exc}') from None
    if not isinstance(data, dict):
        raise FixtureError(
            f'Fixture {path} must be a mapping at the top level, '
            f'got {type(data).__name__}.'
        )
    return data


def build_feedback_input(data, *, source):
    """Validate a fixture dict into a ``SprintFeedbackInput``.

    Raises :class:`FixtureError` naming the field on validation failure.
    """
    try:
        return feedback_synthesis.SprintFeedbackInput.model_validate(data)
    except Exception as exc:
        raise FixtureError(
            f'Invalid feedback fixture {source}: {exc}'
        ) from None


def build_onboarding_input(data, *, source):
    """Validate a fixture dict into onboarding call kwargs.

    Returns ``(transcript, member_message, persona_catalog)``. Raises
    :class:`FixtureError` naming the field on validation failure.
    """
    if 'transcript' not in data and 'member_message' not in data:
        raise FixtureError(
            f'Invalid onboarding fixture {source}: must contain at least '
            f'"transcript" or "member_message".'
        )
    transcript = data.get('transcript') or []
    member_message = data.get('member_message')
    try:
        persona_catalog = [
            onboarding_ai.PersonaInfo.model_validate(p)
            for p in (data.get('persona_catalog') or [])
        ]
    except Exception as exc:
        raise FixtureError(
            f'Invalid onboarding fixture {source} ("persona_catalog"): {exc}'
        ) from None
    if not isinstance(transcript, list):
        raise FixtureError(
            f'Invalid onboarding fixture {source}: "transcript" must be a list.'
        )
    return transcript, member_message, persona_catalog


def run_feedback(data, *, trace, model, source):
    """Run ``synthesize_feedback`` and return ``(result, headline_fields)``."""
    feedback = build_feedback_input(data, source=source)
    result = feedback_synthesis.synthesize_feedback(feedback, trace=trace)
    headline = {
        'response_count': result.response_count,
        'themes': len(result.themes),
        'recommendations': len(result.recommendations),
    }
    return result, headline


def run_onboarding(data, *, trace, model, source):
    """Run ``run_onboarding_turn`` and return ``(result, headline_fields)``."""
    transcript, member_message, persona_catalog = build_onboarding_input(
        data, source=source
    )
    result = onboarding_ai.run_onboarding_turn(
        transcript,
        member_message=member_message,
        persona_catalog=persona_catalog,
        trace=trace,
    )
    headline = {
        'is_complete': result.is_complete,
        'assistant_message': (result.assistant_message or '')[:80],
    }
    return result, headline


def run_callable(callable_name, data, *, trace, model, source):
    """Dispatch to the chosen callable. Returns ``(result, headline)``.

    ``result`` is the callable's Pydantic return value (it has
    ``model_dump``); ``headline`` is a small dict for the run summary.
    """
    if callable_name == FEEDBACK:
        return run_feedback(data, trace=trace, model=model, source=source)
    if callable_name == ONBOARDING:
        return run_onboarding(data, trace=trace, model=model, source=source)
    raise FixtureError(f'Unknown callable: {callable_name!r}')
