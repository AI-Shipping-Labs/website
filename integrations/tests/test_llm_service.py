"""Tests for the provider-neutral LLM service (issue #799).

The Anthropic SDK is fully mocked in every test — CI never hits a live
API. All key values are obvious fakes (``sk-test-fake-*``); no real key
appears anywhere.

Covers: config resolution order, ``is_enabled()`` gating, backend
selection (including unimplemented providers), per-call client
construction from config, model/base-URL forwarding, structured output,
retry/backoff on transient errors, immediate failure on non-transient
errors, the unconfigured guard, and the token-safety invariant.
"""

from unittest.mock import MagicMock, patch

import httpx
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from integrations.services import llm
from integrations.services.llm import CancellationToken, LLMError, LLMResult

FAKE_KEY = 'sk-test-fake-secret123'

# Patch target for the Anthropic client class used inside the backend.
ANTHROPIC_PATCH = 'anthropic.Anthropic'
SLEEP_PATCH = 'integrations.services.llm.backends.time.sleep'


def _set_setting(key, value, *, is_secret=False):
    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={
            'value': value,
            'is_secret': is_secret,
            'group': 'llm',
            'description': '',
        },
    )
    clear_config_cache()


def _text_response(text='hello world'):
    """Build a mock Anthropic Messages response with a text block."""
    block = MagicMock()
    block.type = 'text'
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = None
    return response


def _tool_response(tool_name='verdict', tool_input=None):
    """Build a mock Anthropic Messages response with a tool_use block."""
    if tool_input is None:
        tool_input = {'verdict': 'ai-first'}
    block = MagicMock()
    block.type = 'tool_use'
    block.name = tool_name
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response


def _make_transient(exc_type):
    """Construct a transient SDK exception with the httpx plumbing it needs."""
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    if exc_type is APITimeoutError:
        return APITimeoutError(request=req)
    if exc_type is APIConnectionError:
        return APIConnectionError(message='connection error', request=req)
    status = 429 if exc_type is RateLimitError else 500
    resp = httpx.Response(status, request=req)
    return exc_type('transient', response=resp, body=None)


def _make_auth_error():
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    resp = httpx.Response(401, request=req)
    return AuthenticationError('invalid api key', response=resp, body=None)


class _LLMCacheCleanupMixin:
    """Clear the module-level config cache around each test."""

    def setUp(self):
        super().setUp()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)
