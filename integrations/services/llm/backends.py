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


# Event kinds yielded by ``stream(...)``. Kept as plain string constants so
# the surface stays provider-neutral and JSON-serialisable at the transport
# layer (no vendor enum leaks to callers).
STREAM_TEXT_DELTA = 'text_delta'
STREAM_DONE = 'done'


class StreamEvent:
    """One provider-neutral event yielded by :func:`stream`.

    Two kinds, distinguished by ``kind``:

    - ``text_delta``: ``text`` carries the next incremental chunk of the
      assistant's reply. ``result`` is ``None``.
    - ``done``: the terminal event. ``result`` carries the fully assembled
      :class:`LLMResult` — the SAME object ``complete()`` would have
      returned for the same input (``.text`` is the concatenation of every
      delta; ``.tool_input`` is set when a tool was used). ``text`` is
      empty on the terminal event.

    A mid-stream transport/SDK failure is NOT signalled as an event: it is
    re-raised from the generator so the transport layer can trigger the
    graceful fallback (see :func:`AnthropicBackend.stream`).
    """

    def __init__(self, *, kind, text='', result=None):
        self.kind = kind
        self.text = text
        self.result = result

    @property
    def is_done(self):
        return self.kind == STREAM_DONE

    def __repr__(self):
        if self.is_done:
            return f'StreamEvent(done, result={self.result!r})'
        return f'StreamEvent(text_delta, text={self.text!r})'


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

    def stream(
        self,
        messages,
        *,
        model=None,
        system=None,
        max_tokens=DEFAULT_MAX_TOKENS,
        temperature=None,
    ):
        """Stream a completion, yielding :class:`StreamEvent` objects.

        Yields ``text_delta`` events as the model produces tokens, then a
        single terminal ``done`` event carrying a fully assembled
        :class:`LLMResult` (the SAME object ``complete()`` would return for
        the same input).

        Error contract (documented for the transport fallback):

        - A transport/SDK error while OPENING the stream (before any delta
          is yielded) raises :class:`LLMError` — same as ``complete()``.
          The SDK's own ``max_retries`` covers transient connect/open
          retries; opening the stream is the only retryable phase.
        - A mid-stream failure (after at least one delta) is re-raised
          from the generator (wrapped in :class:`LLMError`). It is NOT
          retried — replaying tokens would corrupt the stream — so the
          caller (transport layer) catches it and falls back to the
          non-streaming path for the same member message.

        The API key never appears in any raised message (scrubbed via
        :func:`_safe_error_message`, mirroring ``complete()``).

        Tools / structured output are intentionally NOT supported here
        (the final extraction turn keeps using ``complete()``); this
        surface targets the plain-text conversational turns.
        """
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

        open_errors = (
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
        )

        # Opening the stream is the retryable phase. The SDK's
        # ``client.messages.stream(...)`` returns a context manager; we
        # enter it inside the try so an open failure raises LLMError
        # before any delta has been emitted.
        try:
            stream_cm = client.messages.stream(**request_kwargs)
            manager = stream_cm.__enter__()
        except open_errors as exc:
            raise LLMError(
                'LLM stream failed to open: '
                + _safe_error_message(type(exc).__name__, api_key)
            ) from None
        except Exception as exc:
            raise LLMError(
                'LLM stream failed to open: '
                + _safe_error_message(
                    f'{type(exc).__name__}: {exc}', api_key,
                )
            ) from None

        return self._iter_stream(stream_cm, manager, api_key)

    def _iter_stream(self, stream_cm, manager, api_key):
        """Generator that yields deltas then the assembled terminal event.

        Separated from :meth:`stream` so that an open failure raises
        eagerly (before the caller starts iterating) while delta iteration
        and the terminal assembly happen lazily.
        """
        text_parts = []
        try:
            for chunk in manager.text_stream:
                if not chunk:
                    continue
                text_parts.append(chunk)
                yield StreamEvent(kind=STREAM_TEXT_DELTA, text=chunk)
            final_message = manager.get_final_message()
        except Exception as exc:
            # Mid-stream failure: never retried (would replay tokens).
            # Surface to the caller so the transport falls back.
            raise LLMError(
                'LLM stream failed mid-response: '
                + _safe_error_message(
                    f'{type(exc).__name__}: {exc}', api_key,
                )
            ) from None
        finally:
            stream_cm.__exit__(None, None, None)

        result = _parse_response(final_message, api_key)
        # Defensively prefer the streamed text when the final message did
        # not echo a text block (some gateways stream text without a final
        # content block); the assembled deltas are authoritative for text.
        streamed = ''.join(text_parts).strip()
        if streamed and not result.text:
            result.text = streamed
        yield StreamEvent(kind=STREAM_DONE, result=result)


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
