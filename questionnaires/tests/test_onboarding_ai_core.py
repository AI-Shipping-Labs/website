"""Tests for the pure AI onboarding interview core (issue #804).

The core (:mod:`questionnaires.onboarding_ai`) is Django-independent: the
LLM is mocked at the ``integrations.services.llm.complete`` boundary and
the tests run as :class:`SimpleTestCase` (no DB). They cover the turn
contract, the structured extraction on completion, the
persona-name-never-leaked guarantee, the trace hooks, and the import
isolation that keeps the seam clean for #809.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, tag

from integrations.services.llm import LLMError, LLMResult
from questionnaires.onboarding_ai import (
    _INTERNAL_PERSONA_NAMES,
    GREETING,
    OnboardingExtraction,
    OnboardingTurnResult,
    PersonaInfo,
    PersonaQuestion,
    TraceSink,
    _sanitize,
    run_onboarding_turn,
)

# A complete, valid extraction the mocked tool call returns on the final
# turn. Covers every required field in the appendix schema.
VALID_EXTRACTION = {
    'persona_signal': 'alex',
    'eng_comfort': 4,
    'ai_comfort': 2,
    'primary_goal': 'Ship a RAG chatbot for my docs',
    'goal_category': 'ship_new',
    'time_commitment_hours_per_week': 8,
    'time_profile': 'steady',
    'main_blocker': 'scoping',
    'secondary_blockers': ['time'],
    'accountability_preference': ['Weekly check-ins'],
    'current_project': 'A docs assistant',
    'project_stage': 'idea',
    'target_outcome': 'A deployed assistant my team uses',
    'career_direction': 'ai_engineer',
    'tech_stack_known': ['Python', 'Django'],
    'tech_stack_gaps': ['vector DBs'],
    'in_scope': ['retrieval', 'chat UI'],
    'out_of_scope': ['fine-tuning'],
    'coding_agent_use': 'boilerplate_only',
    'support_wanted': ['Architecture'],
    'learning_track_links': [],
    'hard_deadline': None,
    'plan_horizon': 'single_sprint',
    'notes': 'Wants to move from backend to AI.',
}

CATALOG = [
    PersonaInfo(
        signal='alex',
        archetype='The Engineer transitioning to AI',
        description='Strong eng, low AI',
        questions=[
            PersonaQuestion(
                prompt='What would you like to have achieved 6 to 8 weeks '
                       'from now?',
                question_type='long_text',
            ),
        ],
    ),
]


class OnboardingExtractionSchemaTest(SimpleTestCase):
    """The schema must cover every appendix field, enums included."""

    def test_schema_covers_all_appendix_fields(self):
        props = OnboardingExtraction.model_json_schema()['properties']
        expected = {
            'persona_signal', 'eng_comfort', 'ai_comfort', 'primary_goal',
            'goal_category', 'time_commitment_hours_per_week', 'time_profile',
            'main_blocker', 'secondary_blockers', 'accountability_preference',
            'current_project', 'project_stage', 'target_outcome',
            'career_direction', 'tech_stack_known', 'tech_stack_gaps',
            'in_scope', 'out_of_scope', 'coding_agent_use', 'support_wanted',
            'learning_track_links', 'hard_deadline', 'plan_horizon', 'notes',
        }
        self.assertEqual(set(props), expected)

    def test_persona_signal_enum_values(self):
        validated = OnboardingExtraction.model_validate(VALID_EXTRACTION)
        self.assertEqual(validated.persona_signal.value, 'alex')
        # The documented blend/other signals validate too.
        for signal in ('priya', 'sam', 'taylor', 'blend', 'other'):
            data = dict(VALID_EXTRACTION, persona_signal=signal)
            self.assertEqual(
                OnboardingExtraction.model_validate(data).persona_signal.value,
                signal,
            )

    def test_nullable_fields_accept_null(self):
        data = dict(
            VALID_EXTRACTION, current_project=None, hard_deadline=None,
        )
        validated = OnboardingExtraction.model_validate(data)
        self.assertIsNone(validated.current_project)
        self.assertIsNone(validated.hard_deadline)


@tag('core')
class RunOnboardingTurnTest(SimpleTestCase):
    def test_opening_turn_greets_without_llm_call(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
        ) as mock_complete:
            result = run_onboarding_turn(
                [], member_message=None, persona_catalog=CATALOG,
            )
        mock_complete.assert_not_called()
        self.assertEqual(result.assistant_message, GREETING)
        self.assertFalse(result.is_complete)

    def test_intermediate_turn_returns_assistant_message(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Got it. What blocks your consistency?'),
        ):
            result = run_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='I want to ship a RAG app',
                persona_catalog=CATALOG,
            )
        self.assertIsInstance(result, OnboardingTurnResult)
        self.assertFalse(result.is_complete)
        self.assertIn('blocks your consistency', result.assistant_message)
        self.assertIsNone(result.extraction)
        self.assertIsNone(result.answers)

    def test_completion_turn_returns_validated_extraction_and_answers(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text="Thanks, that's everything.",
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            result = run_onboarding_turn(
                [{'role': 'assistant', 'content': GREETING}],
                member_message='Here are all my answers',
                persona_catalog=CATALOG,
            )
        self.assertTrue(result.is_complete)
        self.assertIsInstance(result.extraction, OnboardingExtraction)
        self.assertEqual(result.extraction.primary_goal,
                         'Ship a RAG chatbot for my docs')
        self.assertTrue(result.answers)
        # The primary-goal spine answer is mapped to a text answer.
        prompts = {a.prompt for a in result.answers}
        self.assertIn(
            'What would you like to have achieved 6 to 8 weeks from now?',
            prompts,
        )

    def test_invalid_tool_input_raises_llmerror(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                tool_input={'persona_signal': 'not-a-real-signal'},
                tool_name='record_onboarding',
            ),
        ):
            with self.assertRaises(LLMError):
                run_onboarding_turn(
                    [], member_message='done',
                    persona_catalog=CATALOG,
                )

    def test_llm_error_propagates(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=LLMError('boom'),
        ):
            with self.assertRaises(LLMError):
                run_onboarding_turn(
                    [], member_message='hello',
                    persona_catalog=CATALOG,
                )


@tag('core')
class PersonaNameNeverLeaksTest(SimpleTestCase):
    """The core must never emit an internal persona name to the member."""

    def test_system_prompt_forbids_persona_names(self):
        captured = {}

        def fake_complete(messages, **kwargs):
            captured['system'] = kwargs.get('system')
            return LLMResult(text='ok')

        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=fake_complete,
        ):
            run_onboarding_turn(
                [], member_message='hi', persona_catalog=CATALOG,
            )
        # The internal names are never injected into the catalog the model
        # sees (only archetype + signal), and the prompt explicitly bans
        # them.
        self.assertNotIn('signal: alex', captured['system'].split('\n')[0])
        self.assertIn('never', captured['system'].lower())

    def test_leaked_persona_name_in_reply_is_sanitized(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='As an Alex, you should focus on shipping.',
            ),
        ):
            result = run_onboarding_turn(
                [], member_message='hi', persona_catalog=CATALOG,
            )
        for name in ('Alex', 'Priya', 'Sam', 'Taylor'):
            self.assertNotIn(name, result.assistant_message)

    def test_completion_closing_message_is_sanitized(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='Great, Taylor! We are done.',
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            result = run_onboarding_turn(
                [], member_message='done', persona_catalog=CATALOG,
            )
        self.assertNotIn('Taylor', result.assistant_message)

    def test_sanitize_codename_probe_reads_grammatically(self):
        # The adversarial codename-probe path: a reply that names every
        # codename, each preceded by a leading article. The sanitized output
        # must (a) leak no codename and (b) carry no broken-article artifact.
        probe_reply = (
            "You're clearly a Taylor, and an Alex or a Priya would also "
            "fit Sam."
        )

        cleaned = _sanitize(probe_reply)

        # (a) No internal codename survives.
        for name in _INTERNAL_PERSONA_NAMES:
            self.assertNotIn(name, cleaned)
        # The member-facing term is used as the replacement.
        self.assertIn('your archetype', cleaned)
        # (b) No dangling leading article from the substitution.
        self.assertNotIn('a your archetype', cleaned.lower())
        self.assertNotIn('an your archetype', cleaned.lower())


class TraceSinkTest(SimpleTestCase):
    def test_trace_hooks_fire_with_request_result_and_parsed(self):
        events = []

        class RecordingSink(TraceSink):
            def on_request(self, *, system, messages, tool):
                events.append(('request', system, messages, tool))

            def on_result(self, *, result, latency_seconds):
                events.append(('result', result, latency_seconds))

            def on_parsed(self, *, parsed):
                events.append(('parsed', parsed))

        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='done',
                tool_input=dict(VALID_EXTRACTION),
                tool_name='record_onboarding',
            ),
        ):
            run_onboarding_turn(
                [], member_message='done',
                persona_catalog=CATALOG, trace=RecordingSink(),
            )
        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ['request', 'result', 'parsed'])
        # The request hook reports the system prompt, messages, and tool.
        _, system, sent_messages, tool = events[0]
        self.assertTrue(system)
        self.assertEqual(sent_messages[-1]['content'], 'done')
        self.assertEqual(tool['name'], 'record_onboarding')
        # The result hook reports the raw result + a latency.
        _, raw, latency = events[1]
        self.assertIsInstance(raw, LLMResult)
        self.assertGreaterEqual(latency, 0)

    def test_trace_error_hook_fires_on_llm_error(self):
        events = []

        class RecordingSink(TraceSink):
            def on_error(self, *, error):
                events.append(error)

        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            side_effect=LLMError('down'),
        ):
            with self.assertRaises(LLMError):
                run_onboarding_turn(
                    [], member_message='hi',
                    persona_catalog=CATALOG, trace=RecordingSink(),
                )
        self.assertEqual(len(events), 1)

    def test_no_sink_runs_without_error(self):
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='hello there'),
        ):
            result = run_onboarding_turn(
                [], member_message='hi', persona_catalog=CATALOG, trace=None,
            )
        self.assertEqual(result.assistant_message, 'hello there')


class OnboardingAiImportIsolationTest(SimpleTestCase):
    """The core must stay Django-independent so #809 can wrap it."""

    def test_module_source_imports_no_django_or_app_models(self):
        import inspect

        from questionnaires import onboarding_ai

        source = inspect.getsource(onboarding_ai)
        forbidden = [
            'from django.db',
            'import django.db',
            'from django.http',
            'HttpRequest',
            'from questionnaires.models',
            'import questionnaires.models',
            'from questionnaires.services',
            'get_object_or_404',
        ]
        for needle in forbidden:
            self.assertNotIn(
                needle, source,
                f'onboarding_ai must not reference {needle!r}',
            )

    def test_loaded_module_has_no_django_db_dependency(self):
        import sys

        from questionnaires import onboarding_ai

        module_globals = vars(onboarding_ai)
        self.assertNotIn('models', module_globals)
        self.assertNotIn('HttpRequest', module_globals)
        self.assertIn('questionnaires.onboarding_ai', sys.modules)
