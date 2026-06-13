"""Archetype-aware onboarding interview tests (issue #823).

The AI chat must tailor its DELTA questions to the inferred archetype
DURING the conversation (not only repoint at completion), while still
asking the shared ``COMMON_SPINE``. The mechanism is PROMPT-LEVEL: the
system prompt instructs the model to commit to one working archetype
early and prioritise that archetype's deltas, and
``_render_persona_catalog`` now separates the shared spine from each
archetype's deltas so the model can branch on the deltas.

These are the deterministic CI gate: the LLM is mocked at the
``questionnaires.onboarding_ai.llm.complete`` / ``llm.stream`` boundary,
so no live model call is made. The live judge
(``tests/live_judge/test_onboarding_judge.py``) is the separate [HUMAN]
check, not part of CI.
"""

import json
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from integrations.services.llm import (
    STREAM_DONE,
    STREAM_TEXT_DELTA,
    LLMResult,
    StreamEvent,
)
from questionnaires.onboarding_ai import (
    PersonaInfo,
    PersonaQuestion,
    _build_system_prompt,
    _render_persona_catalog,
    run_onboarding_turn,
    stream_onboarding_turn,
)

# The real per-archetype deltas + shared spine, imported from the seed
# migration so the test catalog matches what ships in production. Using
# the real spine is what makes the shared-vs-delta factoring meaningful.
_seed = __import__(
    'questionnaires.migrations.0003_seed_personas_and_onboarding',
    fromlist=['COMMON_SPINE', 'ALEX_DELTAS', 'TAYLOR_DELTAS', 'PRIYA_DELTAS',
              'SAM_DELTAS', 'PERSONAS'],
)
COMMON_SPINE = _seed.COMMON_SPINE
ALEX_DELTAS = _seed.ALEX_DELTAS
TAYLOR_DELTAS = _seed.TAYLOR_DELTAS
PRIYA_DELTAS = _seed.PRIYA_DELTAS
SAM_DELTAS = _seed.SAM_DELTAS

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / 'integrations' / 'services' / 'ai_eval'
    / 'fixtures' / 'onboarding' / 'dataset'
)


def _load_fixture(name):
    with open(FIXTURE_DIR / name, encoding='utf-8') as fh:
        return json.load(fh)


def _catalog_from_fixture(fixture):
    """Convert a fixture's raw ``persona_catalog`` dicts into PersonaInfo."""
    return [
        PersonaInfo(
            signal=p['signal'],
            archetype=p['archetype'],
            description=p.get('description', ''),
            questions=[
                PersonaQuestion(
                    prompt=q['prompt'],
                    question_type=q['question_type'],
                    options=q.get('options', []),
                )
                for q in p.get('questions', [])
            ],
        )
        for p in fixture['persona_catalog']
    ]


def _qs(specs):
    return [
        PersonaQuestion(prompt=prompt, question_type=qtype, options=opts)
        for prompt, qtype, opts in specs
    ]


def _persona(signal, archetype, deltas):
    return PersonaInfo(
        signal=signal,
        archetype=archetype,
        description=f'{archetype} description',
        questions=_qs(COMMON_SPINE + deltas),
    )


# A realistic four-archetype catalog built from the SHIPPED spine + deltas.
FULL_CATALOG = [
    _persona('alex', 'The Engineer transitioning to AI', ALEX_DELTAS),
    _persona('priya', 'The Improver', PRIYA_DELTAS),
    _persona('sam', 'The Technical Professional moving to AI', SAM_DELTAS),
    _persona('taylor', 'The Research-to-Engineering transitioner',
             TAYLOR_DELTAS),
]

# A few representative delta prompts to assert branching on.
ALEX_DELTA_PROMPT = 'Which AI area first?'
ALEX_DELTA_PROMPT_2 = 'Project-first or foundations-first?'
TAYLOR_DELTA_PROMPT = 'Career direction?'
TAYLOR_DELTA_PROMPT_2 = (
    'Which pipeline part for hands-on production experience?'
)
# A shared-spine prompt present for every archetype.
SHARED_PROMPT = (
    'What is the one concrete outcome you want by the end of the next '
    '6 to 8 weeks?'
)
SHARED_PROMPT_HOURS = (
    'How many hours per week can you realistically commit, consistently?'
)


def _capture_system(mock_complete):
    """Return the ``system`` kwarg passed to a mocked ``llm.complete``."""
    assert mock_complete.call_args is not None, 'llm.complete was not called'
    return mock_complete.call_args.kwargs['system']


@tag('core')
class SystemPromptArchetypeCommitTest(SimpleTestCase):
    """The system prompt must instruct early archetype commit + bias."""

    def test_system_prompt_instructs_early_archetype_commit(self):
        prompt = _build_system_prompt(FULL_CATALOG)
        lowered = prompt.lower()
        # Commit-to-one-archetype instruction.
        self.assertIn('commit', lowered)
        self.assertIn('working archetype', lowered)
        # Prioritise that archetype's deltas instruction.
        self.assertIn('delta question', lowered)
        self.assertIn('prioritise', lowered)
        # Still ask the shared spine for everyone.
        self.assertIn('shared spine', lowered)

    def test_catalog_separates_shared_spine_from_deltas(self):
        rendered = _render_persona_catalog(FULL_CATALOG)
        # The shared spine is rendered once under its own labelled block.
        self.assertIn('Shared spine', rendered)
        self.assertIn(SHARED_PROMPT, rendered)
        # The shared prompt appears exactly once (not once per archetype).
        self.assertEqual(rendered.count(SHARED_PROMPT), 1)
        # Each archetype lists its OWN delta block.
        self.assertIn('Delta questions (specific to this archetype)', rendered)
        self.assertIn(ALEX_DELTA_PROMPT, rendered)
        self.assertIn(TAYLOR_DELTA_PROMPT, rendered)


