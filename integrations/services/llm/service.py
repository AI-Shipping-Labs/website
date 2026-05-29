"""Provider-neutral LLM service wrapper (issue #799).

Thin layer over the backend registry in
:mod:`integrations.services.llm.backends`. Resolves the provider and
credentials from :func:`integrations.config.get_config` at call time and
dispatches to the selected backend. Callers import only from the package
root (``integrations.services.llm``).
"""

from integrations.config import get_config

from .backends import (
    DEFAULT_MAX_TOKENS,
    LLMError,
    get_backend,
    is_provider_implemented,
)


def _resolve_provider():
    """Return the configured provider name (defaults to ``anthropic``)."""
    return (get_config('LLM_PROVIDER', 'anthropic') or 'anthropic').strip()


def is_enabled():
    """Return True iff the LLM service can run.

    True only when ``LLM_API_KEY`` resolves to a non-empty value AND the
    selected ``LLM_PROVIDER`` has an implemented backend. Base URL and
    model always have defaults, so the key + a supported provider are the
    gate (mirrors ``banner_generator.is_enabled()``).
    """
    api_key = (get_config('LLM_API_KEY', '') or '').strip()
    if not api_key:
        return False
    return is_provider_implemented(_resolve_provider())


def complete(
    messages,
    *,
    model=None,
    system=None,
    max_tokens=DEFAULT_MAX_TOKENS,
    temperature=None,
    tools=None,
    tool_choice=None,
):
    """Run a single completion against the configured provider.

    Args:
        messages: Anthropic-style message list
            (``[{'role': 'user', 'content': ...}]``).
        model: Explicit model name; falls back to ``LLM_MODEL`` config.
        system: Optional system prompt.
        max_tokens: Output token ceiling.
        temperature: Optional sampling temperature.
        tools: Optional tool specs for structured output (each built from
            a Pydantic ``model_json_schema()``).
        tool_choice: Optional tool-choice directive
            (e.g. ``{'type': 'tool', 'name': ...}``).

    Returns:
        LLMResult: exposes ``.text`` and, when a tool was used,
        ``.tool_input``.

    Raises:
        LLMError: on not-configured, unsupported provider, transport
            error after retries, non-transient SDK error, or an empty /
            blocked response. The API key never appears in the message.
    """
    provider = _resolve_provider()
    # Select the backend first so an unimplemented provider raises before
    # any client construction or network call.
    backend = get_backend(provider)
    return backend.complete(
        messages,
        model=model,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        tools=tools,
        tool_choice=tool_choice,
    )


__all__ = ['LLMError', 'complete', 'is_enabled']
