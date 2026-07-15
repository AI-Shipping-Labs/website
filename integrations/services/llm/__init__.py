"""Provider-neutral LLM service (issue #799).

Callers depend on this package's public interface — ``is_enabled()``,
``complete(...)``, ``LLMResult``, and ``LLMError`` — never on a specific
vendor SDK. The service selects a backend from ``LLM_PROVIDER`` and
resolves credentials/model/base-URL from :func:`integrations.config.get_config`
at call time, so a Studio override takes effect without a redeploy and
worker processes read fresh values.

Only the Anthropic-compatible backend is implemented today (the official
``anthropic`` SDK pointed at the configurable ``LLM_BASE_URL``, which also
covers Anthropic-compatible gateways such as Z.ai). ``openai`` and
``bedrock`` are reserved seams that raise :class:`LLMError` until built.

The onboarding assistant (#804) and feedback synthesis (#805) build on
top of this contract — keep the public surface stable.

Structured output
=================

Callers build a tool spec from a Pydantic model and pass it through::

    tool = {
        'name': 'verdict',
        'description': 'Structured verdict',
        'input_schema': MyModel.model_json_schema(),
    }
    result = complete(
        messages,
        tools=[tool],
        tool_choice={'type': 'tool', 'name': tool['name']},
    )
    MyModel.model_validate(result.tool_input)

The wrapper itself does not import Pydantic — callers own their schemas.

Security
========

The API key never appears in any exception message or log line emitted
by this service (asserted in tests, mirroring banner_generator).
"""

from .backends import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    CancellationToken,
    LLMError,
    LLMResult,
    LLMTimeoutError,
    StreamEvent,
)
from .service import complete, is_enabled, stream

__all__ = [
    'STREAM_DONE',
    'STREAM_TEXT_DELTA',
    'CancellationToken',
    'LLMError',
    'LLMTimeoutError',
    'LLMResult',
    'StreamEvent',
    'complete',
    'is_enabled',
    'stream',
]
