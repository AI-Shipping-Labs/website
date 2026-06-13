"""Tests for the AI onboarding chat views + routing (issue #804).

The LLM is enabled via ``@override_settings`` (key + provider) and mocked
at ``integrations.services.llm.complete`` -- CI never makes a live call.
Covers the is_enabled/flag gating, the visible switch-to-form link, the
graceful fallback on ``LLMError``, access control, the
already-onboarded confirmation, and that the internal persona signal
never reaches a member-facing page.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from integrations.services.llm import LLMError, LLMResult
from questionnaires.models import OnboardingConversation, Response
from questionnaires.services_onboarding_ai import (
    get_or_create_ai_onboarding_response,
)
from questionnaires.tests.test_onboarding_ai_core import VALID_EXTRACTION

User = get_user_model()

PERSONA_NAMES = ['Alex', 'Priya', 'Sam', 'Taylor']

# Enable the LLM service: a key + an implemented provider.
LLM_ON = override_settings(
    LLM_API_KEY='sk-test-fake', LLM_PROVIDER='anthropic',
    ONBOARDING_AI_ENABLED='true',
)


@tag('core')
class AnonymousAccessTest(TestCase):
    def test_chat_redirects_anonymous_to_login(self):
        resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_chat_message_redirects_anonymous_to_login(self):
        resp = self.client.post('/onboarding/chat/message', {'message': 'hi'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


@LLM_ON
@tag('core')
class RoutingGatingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-route@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def test_onboarding_landing_routes_to_chat_when_ai_available(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_chat'))

    def test_chat_greets_member(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
        ) as mock_complete:
            resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-chat-transcript"')
        # The greeting is deterministic -- no LLM call to open.
        mock_complete.assert_not_called()
        self.assertContains(resp, 'build the right plan')

    def test_chat_shows_switch_to_form_link(self):
        resp = self.client.get('/onboarding/chat')
        self.assertContains(resp, 'data-testid="onboarding-switch-to-form"')

    @override_settings(ONBOARDING_AI_ENABLED='false')
    def test_flag_off_renders_form_not_chat(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)
        # The #802 self-ID form, not a redirect to chat.
        self.assertContains(resp, 'data-testid="onboarding-identify-form"')

    @override_settings(ONBOARDING_AI_ENABLED='false')
    def test_chat_url_redirects_to_form_when_flag_off(self):
        resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))


@tag('core')
class LlmDisabledTest(TestCase):
    """With no key configured, the chat is never offered -- only the form."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-disabled@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    @override_settings(LLM_API_KEY='')
    def test_landing_renders_form_when_llm_disabled(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-identify-form"')

    @override_settings(LLM_API_KEY='')
    def test_chat_url_redirects_to_form_when_llm_disabled(self):
        resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))


@LLM_ON
@tag('core')
class ChatTurnTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-turn@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        # Seed the greeting.
        self.client.get('/onboarding/chat')

    def test_member_turn_returns_assistant_reply_in_response(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='What blocks your consistency?'),
        ):
            resp = self.client.post(
                '/onboarding/chat/message',
                {'message': 'I want to ship a RAG app'},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'What blocks your consistency?')

    def test_completion_redirects_with_thank_you(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            resp = self.client.post(
                '/onboarding/chat/message', {'message': 'all answered'},
            )
        self.assertEqual(resp.status_code, 302)
        # Land on the end-of-onboarding completion screen (#951), not home.
        self.assertEqual(resp['Location'], reverse('onboarding_start'))
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.status, 'submitted')

    def test_llm_error_routes_to_form_fallback(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=LLMError('down'),
        ):
            resp = self.client.post(
                '/onboarding/chat/message', {'message': 'hello'},
            )
        self.assertEqual(resp.status_code, 302)
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(resp['Location'], reverse('onboarding_questions'))
        # The draft response is preserved (not deleted) with questions.
        self.assertEqual(response.status, 'draft')
        self.assertTrue(response.response_questions.exists())


@LLM_ON
@tag('core')
class AlreadyOnboardedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-done@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        # Complete onboarding via the AI path first.
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            from questionnaires.services_onboarding_ai import run_member_turn
            run_member_turn(conversation, 'all done')

    def test_landing_shows_completion_not_chat(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'accounts/onboarding_complete.html')

    def test_chat_url_redirects_completed_member(self):
        resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))
        # No new conversation started beyond the one already there.
        self.assertEqual(
            OnboardingConversation.objects.filter(
                response__respondent=self.member,
            ).count(),
            1,
        )


@LLM_ON
@tag('core')
class PersonaSignalNeverLeaksTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ai-leak@test.com', password='pw',
        )

    def test_chat_page_has_no_persona_name(self):
        self.client.force_login(self.member)
        resp = self.client.get('/onboarding/chat')
        body = resp.content.decode()
        for name in PERSONA_NAMES:
            # The base template author meta carries "Alexey"; assert the
            # standalone persona names are absent from the chat surface.
            self.assertNotIn(f'>{name}<', body)
            self.assertNotIn(f' {name},', body)

    def test_staff_response_detail_shows_persona_signal(self):
        # Complete via AI so a persona_signal is stored.
        response, conversation = get_or_create_ai_onboarding_response(
            self.member,
        )
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            from questionnaires.services_onboarding_ai import run_member_turn
            run_member_turn(conversation, 'all done')
        response.refresh_from_db()

        staff = User.objects.create_user(
            email='staff-ai@test.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(staff)
        url = reverse(
            'studio_questionnaire_response_detail',
            kwargs={
                'questionnaire_id': response.questionnaire_id,
                'response_id': response.pk,
            },
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, 'data-testid="response-detail-persona-signal"',
        )
        self.assertContains(resp, 'alex')
