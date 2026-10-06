"""Shared file-backed trace sink for the standalone AI-eval harness (#809).

:class:`FileTraceSink` is a concrete ``TraceSink`` that works against BOTH
callables' trace contracts -- ``synthesize_feedback`` (#805) and
``run_onboarding_turn`` (#804) expose the identical hook shape
(``on_request``, ``on_result``, ``on_parsed``, ``on_error``), so a single
sink with no callable-specific branching captures either run.

The sink accumulates the hook calls and serializes one ``trace.json`` per
run via :meth:`to_dict` / :meth:`write`. The #799 ``LLMResult`` carries
the provider's per-call token counters (``input_tokens``,
``output_tokens``, ``cache_read_tokens``, ``cache_write_tokens``) when
the provider reports usage; :meth:`_extract_token_usage` records them as
a per-key dict and keeps ``token_usage`` ``null`` when the result has no
counters at all (mock mode, counter-less gateway) rather than crashing
or inventing zeros.

The API key never appears in any captured field: the sink only stores the
rendered request (system/messages/tool), the parsed result, latency, and a
type + safe message for errors -- none of which carry credentials.
"""

import json

# The #799 ``LLMResult`` per-call token counter fields. Raw provider
# values: already measured per completion, never summed across calls, and
# NOT inclusive of each other (Anthropic ``input_tokens`` excludes cache
# read/write tokens), so they are recorded per key with no computed
# ``total_tokens`` -- a naive sum would double-count cache tokens.
_TOKEN_FIELDS = (
    'input_tokens',
    'output_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
)


class FileTraceSink:
    """Concrete ``TraceSink`` capturing one run to ``trace.json``.

    Compatible with either callable's ``TraceSink`` contract. Construct it
    with the static run metadata (callable name, provider, model, start
    timestamp); the hooks fill in the request, result, parsed output, and
    any error as the run proceeds.
    """

    def __init__(self, *, callable_name, provider, model, timestamp_utc):
        self.callable_name = callable_name
        self.provider = provider
        self.model = model
        self.timestamp_utc = timestamp_utc
        self.system_prompt = None
        self.messages = None
        self.tool = None
        self.raw_result = None
        self.token_usage = None
        self.latency_seconds = None
        self.parsed_output = None
        self.error = None

    # --- TraceSink hooks (shared shape across both callables) ---

    def on_request(self, *, system, messages, tool):
        self.system_prompt = system
        self.messages = messages
        self.tool = tool

    def on_result(self, *, result, latency_seconds):
        self.raw_result = {
            'text': getattr(result, 'text', None),
            'tool_name': getattr(result, 'tool_name', None),
            'tool_input': getattr(result, 'tool_input', None),
        }
        self.token_usage = self._extract_token_usage(result)
        self.latency_seconds = latency_seconds

    def on_parsed(self, *, parsed):
        # Both callables pass a Pydantic model here.
        self.parsed_output = parsed.model_dump(mode='json')

    def on_error(self, *, error):
        self.error = {
            'type': type(error).__name__,
            'message': str(error),
        }

    # --- Serialization ---

    @staticmethod
    def _extract_token_usage(result):
        """Collect the result's per-call token counters, ``None`` if none.

        Reads the #799 ``LLMResult`` counter fields directly (defensively,
        so a counter-less stub without the attributes stays ``None``).
        Returns a dict holding ONLY the keys whose value is not ``None``;
        when no counter is present (the mock backend, a counter-less
        Anthropic-compatible gateway) the usage stays ``None`` --
        deliberate absence, not a crash, not a misleading zero. This is
        the ONE shared extraction: the judge path
        (:func:`eval_runner._extract_usage`) delegates here too.
        """
        usage = {
            field: value
            for field in _TOKEN_FIELDS
            if (value := getattr(result, field, None)) is not None
        }
        return usage or None

    def to_dict(self):
        """Return the full captured trace as a JSON-serializable dict."""
        return {
            'callable': self.callable_name,
            'provider': self.provider,
            'model': self.model,
            'timestamp_utc': self.timestamp_utc,
            'system_prompt': self.system_prompt,
            'messages': self.messages,
            'tool': self.tool,
            'raw_result': self.raw_result,
            'token_usage': self.token_usage,
            'latency_seconds': self.latency_seconds,
            'parsed_output': self.parsed_output,
            'error': self.error,
        }

    def write(self, path):
        """Serialize the trace to ``path`` (a ``pathlib.Path``)."""
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding='utf-8',
        )
