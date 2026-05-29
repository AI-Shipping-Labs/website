"""LLM backend abstraction seam (issue #799).

Defines the provider-neutral result/error types, the backend protocol,
and the concrete Anthropic-compatible backend. The service wrapper
(:mod:`integrations.services.llm.service`) selects a backend by name from
this module's registry at call time.

Adding an ``openai`` / ``bedrock`` backend later is a matter of writing a
new backend class and registering it in :data:`_BACKENDS` — callers and
the wrapper do not change.

The reference implementation this mirrors lives in the AI Engineering
Field Guide (``job-market/_internal/extract_llm.py``): the official
``anthropic`` SDK pointed at a configurable ``base_url`` with
``max_retries``, plus exponential-backoff-with-jitter on transient errors
and structured output via SDK tools + ``tool_choice``.
"""

import logging
import random
import time

from integrations.config import get_config

logger = logging.getLogger(__name__)

# Default ceiling on a single completion's output tokens. Callers may
# override per call via ``complete(..., max_tokens=...)``.
DEFAULT_MAX_TOKENS = 4096

# Bounded backoff knobs for the wrapper-level retry (on top of the SDK's
# own ``max_retries``). Small and deterministic so unit tests can drive
# the loop with a patched sleep without waiting.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0


class LLMError(Exception):
    """Raised on any LLM service failure.

    Failure modes: not configured (empty key), unsupported provider,
    transport/SDK error after retries are exhausted, a non-transient SDK
    error (converted immediately), or an empty/blocked response. The
    exception string never includes the API key value — see
    :func:`_safe_error_message`.
    """


class LLMResult:
    """Provider-neutral result of a single completion.

    Exposes the assistant text (concatenated text blocks) and, when the
    model used a tool, the structured tool input dict — so callers can
    read ``result.text`` for plain completions or
    ``MyModel.model_validate(result.tool_input)`` for structured output
    without re-parsing the vendor SDK's response shape.
    """

    def __init__(self, *, text='', tool_input=None, tool_name=None):
        self.text = text
        self.tool_input = tool_input
        self.tool_name = tool_name

    def __repr__(self):
        return (
            f'LLMResult(text={self.text!r}, '
            f'tool_name={self.tool_name!r}, '
            f'has_tool_input={self.tool_input is not None})'
        )


def _retry_delay(attempt, *, base, cap):
    """Exponential backoff with small jitter (mirrors the reference)."""
    return min(cap, base * (2 ** attempt)) + random.uniform(0, 1)


def _safe_error_message(message, api_key):
    """Return ``message`` with the API key value scrubbed if present.

    Defense in depth: the wrapper never interpolates the key into its own
    messages, but an SDK error string could theoretically echo it back.
    """
    text = str(message)
    if api_key and api_key in text:
        text = text.replace(api_key, '***')
    return text


