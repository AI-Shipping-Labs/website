"""Tests for the SSE streaming onboarding chat (issue #806).

The LLM is enabled via ``@override_settings`` and mocked at the
``questionnaires.onboarding_ai.llm`` boundary (``stream`` + ``complete``)
-- CI never opens a live stream. Covers the SSE response shape + headers,
access control, the already-onboarded short-circuit, the streaming-off /
LLM-disabled gating, the persisted-artifact equivalence to the
non-streaming path, the open-error fallback signal, and idempotency on a
v1 retry after a streaming failure.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from integrations.services.llm import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    LLMError,
    LLMResult,
    StreamEvent,
)
from questionnaires.models import OnboardingConversation, Response
from questionnaires.services_onboarding_ai import (
    get_or_create_ai_onboarding_response,
)
from questionnaires.tests.test_onboarding_ai_core import VALID_EXTRACTION

User = get_user_model()

PERSONA_NAMES = ['Alex', 'Priya', 'Sam', 'Taylor']

LLM_ON = override_settings(
    LLM_API_KEY='sk-test-fake', LLM_PROVIDER='anthropic',
    ONBOARDING_AI_ENABLED='true', ONBOARDING_AI_STREAMING='true',
)


def _scripted_stream(deltas, final_text):
    def gen(messages, **kwargs):
        for d in deltas:
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text=d)
        yield StreamEvent(kind=STREAM_DONE, result=LLMResult(text=final_text))
    return gen


def _read(resp):
    return b''.join(resp.streaming_content).decode()


@tag('core')
class StreamAccessControlTest(TestCase):
    def test_anonymous_redirected_to_login(self):
        resp = self.client.post('/onboarding/chat/stream', {'message': 'hi'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


@LLM_ON
@tag('core')
class StreamResponseShapeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='stream-shape@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.get('/onboarding/chat')  # seed greeting

    def test_streams_deltas_and_done_with_sse_headers(self):
        reply = 'What blocks your consistency?'
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(
                ['What blocks ', 'your consistency?'], reply,
            ),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text=reply),
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'ship a RAG app'},
            )
            body = _read(resp)
        self.assertEqual(resp['Content-Type'], 'text/event-stream')
        self.assertEqual(resp['Cache-Control'], 'no-cache')
        self.assertEqual(resp['X-Accel-Buffering'], 'no')
        # Each delta is an SSE event; assembling them reproduces the reply.
        self.assertIn('event: delta', body)
        self.assertIn('What blocks ', body)
        self.assertIn('your consistency?', body)
        self.assertIn('event: done', body)
        self.assertIn('"complete": false', body)

    def test_no_persona_name_in_stream(self):
        reply = 'Tell me more about your goals.'
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream([reply], reply),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text=reply),
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'about me'},
            )
            body = _read(resp)
        for name in PERSONA_NAMES:
            self.assertNotIn(name, body)


@LLM_ON
@tag('core')
class StreamCompletionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='stream-complete@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.get('/onboarding/chat')

    def test_completion_signals_redirect_and_submits_response(self):
        tool_result = LLMResult(
            text='done', tool_input=dict(VALID_EXTRACTION),
            tool_name='record_onboarding',
        )
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(['All set. '], 'All set.'),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=tool_result,
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'all answered'},
            )
            body = _read(resp)
        self.assertIn('event: done', body)
        self.assertIn('"complete": true', body)
        self.assertIn(reverse('home'), body)
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.status, 'submitted')

    def test_streamed_completion_matches_non_streaming_artifacts(self):
        # Run a streamed completion for THIS member and a non-streaming
        # completion for a second member with the SAME mocked extraction;
        # the persisted Response/Answer rows must match in shape.
        tool_result = LLMResult(
            text='done', tool_input=dict(VALID_EXTRACTION),
            tool_name='record_onboarding',
        )
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(['All set.'], 'All set.'),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=tool_result,
        ):
            _read(self.client.post(
                '/onboarding/chat/stream', {'message': 'all answered'},
            ))
        streamed = Response.objects.get(respondent=self.member)

        other = User.objects.create_user(
            email='stream-nonstream@test.com', password='pw',
        )
        response, conversation = get_or_create_ai_onboarding_response(other)
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=tool_result,
        ):
            from questionnaires.services_onboarding_ai import run_member_turn
            run_member_turn(conversation, 'all answered')
        non_streamed = Response.objects.get(respondent=other)

        self.assertEqual(streamed.status, non_streamed.status)
        self.assertEqual(
            streamed.questionnaire_id, non_streamed.questionnaire_id,
        )

        def answer_map(resp):
            return {
                a.question.prompt: (
                    a.text_value, a.number_value,
                    sorted(o.label for o in a.selected_options.all()),
                )
                for a in resp.answers.select_related('question')
            }
        self.assertEqual(answer_map(streamed), answer_map(non_streamed))
        # Exactly one onboarding response per member.
        self.assertEqual(
            Response.objects.filter(respondent=self.member).count(), 1,
        )


@LLM_ON
@tag('core')
class StreamFallbackTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='stream-fallback@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.get('/onboarding/chat')

    def test_open_error_emits_fallback_and_persists_nothing(self):
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=LLMError('open failed'),
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hello'},
            )
            body = _read(resp)
        self.assertIn('event: fallback', body)
        self.assertIn('stream-error', body)
        # Nothing persisted to the transcript -> a v1 retry is the first
        # write (no duplicate turn). Only the seeded greeting is present.
        conversation = OnboardingConversation.objects.get(
            response__respondent=self.member,
        )
        roles = [t['role'] for t in conversation.transcript]
        self.assertEqual(roles, ['assistant'])  # greeting only

    def test_retry_via_v1_after_stream_failure_is_idempotent(self):
        # Stream fails (nothing persisted), then the client retries the
        # SAME message via the v1 non-streaming endpoint.
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=LLMError('open failed'),
        ):
            _read(self.client.post(
                '/onboarding/chat/stream', {'message': 'my reply'},
            ))
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Thanks, tell me more.'),
        ):
            self.client.post(
                '/onboarding/chat/message', {'message': 'my reply'},
            )
        conversation = OnboardingConversation.objects.get(
            response__respondent=self.member,
        )
        user_turns = [
            t for t in conversation.transcript
            if t['role'] == 'user' and t['content'] == 'my reply'
        ]
        self.assertEqual(len(user_turns), 1)  # not duplicated

    def test_mid_stream_error_emits_fallback(self):
        def gen(messages, **kwargs):
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='partial ')
            raise LLMError('mid-stream drop')

        with patch(
            'questionnaires.onboarding_ai.llm.stream', side_effect=gen,
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hello'},
            )
            body = _read(resp)
        self.assertIn('event: delta', body)
        self.assertIn('partial ', body)
        self.assertIn('event: fallback', body)
        # No turn persisted on a mid-stream failure.
        conversation = OnboardingConversation.objects.get(
            response__respondent=self.member,
        )
        self.assertEqual(
            [t['role'] for t in conversation.transcript], ['assistant'],
        )


@LLM_ON
@tag('core')
class StreamGatingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='stream-gate@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def test_chat_page_marks_streaming_enabled(self):
        resp = self.client.get('/onboarding/chat')
        self.assertContains(resp, 'data-streaming="1"')

    @override_settings(ONBOARDING_AI_STREAMING='false')
    def test_chat_page_omits_streaming_flag_when_off(self):
        resp = self.client.get('/onboarding/chat')
        self.assertNotContains(resp, 'data-streaming="1"')

    @override_settings(ONBOARDING_AI_STREAMING='false')
    def test_stream_endpoint_emits_fallback_when_off(self):
        self.client.get('/onboarding/chat')
        with patch('questionnaires.onboarding_ai.llm.stream') as mock_stream:
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hi'},
            )
            body = _read(resp)
        mock_stream.assert_not_called()
        self.assertIn('event: fallback', body)
        self.assertIn('streaming-disabled', body)

    @override_settings(LLM_API_KEY='')
    def test_stream_endpoint_emits_fallback_when_llm_disabled(self):
        resp = self.client.post(
            '/onboarding/chat/stream', {'message': 'hi'},
        )
        body = _read(resp)
        self.assertIn('event: fallback', body)


@LLM_ON
@tag('core')
class StreamAlreadyOnboardedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='stream-done@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done', tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            from questionnaires.services_onboarding_ai import run_member_turn
            run_member_turn(conversation, 'all done')

    def test_stream_endpoint_signals_completion_not_restart(self):
        with patch('questionnaires.onboarding_ai.llm.stream') as mock_stream:
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hi again'},
            )
            body = _read(resp)
        mock_stream.assert_not_called()
        self.assertIn('event: done', body)
        self.assertIn(reverse('onboarding_start'), body)
        # No second onboarding response created.
        self.assertEqual(
            Response.objects.filter(respondent=self.member).count(), 1,
        )


@tag('core')
class StreamCrossMemberIsolationTest(TestCase):
    """A member can only ever stream into their OWN conversation.

    There is no conversation id in the URL: the endpoint always resolves
    the logged-in member's own onboarding response. So member A's request
    can never address member B's conversation; A only ever drives A's own.
    """

    @LLM_ON
    def test_stream_uses_only_the_logged_in_members_response(self):
        member_a = User.objects.create_user(email='a@test.com', password='pw')
        member_b = User.objects.create_user(email='b@test.com', password='pw')
        # B starts a conversation.
        get_or_create_ai_onboarding_response(member_b)

        self.client.force_login(member_a)
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=_scripted_stream(['Hi A'], 'Hi A'),
        ), patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Hi A'),
        ):
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hello'},
            )
            _read(resp)
        # A's turn landed on A's own conversation; B's transcript is empty.
        conv_a = OnboardingConversation.objects.get(
            response__respondent=member_a,
        )
        conv_b = OnboardingConversation.objects.get(
            response__respondent=member_b,
        )
        self.assertTrue(any(
            t['role'] == 'user' and t['content'] == 'hello'
            for t in conv_a.transcript
        ))
        self.assertEqual(conv_b.transcript, [])
