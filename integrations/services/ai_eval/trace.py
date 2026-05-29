"""Shared file-backed trace sink for the standalone AI-eval harness (#809).

:class:`FileTraceSink` is a concrete ``TraceSink`` that works against BOTH
callables' trace contracts -- ``synthesize_feedback`` (#805) and
``run_onboarding_turn`` (#804) expose the identical hook shape
(``on_request``, ``on_result``, ``on_parsed``, ``on_error``), so a single
sink with no callable-specific branching captures either run.

The sink accumulates the hook calls and serializes one ``trace.json`` per
run via :meth:`to_dict` / :meth:`write`. The #799 ``LLMResult`` exposes
``.text`` / ``.tool_input`` / ``.tool_name`` only -- token usage and the
raw vendor response are not on it today, so the sink reads usage
defensively (``getattr``) and records ``null`` when absent rather than
crashing.

The API key never appears in any captured field: the sink only stores the
rendered request (system/messages/tool), the parsed result, latency, and a
type + safe message for errors -- none of which carry credentials.
"""

import json


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
        """Read token usage off the result if present, else ``None``.

        The #799 ``LLMResult`` does not carry usage today; this stays
        ``None`` rather than raising so the sink never crashes on its
        absence. If a future ``LLMResult`` grows a ``usage`` attribute it
        is captured automatically.
        """
        usage = getattr(result, 'usage', None)
        if usage is None:
            return None
        # Normalise common shapes to a plain dict where possible.
        if isinstance(usage, dict):
            return usage
        for attr in ('model_dump', 'to_dict', '_asdict'):
            fn = getattr(usage, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    return None
        return None

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
