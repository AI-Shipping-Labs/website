"""Tests for the LLM streaming surface (issue #806).

The Anthropic SDK streaming API is fully mocked in every test -- CI never
opens a live stream. All key values are obvious fakes; no real key
appears anywhere.

Covers: streamed text-delta assembly, the terminal ``done`` event
carrying a fully-assembled ``LLMResult`` equivalent to ``complete()``,
per-call client construction from config, backend selection for an
unimplemented provider, the open-failure vs mid-stream-failure error
contract, and the token-safety invariant.
"""

from unittest.mock import MagicMock, patch

import httpx
from anthropic import APIConnectionError, RateLimitError
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.services import llm
from integrations.services.llm import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    CancellationToken,
    LLMError,
    LLMResult,
)

FAKE_KEY = 'sk-test-fake-stream-secret123'
ANTHROPIC_PATCH = 'anthropic.Anthropic'

LLM_ON = override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)


def _final_text_message(text):
    """Mock final Anthropic message with one text block."""
    block = MagicMock()
    block.type = 'text'
    block.text = text
    message = MagicMock()
    message.content = [block]
    message.usage = None
    return message


def _final_tool_message(tool_name, tool_input):
    """Mock final Anthropic message with a tool_use block."""
    block = MagicMock()
    block.type = 'tool_use'
    block.name = tool_name
    block.input = tool_input
    message = MagicMock()
    message.content = [block]
    message.usage = None
    return message


def _stream_cm(deltas, final_message, *, raise_after=None, open_error=None):
    """Build a mock for ``client.messages.stream(...)`` return value.

    The returned object is a context manager whose ``__enter__`` yields a
    manager exposing ``text_stream`` (an iterator of ``deltas``) and
    ``get_final_message()``.

    ``raise_after``: if set, the ``text_stream`` iterator raises after
    yielding that many deltas (a mid-stream failure).
    ``open_error``: if set, entering the context manager raises it (an
    open failure).
    """
    manager = MagicMock()

    def gen():
        for i, d in enumerate(deltas):
            if raise_after is not None and i == raise_after:
                raise APIConnectionError(
                    message='mid-stream drop',
                    request=httpx.Request('POST', 'https://x/'),
                )
            yield d
        if raise_after is not None and raise_after >= len(deltas):
            raise APIConnectionError(
                message='mid-stream drop',
                request=httpx.Request('POST', 'https://x/'),
            )

    manager.text_stream = gen()
    manager.get_final_message.return_value = final_message

    cm = MagicMock()
    if open_error is not None:
        cm.__enter__.side_effect = open_error
    else:
        cm.__enter__.return_value = manager
    cm.__exit__.return_value = False
    return cm


class _Mixin:
    def setUp(self):
        super().setUp()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


@LLM_ON
class StreamAssemblyTest(_Mixin, TestCase):

    def test_cancellation_token_closes_stream_provider_client(self):
        token = CancellationToken()
        with patch(ANTHROPIC_PATCH) as mock_cls:
            client = mock_cls.return_value
            client.messages.stream.return_value = _stream_cm(
                ['done'], _final_text_message('done'),
            )
            list(llm.stream(
                [{'role': 'user', 'content': 'hi'}], cancellation=token,
            ))
            token.cancel()
        client.close.assert_called_once_with()

    def test_yields_deltas_then_terminal_done(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['Hel', 'lo ', 'world'], _final_text_message('Hello world'),
            )
            events = list(llm.stream([{'role': 'user', 'content': 'hi'}]))

        kinds = [e.kind for e in events]
        self.assertEqual(
            kinds, [STREAM_TEXT_DELTA, STREAM_TEXT_DELTA, STREAM_TEXT_DELTA,
                    STREAM_DONE],
        )
        # Assembling the deltas reproduces the final text.
        assembled = ''.join(e.text for e in events if e.kind == STREAM_TEXT_DELTA)
        self.assertEqual(assembled, 'Hello world')
        terminal = events[-1]
        self.assertTrue(terminal.is_done)
        self.assertIsInstance(terminal.result, LLMResult)
        self.assertEqual(terminal.result.text, 'Hello world')
        self.assertIsNone(terminal.result.tool_input)

    def test_terminal_result_equivalent_to_complete(self):
        # Same scripted output served to complete() and stream(): the
        # terminal result text matches complete()'s result text.
        with patch(ANTHROPIC_PATCH) as mock_cls:
            client = mock_cls.return_value
            # complete():
            block = MagicMock()
            block.type = 'text'
            block.text = 'The answer is 42'
            comp_resp = MagicMock()
            comp_resp.content = [block]
            client.messages.create.return_value = comp_resp
            complete_result = llm.complete([{'role': 'user', 'content': 'q'}])

            client.messages.stream.return_value = _stream_cm(
                ['The ', 'answer ', 'is 42'],
                _final_text_message('The answer is 42'),
            )
            events = list(llm.stream([{'role': 'user', 'content': 'q'}]))
        terminal = events[-1]
        self.assertEqual(terminal.result.text, complete_result.text)

    def test_terminal_result_carries_tool_input(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['Recording your answers.'],
                _final_tool_message('record', {'persona_signal': 'alex'}),
            )
            events = list(llm.stream([{'role': 'user', 'content': 'done'}]))
        terminal = events[-1]
        self.assertEqual(terminal.result.tool_input, {'persona_signal': 'alex'})

    def test_terminal_result_carries_provider_usage(self):
        message = _final_text_message('done')
        message.usage = MagicMock(
            input_tokens=40,
            output_tokens=8,
            cache_read_input_tokens=6,
            cache_creation_input_tokens=2,
        )
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['done'], message,
            )
            terminal = list(llm.stream([
                {'role': 'user', 'content': 'hi'},
            ]))[-1]
        self.assertEqual(
            (terminal.result.input_tokens, terminal.result.output_tokens,
             terminal.result.cache_read_tokens,
             terminal.result.cache_write_tokens),
            (40, 8, 6, 2),
        )

    def test_client_built_per_call_from_config(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['hi'], _final_text_message('hi'),
            )
            list(llm.stream([{'role': 'user', 'content': 'hi'}]))
        _, ctor_kwargs = mock_cls.call_args
        self.assertEqual(ctor_kwargs['api_key'], FAKE_KEY)
        self.assertEqual(ctor_kwargs['max_retries'], 6)

    def test_tools_forwarded_to_stream_request(self):
        # #821: tools attach to the SAME streamed generation so the
        # terminal result can carry a tool call without a second complete().
        tool = {'name': 'record', 'description': 'x', 'input_schema': {}}
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['hi'], _final_text_message('hi'),
            )
            list(llm.stream(
                [{'role': 'user', 'content': 'hi'}], tools=[tool],
            ))
        _, stream_kwargs = mock_cls.return_value.messages.stream.call_args
        self.assertEqual(stream_kwargs['tools'], [tool])

    def test_tools_omitted_from_request_when_not_supplied(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['hi'], _final_text_message('hi'),
            )
            list(llm.stream([{'role': 'user', 'content': 'hi'}]))
        _, stream_kwargs = mock_cls.return_value.messages.stream.call_args
        self.assertNotIn('tools', stream_kwargs)


