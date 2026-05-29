"""Seed the four documented personas + their onboarding questionnaires (#801).

Idempotent: keyed on ``Persona.slug`` and ``Questionnaire.slug`` via
``get_or_create``, so re-running (or running on an environment that
already has the rows) never duplicates data.

The question sets come from the issue's appendix: a common spine shared
by every persona, plus per-persona deltas, plus a generic fallback set
(spine + routing diagnostics) used by #802 for "none / not sure / both".
"""

from django.db import migrations
from django.utils.text import slugify

# (prompt, type, [option labels])
COMMON_SPINE = [
    (
        'What is the one concrete outcome you want by the end of the next '
        '6 to 8 weeks?',
        'long_text',
        [],
    ),
    (
        'Which best describes that outcome?',
        'single_choice',
        [
            'Ship new project',
            'Improve/finish existing',
            'Strengthen eng skills',
            'Build foundations/learn',
            'Career/portfolio',
        ],
    ),
    (
        'How many hours per week can you realistically commit, consistently?',
        'number',
        [],
    ),
    (
        'Will your weekly time be steady, or drop sharply some weeks?',
        'single_choice',
        ['Steady', 'Drops some weeks', 'One high week then much less'],
    ),
    (
        'What usually makes it hard to stay consistent or finish?',
        'multiple_choice',
        [
            'Scoping',
            'Getting started',
            'Momentum',
            'Finishing last 20%',
            'Technical obstacles',
            'FOMO',
            'Not enough time',
            'No feedback',
        ],
    ),
    (
        'What kind of accountability helps you most?',
        'multiple_choice',
        [
            'Weekly check-ins',
            'Fixed deliverables',
            'Demo milestones',
            'Async Slack',
            'Partner pairing',
            'Build-in-public',
            'Reflections',
            'Checklists',
        ],
    ),
    (
        'Do you already have a project or idea, even if rough? Describe it.',
        'long_text',
        [],
    ),
    (
        'What stage is it at?',
        'single_choice',
        [
            'No idea',
            'Idea only',
            'Scoped',
            'Started',
            'Built not deployed',
            'Deployed needs hardening',
        ],
    ),
    (
        'What result would make the next 6-8 weeks worthwhile?',
        'long_text',
        [],
    ),
    (
        'What support from Alexey/community would be most useful now?',
        'multiple_choice',
        [
            'Scoping',
            'Architecture',
            'Code review',
            'Deployment',
            'Eval plan',
            'Portfolio/README',
            'Career advice',
            'Avoid overengineering',
        ],
    ),
    (
        'Anything else we should know before preparing your plan?',
        'long_text',
        [],
    ),
]

ALEX_DELTAS = [
    (
        'If you build one project now, what should it prove to employers?',
        'long_text',
        [],
    ),
    (
        'Which AI area first?',
        'single_choice',
        ['RAG', 'Agents+tools', 'Evaluation', 'LLM architecture',
         'Deploy an AI app', 'Not sure'],
    ),
    (
        'What SWE strengths should the plan build on?',
        'long_text',
        [],
    ),
    (
        'Project-first or foundations-first?',
        'single_choice',
        ['Project-first', 'Foundations-first', 'Alternate'],
    ),
    (
        'Biggest blocker?',
        'single_choice',
        ['Info paralysis', 'Idea generation', 'Finishing',
         'Translating SWE to AI', 'Time'],
    ),
]

PRIYA_DELTAS = [
    (
        'Which existing project to build on + status?',
        'long_text',
        [],
    ),
    (
        'What makes it not production-grade?',
        'multiple_choice',
        ['Evaluation', 'Deploy quality', 'Monitoring', 'Tests', 'CI/CD',
         'Logging', 'Cost', 'Error handling', 'Docs', 'Architecture'],
    ),
    (
        'Which AI fundamentals to understand deeper?',
        'multiple_choice',
        ['Embeddings', 'RAG internals', 'Evaluation',
         'Agents/orchestration', 'Fine-tuning', 'Inference/cost-latency'],
    ),
    (
        'What pulls you off track?',
        'single_choice',
        ['Analysis paralysis', 'Perfectionism', 'Parallel projects',
         'Tool comparison', 'No deadlines'],
    ),
    (
        'Balance theory vs shipping?',
        'single_choice',
        ['Mostly project', 'Lessons-first', 'Alternate weekly'],
    ),
]

SAM_DELTAS = [
    (
        'What does "becoming more technical" mean in practice?',
        'multiple_choice',
        ['Python confidence', 'Project structure', 'APIs', 'Docker',
         'Cloud', 'Small apps', 'Deploy'],
    ),
    (
        'Current Python level?',
        'single_choice',
        ['Data-analysis only', 'Functions yes/classes shaky', 'Small apps',
         'Confident'],
    ),
    (
        'Which AI tools have you used + for what?',
        'multiple_choice',
        ['ChatGPT', 'Claude', 'Gemini', 'Copilot/Cursor', 'RAG tools',
         'Agents', 'Automation/RPA', 'None'],
    ),
    (
        'Gentler foundations-first or build now?',
        'single_choice',
        ['Foundations', 'Build now', 'Phased'],
    ),
    (
        'Relying on AI as a black box — want to understand it?',
        'single_choice',
        ['Yes', 'No'],
    ),
]