class AnthropicBackend:
    """Anthropic-compatible backend using the official ``anthropic`` SDK.

    The same client talks to Anthropic or any Anthropic-compatible
    gateway (e.g. Z.ai) by pointing ``base_url`` at the gateway. The
    client is constructed per call from :func:`get_config` — never cached
    at import — so Studio overrides and worker processes read fresh
    values.
    """

    name = 'anthropic'

    def complete(
        self,
        messages,
        *,
        model=None,
        system=None,
        max_tokens=DEFAULT_MAX_TOKENS,
        temperature=None,
        tools=None,
        tool_choice=None,
    ):
        # Imported lazily so the module imports even in environments where
        # the optional ``anthropic`` SDK is absent; only the Anthropic
        # backend needs it.
        from anthropic import (  # noqa: PLC0415
            Anthropic,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        api_key = (get_config('LLM_API_KEY', '') or '').strip()
        if not api_key:
            raise LLMError('LLM is not configured (LLM_API_KEY is empty)')

        base_url = (get_config('LLM_BASE_URL', '') or '').strip() or None
        resolved_model = model or get_config('LLM_MODEL', 'claude-sonnet-4-5')
        max_retries = _resolve_max_retries()

        client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
        )

        request_kwargs = {
            'model': resolved_model,
            'max_tokens': max_tokens,
            'messages': messages,
        }
        if system is not None:
            request_kwargs['system'] = system
        if temperature is not None:
            request_kwargs['temperature'] = temperature
        if tools is not None:
            request_kwargs['tools'] = tools
        if tool_choice is not None:
            request_kwargs['tool_choice'] = tool_choice

        transient_errors = (
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
        )

        # ``max_retries`` is the number of RETRIES, so total attempts is
        # one initial call plus that many retries.
        total_attempts = max_retries + 1
        last_exc = None
        for attempt in range(total_attempts):
            try:
                response = client.messages.create(**request_kwargs)
                return _parse_response(response, api_key)
            except transient_errors as exc:
                last_exc = exc
                if attempt < total_attempts - 1:
                    delay = _retry_delay(
                        attempt,
                        base=_BACKOFF_BASE_SECONDS,
                        cap=_BACKOFF_CAP_SECONDS,
                    )
                    logger.warning(
                        'LLM transient error (%s), retrying',
                        type(exc).__name__,
                    )
                    time.sleep(delay)
                    continue
                break
            except LLMError:
                # Raised by _parse_response for empty/blocked responses —
                # do not retry, do not wrap again.
                raise
            except Exception as exc:
                # Non-transient SDK/transport error (auth, bad request,
                # etc.) — never retried; surface immediately.
                raise LLMError(
                    'LLM request failed: '
                    + _safe_error_message(
                        f'{type(exc).__name__}: {exc}', api_key,
                    )
                ) from None

        raise LLMError(
            'LLM request failed after retries: '
            + _safe_error_message(
                type(last_exc).__name__ if last_exc else 'unknown', api_key,
            )
        )


def _parse_response(response, api_key):
    """Turn an Anthropic Messages response into an :class:`LLMResult`.

    Concatenates text blocks and surfaces the first tool-use block's
    input. Raises :class:`LLMError` when the response carries no usable
    content (empty or blocked).
    """
    content = getattr(response, 'content', None) or []
    text_parts = []
    tool_input = None
    tool_name = None
    for block in content:
        block_type = getattr(block, 'type', None)
        if block_type == 'text':
            text_parts.append(getattr(block, 'text', '') or '')
        elif block_type == 'tool_use':
            if tool_input is None:
                tool_input = getattr(block, 'input', None)
                tool_name = getattr(block, 'name', None)

    text = ''.join(text_parts).strip()
    if not text and tool_input is None:
        raise LLMError('LLM returned an empty or blocked response')

    return LLMResult(text=text, tool_input=tool_input, tool_name=tool_name)


def _resolve_max_retries():
    """Resolve the SDK ``max_retries`` value (default 6, env-tunable)."""
    raw = get_config('LLM_MAX_RETRIES', 6)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 6
    return max(0, value)


# Backend registry: provider name -> backend factory. Only ``anthropic``
# is implemented; ``openai`` / ``bedrock`` are reserved seams. To add a
# new backend, register its factory here — callers and the wrapper do not
# change.
_BACKENDS = {
    'anthropic': AnthropicBackend,
}

# Providers that are recognised but not yet implemented. Kept distinct so
# the error message can say "reserved" rather than "unknown".
_RESERVED_PROVIDERS = {'openai', 'bedrock'}


def is_provider_implemented(provider):
    """Return True iff a backend exists for ``provider``."""
    return provider in _BACKENDS


def get_backend(provider):
    """Return a backend instance for ``provider`` or raise :class:`LLMError`.

    An unimplemented provider (reserved or unknown) raises with a clear
    message naming the provider, before any network call is made.
    """
    factory = _BACKENDS.get(provider)
    if factory is None:
        if provider in _RESERVED_PROVIDERS:
            raise LLMError(
                f'LLM provider "{provider}" is not supported yet '
                f'(reserved for a future backend)'
            )
        raise LLMError(f'LLM provider "{provider}" is not supported yet')
    return factory()