class StreamProviderSelectionTest(_Mixin, TestCase):

    @override_settings(LLM_PROVIDER='openai', LLM_API_KEY=FAKE_KEY)
    def test_unimplemented_provider_raises_before_network(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            with self.assertRaises(LLMError) as ctx:
                llm.stream([{'role': 'user', 'content': 'hi'}])
        self.assertIn('openai', str(ctx.exception))
        self.assertIn('not supported yet', str(ctx.exception))
        mock_cls.assert_not_called()

    @override_settings(LLM_PROVIDER='anthropic', LLM_API_KEY='')
    def test_unconfigured_raises_without_network(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            with self.assertRaises(LLMError):
                llm.stream([{'role': 'user', 'content': 'hi'}])
        mock_cls.assert_not_called()


@LLM_ON
class StreamErrorContractTest(_Mixin, TestCase):

    def test_open_failure_raises_llm_error(self):
        # Error opening the stream (before any delta) -> LLMError eagerly,
        # before the caller starts iterating.
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                [], None,
                open_error=RateLimitError(
                    'rate limited',
                    response=httpx.Response(
                        429, request=httpx.Request('POST', 'https://x/'),
                    ),
                    body=None,
                ),
            )
            with self.assertRaises(LLMError):
                llm.stream([{'role': 'user', 'content': 'hi'}])

    def test_mid_stream_failure_surfaces_after_first_delta(self):
        # First delta is yielded, then the iterator raises. The error is
        # surfaced (re-raised) from the generator so the transport can
        # fall back.
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                ['first chunk'], _final_text_message('first chunk'),
                raise_after=1,
            )
            gen = llm.stream([{'role': 'user', 'content': 'hi'}])
            first = next(gen)
            self.assertEqual(first.kind, STREAM_TEXT_DELTA)
            self.assertEqual(first.text, 'first chunk')
            with self.assertRaises(LLMError):
                next(gen)


@LLM_ON
class StreamTokenSafetyTest(_Mixin, TestCase):

    def test_key_not_in_open_error(self):
        leaky = RateLimitError(
            f'rate limited for key {FAKE_KEY}',
            response=httpx.Response(
                429, request=httpx.Request('POST', 'https://x/'),
            ),
            body=None,
        )
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = _stream_cm(
                [], None, open_error=leaky,
            )
            with self.assertRaises(LLMError) as ctx:
                llm.stream([{'role': 'user', 'content': 'hi'}])
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_key_not_in_mid_stream_error(self):
        # Force a mid-stream failure whose message echoes the key.
        manager = MagicMock()

        def gen():
            yield 'partial'
            raise RuntimeError(f'boom with key {FAKE_KEY}')

        manager.text_stream = gen()
        cm = MagicMock()
        cm.__enter__.return_value = manager
        cm.__exit__.return_value = False
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.stream.return_value = cm
            stream = llm.stream([{'role': 'user', 'content': 'hi'}])
            next(stream)
            with self.assertRaises(LLMError) as ctx:
                next(stream)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
