"""Exact onboarding catalog and provider prompt contract for issue #1844."""

from unittest.mock import patch

from community_base.questionnaires.persona_catalog import render_persona_catalog
from django.test import SimpleTestCase, tag

from integrations.services.llm import STREAM_DONE, LLMResult, StreamEvent
from questionnaires.onboarding_ai import (
    SYSTEM_PROMPT,
    PersonaInfo,
    PersonaQuestion,
    _build_system_prompt,
    run_onboarding_turn,
    stream_onboarding_turn,
)

HEADER = (
    "Archetypes to reason about (internal signal in brackets -- never "
    "say it to the member). Once you commit to one archetype, prioritise "
    "ITS delta questions below and skip the others':"
)
SHARED_HEADER = "Shared spine -- ask these of EVERY member regardless of archetype:"
SINGLE_CATALOG = [
    PersonaInfo(
        signal='alex', archetype='Builder', description='Builds projects.',
        questions=[
            PersonaQuestion(prompt='Choose path', question_type='choice',
                            options=['Build', 'Learn']),
            PersonaQuestion(prompt='Choose path', question_type='text'),
        ],
    ),
]
MULTI_CATALOG = [
    PersonaInfo(
        signal='alex', archetype='Builder', description='Builds projects.',
        questions=[
            PersonaQuestion(prompt='Choose path', question_type='choice',
                            options=['Build', 'Learn']),
            PersonaQuestion(prompt='Choose path', question_type='text'),
            PersonaQuestion(prompt='Builder goal', question_type='text'),
            PersonaQuestion(prompt='Builder goal', question_type='number'),
        ],
    ),
    PersonaInfo(
        signal='taylor', archetype='Researcher',
        questions=[
            PersonaQuestion(prompt='Choose path', question_type='choice',
                            options=['Research', 'Deploy']),
            PersonaQuestion(prompt='Research goal', question_type='text'),
        ],
    ),
]
SINGLE_RENDERED = '\n'.join([
    HEADER,
    '',
    '- Builder [signal: alex]',
    '  Builds projects.',
    '  Delta questions (specific to this archetype):',
    '  - (choice) Choose path options: Build, Learn',
    '  - (text) Choose path',
])
MULTI_RENDERED = '\n'.join([
    SHARED_HEADER,
    '  - (choice) Choose path options: Build, Learn',
    '',
    HEADER,
    '',
    '- Builder [signal: alex]',
    '  Builds projects.',
    '  Delta questions (specific to this archetype):',
    '  - (text) Builder goal',
    '  - (number) Builder goal',
    '',
    '- Researcher [signal: taylor]',
    '  Delta questions (specific to this archetype):',
    '  - (text) Research goal',
])


@tag('core')
class PersonaCatalogExactOutputTest(SimpleTestCase):
    def test_empty_catalog_keeps_base_prompt_exactly(self):
        self.assertEqual(render_persona_catalog([]), '')
        self.assertEqual(_build_system_prompt([]), SYSTEM_PROMPT)
        self.assertEqual(render_persona_catalog(None), '')

    def test_single_persona_keeps_every_question_in_order(self):
        self.assertEqual(render_persona_catalog(SINGLE_CATALOG), SINGLE_RENDERED)
        self.assertEqual(
            _build_system_prompt(SINGLE_CATALOG),
            f'{SYSTEM_PROMPT}\n\n{SINGLE_RENDERED}',
        )

    def test_multiple_personas_factor_shared_prompts_once(self):
        self.assertEqual(render_persona_catalog(MULTI_CATALOG), MULTI_RENDERED)
        self.assertEqual(
            _build_system_prompt(MULTI_CATALOG),
            f'{SYSTEM_PROMPT}\n\n{MULTI_RENDERED}',
        )

    def test_malformed_catalog_still_raises(self):
        with self.assertRaises(AttributeError):
            render_persona_catalog([object()])
        broken = PersonaInfo(
            signal='alex', archetype='Builder',
            questions=[PersonaQuestion.model_construct(
                prompt='Choose', question_type='choice', options=[1],
            )],
        )
        with self.assertRaises(TypeError):
            render_persona_catalog([broken])

    def test_streamed_and_nonstreamed_requests_keep_full_prompt_bytes(self):
        expected = f'{SYSTEM_PROMPT}\n\n{MULTI_RENDERED}'
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Next question'),
        ) as complete:
            run_onboarding_turn(
                [], member_message='Hello', persona_catalog=MULTI_CATALOG,
            )
        self.assertEqual(complete.call_args.kwargs['system'], expected)

        def stream(messages, **kwargs):
            self.assertEqual(kwargs['system'], expected)
            yield StreamEvent(kind=STREAM_DONE, result=LLMResult(text='Next question'))

        with patch('questionnaires.onboarding_ai.llm.stream', side_effect=stream):
            list(stream_onboarding_turn(
                [], member_message='Hello', persona_catalog=MULTI_CATALOG,
            ))
