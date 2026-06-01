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


def stream(
    messages,
    *,
    model=None,
    system=None,
    max_tokens=DEFAULT_MAX_TOKENS,
    temperature=None,
    tools=None,
):
    """Stream a completion, yielding provider-neutral ``StreamEvent``s.

    The streaming counterpart to :func:`complete`. Used by the onboarding
    chat (#806) to deliver the assistant reply token-by-token over SSE.

    Yields a sequence of ``text_delta`` events (each carrying the next
    chunk of assistant text) followed by a single terminal ``done`` event
    whose ``result`` is the fully assembled :class:`LLMResult` — the SAME
    object :func:`complete` would return for the same input.

    Args:
        messages: Anthropic-style message list.
        model: Explicit model name; falls back to ``LLM_MODEL`` config.
        system: Optional system prompt.
        max_tokens: Output token ceiling (same default as ``complete``).
        temperature: Optional sampling temperature.
        tools: Optional tool specs for structured output. When supplied,
            the terminal ``done`` event's :class:`LLMResult` carries the
            model's ``tool_input`` if it called a tool, so a single
            streamed generation yields both the conversational deltas and
            (when the model decides to) the structured tool call. This lets
            the onboarding chat (#821) avoid a redundant second
            ``complete`` call to obtain the authoritative turn result.

    Returns:
        Iterator[StreamEvent].

    Raises:
        LLMError: when the provider is unimplemented (raised before any
            network call), the stream fails to open, or the stream fails
            mid-response. Mid-stream errors are surfaced (not silently
            retried) so the transport layer can fall back to ``complete``.
            The API key never appears in the message.
    """
    provider = _resolve_provider()
    # Select the backend first so an unimplemented provider raises before
    # any client construction or network call (mirrors ``complete``).
    backend = get_backend(provider)
    return backend.stream(
        messages,
        model=model,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        tools=tools,
    )


__all__ = ['LLMError', 'complete', 'is_enabled', 'stream']
