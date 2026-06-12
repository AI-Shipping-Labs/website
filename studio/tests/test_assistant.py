"""Tests for the Studio AI assistant (issue #872, Phase 1).

The LLM boundary is mocked at ``studio.services.assistant.llm`` so CI
never makes a live call. These assert the wiring, staff gating, the field
whitelist, the single-``complete``-call invariant, member resolution, the
not-configured state, and the audit trail — not live model behaviour.
"""

import inspect
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from crm.models import CRMRecord
from integrations.services.llm import LLMResult
from plans.models import InterviewNote
from studio.models import AssistantActionLog
from studio.services.assistant import TOOL_ADD_NOTE, TOOL_UPDATE_PROFILE

User = get_user_model()


def _note_result(email, body, kind=None, visibility=None):
    payload = {'member_email': email, 'body': body}
    if kind is not None:
        payload['kind'] = kind
    if visibility is not None:
        payload['visibility'] = visibility
    return LLMResult(tool_input=payload, tool_name=TOOL_ADD_NOTE)


def _profile_result(email, **fields):
    payload = {'member_email': email, **fields}
    return LLMResult(tool_input=payload, tool_name=TOOL_UPDATE_PROFILE)


def _decline_result(text='I can only add notes or update profiles.'):
    return LLMResult(text=text, tool_input=None, tool_name=None)