class IsEnabledTest(_LLMCacheCleanupMixin, TestCase):

    @override_settings(LLM_API_KEY='')
    def test_disabled_when_key_empty(self):
        self.assertFalse(llm.is_enabled())

    @override_settings(LLM_API_KEY=FAKE_KEY)
    def test_enabled_when_key_set_and_provider_implemented(self):
        self.assertTrue(llm.is_enabled())

    @override_settings(LLM_API_KEY=FAKE_KEY, LLM_PROVIDER='openai')
    def test_disabled_when_provider_unimplemented(self):
        self.assertFalse(llm.is_enabled())

    @override_settings(LLM_API_KEY='')
    def test_enabled_after_db_override_sets_key(self):
        self.assertFalse(llm.is_enabled())
        _set_setting('LLM_API_KEY', FAKE_KEY, is_secret=True)
        self.assertTrue(llm.is_enabled())


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)
class ConfigResolutionTest(_LLMCacheCleanupMixin, TestCase):

    def test_db_override_wins_over_settings(self):
        # settings default is FAKE_KEY; DB override must win.
        _set_setting('LLM_API_KEY', 'sk-test-fake-db-value', is_secret=True)
        self.assertEqual(get_config('LLM_API_KEY'), 'sk-test-fake-db-value')

    @override_settings(LLM_BASE_URL='https://api.anthropic.com')
    def test_base_url_default_when_no_db_row(self):
        self.assertEqual(
            get_config('LLM_BASE_URL'), 'https://api.anthropic.com',
        )


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)
class CompleteTextTest(_LLMCacheCleanupMixin, TestCase):

    def test_cancellation_token_closes_provider_client(self):
        token = CancellationToken()
        with patch(ANTHROPIC_PATCH) as mock_cls:
            client = mock_cls.return_value
            client.messages.create.return_value = _text_response('done')
            llm.complete(
                [{'role': 'user', 'content': 'hi'}], cancellation=token,
            )
            token.cancel()
        client.close.assert_called_once_with()

    def test_returns_text_result(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response('hi there')
            result = llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertIsInstance(result, LLMResult)
        self.assertEqual(result.text, 'hi there')
        self.assertIsNone(result.tool_input)

    def test_returns_available_provider_usage(self):
        response = _text_response('hi')
        response.usage = MagicMock(
            input_tokens=101,
            output_tokens=23,
            cache_read_input_tokens=17,
            cache_creation_input_tokens=5,
        )
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_cls.return_value.messages.create.return_value = response
            result = llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(
            (result.input_tokens, result.output_tokens,
             result.cache_read_tokens, result.cache_write_tokens),
            (101, 23, 17, 5),
        )

    def test_uses_configured_model_by_default(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete([{'role': 'user', 'content': 'hi'}])
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs['model'], 'claude-sonnet-4-5')

    def test_explicit_model_overrides_config(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete(
                [{'role': 'user', 'content': 'hi'}],
                model='claude-opus-4-1',
            )
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs['model'], 'claude-opus-4-1')

    def test_forwards_system_and_temperature(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete(
                [{'role': 'user', 'content': 'hi'}],
                system='be terse',
                temperature=0.2,
            )
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs['system'], 'be terse')
        self.assertEqual(kwargs['temperature'], 0.2)

    def test_client_built_with_max_retries_six(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete([{'role': 'user', 'content': 'hi'}])
        _, ctor_kwargs = mock_cls.call_args
        self.assertEqual(ctor_kwargs['max_retries'], 6)
        self.assertEqual(ctor_kwargs['api_key'], FAKE_KEY)


class CompleteBaseUrlTest(_LLMCacheCleanupMixin, TestCase):

    @override_settings(
        LLM_PROVIDER='anthropic',
        LLM_API_KEY=FAKE_KEY,
        LLM_BASE_URL='https://gateway.example/v1',
        LLM_MODEL='claude-sonnet-4-5',
        LLM_MAX_RETRIES=6,
    )
    def test_gateway_base_url_passed_to_client(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete([{'role': 'user', 'content': 'hi'}])
        _, ctor_kwargs = mock_cls.call_args
        self.assertEqual(ctor_kwargs['base_url'], 'https://gateway.example/v1')
        self.assertEqual(ctor_kwargs['max_retries'], 6)

    @override_settings(
        LLM_PROVIDER='anthropic',
        LLM_API_KEY=FAKE_KEY,
        LLM_BASE_URL='https://api.anthropic.com',
        LLM_MODEL='claude-sonnet-4-5',
        LLM_MAX_RETRIES=6,
    )
    def test_client_rebuilt_from_config_each_call(self):
        # No module-level client: changing the DB base URL between calls
        # changes the constructed client config.
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _text_response()
            llm.complete([{'role': 'user', 'content': 'hi'}])
            _set_setting('LLM_BASE_URL', 'https://other.example/v1')
            llm.complete([{'role': 'user', 'content': 'hi'}])
        first_kwargs = mock_cls.call_args_list[0].kwargs
        second_kwargs = mock_cls.call_args_list[1].kwargs
        self.assertEqual(first_kwargs['base_url'], 'https://api.anthropic.com')
        self.assertEqual(second_kwargs['base_url'], 'https://other.example/v1')


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)
class StructuredOutputTest(_LLMCacheCleanupMixin, TestCase):

    def test_forwards_tools_and_returns_tool_input(self):
        tool = {
            'name': 'verdict',
            'description': 'verdict',
            'input_schema': {'type': 'object', 'properties': {}},
        }
        tool_choice = {'type': 'tool', 'name': 'verdict'}
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _tool_response(
                tool_input={'verdict': 'ai-first'},
            )
            result = llm.complete(
                [{'role': 'user', 'content': 'classify'}],
                tools=[tool],
                tool_choice=tool_choice,
            )
        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs['tools'], [tool])
        self.assertEqual(kwargs['tool_choice'], tool_choice)
        self.assertEqual(result.tool_input, {'verdict': 'ai-first'})


class ProviderSelectionTest(_LLMCacheCleanupMixin, TestCase):

    @override_settings(LLM_PROVIDER='openai', LLM_API_KEY=FAKE_KEY)
    def test_unimplemented_provider_raises_without_network(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            with self.assertRaises(LLMError) as ctx:
                llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertIn('openai', str(ctx.exception))
        mock_cls.assert_not_called()


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=2,
)
class RetryTest(_LLMCacheCleanupMixin, TestCase):

    def test_transient_retried_then_raises_llm_error(self):
        with patch(SLEEP_PATCH) as mock_sleep, patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = _make_transient(
                RateLimitError,
            )
            with self.assertRaises(LLMError):
                llm.complete([{'role': 'user', 'content': 'hi'}])
        # max_retries=2 -> 3 total attempts (1 initial + 2 retries).
        self.assertEqual(mock_client.messages.create.call_count, 3)
        # 2 sleeps between the 3 attempts; real sleep never happens.
        self.assertEqual(mock_sleep.call_count, 2)

    def test_succeeds_after_transient_then_recovery(self):
        with patch(SLEEP_PATCH), patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = [
                _make_transient(APIConnectionError),
                _text_response('recovered'),
            ]
            result = llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(result.text, 'recovered')
        self.assertEqual(mock_client.messages.create.call_count, 2)

    def test_timeout_and_internal_server_error_are_transient(self):
        for exc_type in (APITimeoutError, InternalServerError):
            with self.subTest(exc=exc_type.__name__):
                clear_config_cache()
                with patch(SLEEP_PATCH), patch(ANTHROPIC_PATCH) as mock_cls:
                    mock_client = mock_cls.return_value
                    mock_client.messages.create.side_effect = _make_transient(
                        exc_type,
                    )
                    with self.assertRaises(LLMError):
                        llm.complete([{'role': 'user', 'content': 'hi'}])
                self.assertEqual(mock_client.messages.create.call_count, 3)

    def test_non_transient_error_not_retried(self):
        with patch(SLEEP_PATCH) as mock_sleep, patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = _make_auth_error()
            with self.assertRaises(LLMError):
                llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(mock_client.messages.create.call_count, 1)
        mock_sleep.assert_not_called()


class UnconfiguredTest(_LLMCacheCleanupMixin, TestCase):

    @override_settings(LLM_PROVIDER='anthropic', LLM_API_KEY='')
    def test_raises_without_network_when_key_empty(self):
        with patch(ANTHROPIC_PATCH) as mock_cls:
            with self.assertRaises(LLMError):
                llm.complete([{'role': 'user', 'content': 'hi'}])
        mock_cls.assert_not_called()


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=1,
)
class TokenSafetyTest(_LLMCacheCleanupMixin, TestCase):

    def test_key_not_in_error_after_transient(self):
        with patch(SLEEP_PATCH), patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = _make_transient(
                RateLimitError,
            )
            with self.assertRaises(LLMError) as ctx:
                llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_key_not_in_error_when_sdk_echoes_it(self):
        # Even if a non-transient SDK error string contains the key, the
        # wrapper scrubs it before raising.
        req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
        resp = httpx.Response(400, request=req)
        from anthropic import BadRequestError
        leaky = BadRequestError(
            f'bad request with key {FAKE_KEY}', response=resp, body=None,
        )
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = leaky
            with self.assertRaises(LLMError) as ctx:
                llm.complete([{'role': 'user', 'content': 'hi'}])
        self.assertNotIn(FAKE_KEY, str(ctx.exception))


@override_settings(
    LLM_PROVIDER='anthropic',
    LLM_API_KEY=FAKE_KEY,
    LLM_BASE_URL='https://api.anthropic.com',
    LLM_MODEL='claude-sonnet-4-5',
    LLM_MAX_RETRIES=6,
)
class EmptyResponseTest(_LLMCacheCleanupMixin, TestCase):

    def test_empty_response_raises(self):
        empty = MagicMock()
        empty.content = []
        with patch(ANTHROPIC_PATCH) as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = empty
            with self.assertRaises(LLMError):
                llm.complete([{'role': 'user', 'content': 'hi'}])