@tag('core')
class ArchetypeBranchingTest(SimpleTestCase):
    """An archetype-strong transcript steers questioning to its deltas."""

    def _run_capture(self, fixture_name):
        fixture = _load_fixture(fixture_name)
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='Got it -- one more question.'),
        ) as mock_complete:
            run_onboarding_turn(
                fixture['transcript'],
                member_message=fixture['member_message'],
                persona_catalog=FULL_CATALOG,
                trace=None,
            )
        return _capture_system(mock_complete)

    def test_alex_strong_transcript_drives_alex_deltas(self):
        system = self._run_capture('persona-alex-strong.json')
        # Alex deltas are present and attributed to the Alex archetype block.
        self.assertIn(ALEX_DELTA_PROMPT, system)
        self.assertIn(ALEX_DELTA_PROMPT_2, system)
        # The model is told to prioritise the committed archetype's deltas
        # and skip the others' -- so Taylor's career/MLOps deltas, while
        # listed under Taylor's block, are clearly delineated as not-Alex.
        alex_block = system.split('[signal: alex]')[1].split('[signal:')[0]
        self.assertIn(ALEX_DELTA_PROMPT, alex_block)
        self.assertNotIn(TAYLOR_DELTA_PROMPT, alex_block)

    def test_taylor_strong_transcript_drives_taylor_deltas(self):
        system = self._run_capture('persona-taylor-strong.json')
        self.assertIn(TAYLOR_DELTA_PROMPT, system)
        self.assertIn(TAYLOR_DELTA_PROMPT_2, system)
        taylor_block = system.split('[signal: taylor]')[1]
        self.assertIn(TAYLOR_DELTA_PROMPT, taylor_block)
        # Alex's deltas live in their own block, not Taylor's.
        self.assertNotIn(ALEX_DELTA_PROMPT, taylor_block)

    def test_alex_and_taylor_runs_emphasise_different_deltas(self):
        alex_system = self._run_capture('persona-alex-strong.json')
        taylor_system = self._run_capture('persona-taylor-strong.json')
        # Both prompts carry the full catalog, but the per-archetype delta
        # blocks are distinct: Alex's deltas are not Taylor's.
        alex_block = (
            alex_system.split('[signal: alex]')[1].split('[signal:')[0]
        )
        taylor_block = taylor_system.split('[signal: taylor]')[1]
        self.assertNotEqual(alex_block, taylor_block)
        self.assertIn(ALEX_DELTA_PROMPT, alex_block)
        self.assertIn(TAYLOR_DELTA_PROMPT, taylor_block)


@tag('core')
class CommonSpineRetainedTest(SimpleTestCase):
    """Shared-spine questions are asked for EVERY archetype."""

    def _system_for(self, fixture_name):
        fixture = _load_fixture(fixture_name)
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='ok'),
        ) as mock_complete:
            run_onboarding_turn(
                fixture['transcript'],
                member_message=fixture['member_message'],
                persona_catalog=FULL_CATALOG,
            )
        return _capture_system(mock_complete)

    def test_common_spine_questions_asked_for_every_archetype(self):
        alex_system = self._system_for('persona-alex-strong.json')
        taylor_system = self._system_for('persona-taylor-strong.json')
        for system in (alex_system, taylor_system):
            self.assertIn('Shared spine', system)
            self.assertIn(SHARED_PROMPT, system)
            self.assertIn(SHARED_PROMPT_HOURS, system)


@tag('core')
class PersonaNameLeakTest(SimpleTestCase):
    """No internal codename reaches the member, even when elicited."""

    def test_no_persona_codename_leaks_when_member_asks(self):
        fixture = _load_fixture('fail-persona-leak.json')
        # The model is goaded into naming a persona; the backstop strips it.
        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(
                text='You are clearly a Priya, and Alex would fit too.',
            ),
        ):
            result = run_onboarding_turn(
                fixture['transcript'],
                member_message=fixture['member_message'],
                persona_catalog=_catalog_from_fixture(fixture),
            )
        for name in ('Alex', 'Priya', 'Sam', 'Taylor'):
            self.assertNotIn(name, result.assistant_message)


@tag('core')
class StreamingPromptParityTest(SimpleTestCase):
    """Streaming and non-streaming build the SAME archetype-aware prompt."""

    def _scripted_stream(self):
        def gen(messages, **kwargs):
            self._stream_system = kwargs.get('system')
            yield StreamEvent(kind=STREAM_TEXT_DELTA, text='ok')
            yield StreamEvent(
                kind=STREAM_DONE, result=LLMResult(text='ok'),
            )
        return gen

    def test_streaming_and_nonstreaming_share_archetype_prompt(self):
        fixture = _load_fixture('persona-alex-strong.json')

        with patch(
            'questionnaires.onboarding_ai.llm.complete',
            return_value=LLMResult(text='ok'),
        ) as mock_complete:
            run_onboarding_turn(
                fixture['transcript'],
                member_message=fixture['member_message'],
                persona_catalog=FULL_CATALOG,
            )
        nonstreaming_system = _capture_system(mock_complete)

        self._stream_system = None
        with patch(
            'questionnaires.onboarding_ai.llm.stream',
            side_effect=self._scripted_stream(),
        ):
            list(stream_onboarding_turn(
                fixture['transcript'],
                member_message=fixture['member_message'],
                persona_catalog=FULL_CATALOG,
            ))

        # Byte-identical system prompt across both transports.
        self.assertEqual(nonstreaming_system, self._stream_system)
        # And it is the archetype-aware prompt (sanity, not a tautology).
        self.assertIn('Delta questions (specific to this archetype)',
                      nonstreaming_system)