@tag('core')
class AssistantAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.url = reverse('studio_assistant')

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_gets_403(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    @patch('studio.views.assistant.llm.is_enabled', return_value=True)
    def test_staff_sees_form(self, _enabled):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="assistant-form"')


@tag('core')
class AssistantProposeConfirmTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='jane@example.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)
        cls.url = reverse('studio_assistant')

    def setUp(self):
        self.client.force_login(self.staff)

    def _propose(self, request_text, llm_result):
        with patch(
            'studio.services.assistant.llm.is_enabled', return_value=True,
        ), patch('studio.views.assistant.llm.is_enabled', return_value=True), \
                patch(
                    'studio.services.assistant.llm.complete',
                    return_value=llm_result,
                ) as mock_complete:
            response = self.client.post(self.url, {
                'action': 'propose',
                'request_text': request_text,
            })
        return response, mock_complete

    def _confirm(self, tool_name, payload_json):
        with patch(
            'studio.views.assistant.llm.is_enabled', return_value=True,
        ), patch(
            'studio.services.assistant.llm.complete',
        ) as mock_complete:
            response = self.client.post(self.url, {
                'action': 'confirm',
                'tool_name': tool_name,
                'payload': payload_json,
            })
        return response, mock_complete

    def test_add_note_proposal_then_confirm_creates_one_note(self):
        response, _ = self._propose(
            'Add a note to jane@example.com: wants the Premium teardown',
            _note_result('jane@example.com', 'wants the Premium teardown'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="assistant-proposal"')
        # Nothing written at propose time.
        self.assertEqual(InterviewNote.objects.count(), 0)

        payload_json = response.context['proposal']['payload_json']
        confirm, _ = self._confirm(TOOL_ADD_NOTE, payload_json)
        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, 'data-testid="assistant-result"')

        notes = InterviewNote.objects.filter(member=self.member)
        self.assertEqual(notes.count(), 1)
        note = notes.get()
        self.assertEqual(note.body, 'wants the Premium teardown')
        self.assertEqual(note.created_by, self.staff)
        self.assertEqual(note.kind, 'general')
        self.assertEqual(note.visibility, 'internal')

    def test_update_profile_confirm_updates_only_proposed_field(self):
        self.record.persona = 'original-persona'
        self.record.summary = 'original-summary'
        self.record.next_steps = 'original-next'
        self.record.save()

        response, _ = self._propose(
            'Archive jane@example.com in the CRM',
            _profile_result('jane@example.com', status='archived'),
        )
        payload_json = response.context['proposal']['payload_json']
        self._confirm(TOOL_UPDATE_PROFILE, payload_json)

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'archived')
        # Other fields untouched.
        self.assertEqual(self.record.persona, 'original-persona')
        self.assertEqual(self.record.summary, 'original-summary')
        self.assertEqual(self.record.next_steps, 'original-next')

    def test_propose_confirm_calls_complete_exactly_once(self):
        response, propose_mock = self._propose(
            'Archive jane@example.com',
            _profile_result('jane@example.com', status='archived'),
        )
        self.assertEqual(propose_mock.call_count, 1)
        payload_json = response.context['proposal']['payload_json']

        # At confirm time the model must NOT be re-invoked.
        _, confirm_mock = self._confirm(TOOL_UPDATE_PROFILE, payload_json)
        self.assertEqual(confirm_mock.call_count, 0)
        # And the executed payload equals the reviewed one.
        log = AssistantActionLog.objects.get(
            outcome=AssistantActionLog.OUTCOME_SUCCESS,
        )
        self.assertEqual(log.payload['status'], 'archived')
        self.assertEqual(log.payload['member_email'], 'jane@example.com')

    def test_non_whitelisted_field_rejected_at_execute(self):
        # Craft a confirm payload directly naming a non-whitelisted key.
        import json
        bad_payload = json.dumps({
            'member_email': 'jane@example.com', 'is_staff': True,
        })
        confirm, _ = self._confirm(TOOL_UPDATE_PROFILE, bad_payload)
        self.assertContains(confirm, 'data-testid="assistant-error"')
        self.assertContains(confirm, 'is_staff')
        # No write; an error row is logged.
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'active')
        self.assertEqual(
            AssistantActionLog.objects.filter(
                outcome=AssistantActionLog.OUTCOME_SUCCESS,
            ).count(),
            0,
        )

    def test_decline_writes_nothing(self):
        response, mock = self._propose(
            'Delete all events from last year',
            _decline_result('I can only add notes or update profiles.'),
        )
        self.assertEqual(mock.call_count, 1)
        self.assertContains(response, 'data-testid="assistant-declined"')
        self.assertEqual(InterviewNote.objects.count(), 0)
        self.assertEqual(AssistantActionLog.objects.count(), 0)

    def test_unknown_member_proposes_no_write(self):
        response, _ = self._propose(
            'Add a note to nobody@nowhere.test: hi',
            _note_result('nobody@nowhere.test', 'hi'),
        )
        self.assertContains(response, 'data-testid="assistant-error"')
        self.assertContains(response, 'nobody@nowhere.test')
        # No confirm form, no note.
        self.assertNotContains(response, 'data-testid="assistant-proposal"')
        self.assertEqual(InterviewNote.objects.count(), 0)

    def test_update_profile_without_crm_record_is_declined(self):
        untracked = User.objects.create_user(
            email='untracked@test.com', password='pw',
        )
        response, _ = self._propose(
            'Archive untracked@test.com',
            _profile_result('untracked@test.com', status='archived'),
        )
        self.assertContains(response, 'data-testid="assistant-error"')
        self.assertContains(response, 'not tracked in the CRM')
        self.assertFalse(CRMRecord.objects.filter(user=untracked).exists())

    def test_successful_and_failed_executes_each_log_one_row(self):
        # Success.
        response, _ = self._propose(
            'Add a note to jane@example.com: hello',
            _note_result('jane@example.com', 'hello'),
        )
        payload_json = response.context['proposal']['payload_json']
        self._confirm(TOOL_ADD_NOTE, payload_json)
        success = AssistantActionLog.objects.filter(
            outcome=AssistantActionLog.OUTCOME_SUCCESS,
        )
        self.assertEqual(success.count(), 1)
        row = success.get()
        self.assertEqual(row.actor, self.staff)
        self.assertEqual(row.tool_name, TOOL_ADD_NOTE)
        self.assertEqual(row.target_member, self.member)
        self.assertEqual(row.payload['body'], 'hello')

        # Failure: confirm an add_member_note for a now-unknown email.
        import json
        bad = json.dumps({'member_email': 'gone@nowhere.test', 'body': 'x'})
        self._confirm(TOOL_ADD_NOTE, bad)
        error = AssistantActionLog.objects.filter(
            outcome=AssistantActionLog.OUTCOME_ERROR,
        )
        self.assertEqual(error.count(), 1)
        self.assertIn('gone@nowhere.test', error.get().message)


@tag('core')
class AssistantNotConfiguredTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.url = reverse('studio_assistant')

    def setUp(self):
        self.client.force_login(self.staff)

    @patch('studio.views.assistant.llm.is_enabled', return_value=False)
    def test_get_shows_not_configured(self, _enabled):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="assistant-not-configured"')

    @patch('studio.views.assistant.llm.is_enabled', return_value=False)
    def test_post_does_not_500(self, _enabled):
        response = self.client.post(self.url, {
            'action': 'propose', 'request_text': 'do something',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="assistant-not-configured"')


class AssistantImportIsolationTest(TestCase):
    """The service module must reuse the LLM seam and not hardcode a model."""

    def test_no_direct_anthropic_import_or_hardcoded_model(self):
        from studio.services import assistant

        source = inspect.getsource(assistant)
        self.assertIn('from integrations.services import llm', source)
        forbidden = [
            'import anthropic',
            'from anthropic',
            "'claude-",
            '"claude-',
            'claude-sonnet',
            'claude-opus',
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, source,
                f'assistant service must not reference {needle!r}',
            )