TAYLOR_DELTAS = [
    (
        'Which pipeline part for hands-on production experience?',
        'single_choice',
        ['Deployment', 'CI/CD', 'Monitoring', 'Eval loop', 'Cost/latency',
         'End of pipeline'],
    ),
    (
        'Backend/deploy baseline?',
        'multiple_choice',
        ['FastAPI/Flask', 'SQL/NoSQL', 'Docker', 'Cloud', 'CI/CD',
         'Monitoring', 'None'],
    ),
    (
        'ML/research strengths to make visible?',
        'long_text',
        [],
    ),
    (
        'Career direction?',
        'single_choice',
        ['AI/ML Eng', 'AI Platform/MLOps', 'Applied AI', 'Research Eng',
         'Deciding'],
    ),
    (
        'What AI work to avoid based on past experience?',
        'long_text',
        [],
    ),
]

FALLBACK_DELTAS = [
    ('Comfort with software engineering', 'scale', []),
    ('Comfort with AI/ML concepts', 'scale', []),
    (
        'Built and deployed an AI project end to end?',
        'single_choice',
        ['No', 'Local not deployed', 'Deployed'],
    ),
    ('Single biggest gap?', 'long_text', []),
    (
        'Which describes you best?',
        'single_choice',
        ['Eng need AI', 'Ship AI need depth', 'Code for data need both',
         'Theory need production', 'Shipping a product', 'None'],
    ),
    ('If you could only get help with one thing?', 'long_text', []),
]

PERSONAS = [
    {
        'name': 'Alex',
        'slug': 'alex',
        'archetype': 'The Engineer transitioning to AI',
        'description': (
            'Strong engineering, low AI knowledge — needs an AI-specific path'
        ),
        'order': 0,
        'deltas': ALEX_DELTAS,
    },
    {
        'name': 'Priya',
        'slug': 'priya',
        'archetype': 'The Improver (working junior/mid AI/ML engineer)',
        'description': (
            'Strong skills — needs depth, production patterns, and peers'
        ),
        'order': 1,
        'deltas': PRIYA_DELTAS,
    },
    {
        'name': 'Sam',
        'slug': 'sam',
        'archetype': (
            'The Technical Professional moving to AI (analyst/PM with coding)'
        ),
        'description': (
            'Weak engineering (scripts not systems), low AI — longest path'
        ),
        'order': 2,
        'deltas': SAM_DELTAS,
    },
    {
        'name': 'Taylor',
        'slug': 'taylor',
        'archetype': (
            'The Research-to-Engineering transitioner '
            '(researcher/DS/academic)'
        ),
        'description': (
            'Strong theory, weak production engineering/deployment'
        ),
        'order': 3,
        'deltas': TAYLOR_DELTAS,
    },
]

# scale questions get a 1-5 range per the appendix.
_SCALE_MIN = 1
_SCALE_MAX = 5


def _seed_questionnaire(apps, title, slug, description, question_specs):
    """Idempotently create a questionnaire + its base questions.

    Returns the Questionnaire instance. If a questionnaire with ``slug``
    already exists it is returned untouched (no duplicate questions).
    """
    Questionnaire = apps.get_model('questionnaires', 'Questionnaire')
    Question = apps.get_model('questionnaires', 'Question')
    QuestionOption = apps.get_model('questionnaires', 'QuestionOption')

    questionnaire, created = Questionnaire.objects.get_or_create(
        slug=slug,
        defaults={
            'title': title,
            'purpose': 'onboarding',
            'description': description,
            'is_active': True,
        },
    )
    if not created:
        return questionnaire

    for order, (prompt, qtype, options) in enumerate(question_specs):
        kwargs = {
            'questionnaire': questionnaire,
            'question_type': qtype,
            'prompt': prompt,
            'is_required': False,
            'order': order,
        }
        if qtype == 'scale':
            kwargs['scale_min'] = _SCALE_MIN
            kwargs['scale_max'] = _SCALE_MAX
        question = Question.objects.create(**kwargs)
        for opt_order, label in enumerate(options):
            QuestionOption.objects.create(
                question=question,
                label=label,
                order=opt_order,
            )
    return questionnaire


def seed(apps, schema_editor):
    Persona = apps.get_model('questionnaires', 'Persona')

    # Generic fallback questionnaire (#802 uses this for none/both).
    _seed_questionnaire(
        apps,
        title='Onboarding — General (fallback)',
        slug='onboarding-general',
        description=(
            'Generic onboarding set for members who pick "none of these" '
            'or "more than one". Includes routing diagnostics so staff can '
            'infer a persona.'
        ),
        question_specs=COMMON_SPINE + FALLBACK_DELTAS,
    )

    for spec in PERSONAS:
        questionnaire = _seed_questionnaire(
            apps,
            title=f'Onboarding — {spec["name"]}',
            slug=f'onboarding-{spec["slug"]}',
            description=(
                f'Onboarding questionnaire for the {spec["name"]} persona '
                f'({spec["archetype"]}).'
            ),
            question_specs=COMMON_SPINE + spec['deltas'],
        )
        Persona.objects.get_or_create(
            slug=spec['slug'] or slugify(spec['name']),
            defaults={
                'name': spec['name'],
                'archetype': spec['archetype'],
                'description': spec['description'],
                'is_active': True,
                'order': spec['order'],
                'default_questionnaire': questionnaire,
            },
        )


def unseed(apps, schema_editor):
    """Remove only the rows this migration created (best-effort, by slug)."""
    Persona = apps.get_model('questionnaires', 'Persona')
    Questionnaire = apps.get_model('questionnaires', 'Questionnaire')
    persona_slugs = [p['slug'] for p in PERSONAS]
    Persona.objects.filter(slug__in=persona_slugs).delete()
    questionnaire_slugs = (
        [f'onboarding-{s}' for s in persona_slugs] + ['onboarding-general']
    )
    Questionnaire.objects.filter(slug__in=questionnaire_slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('questionnaires', '0002_persona'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
