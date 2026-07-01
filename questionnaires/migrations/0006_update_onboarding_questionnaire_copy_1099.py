"""Update seeded onboarding questionnaire copy and free-text options (#1099)."""

from django.db import migrations


def option(label, free_text=False):
    return {'label': label, 'allows_free_text': free_text}


COMMON_SPINE = [
    {
        'prompt': 'What would you like to have achieved 6 to 8 weeks from now?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
    {
        'prompt': 'Which path best fits that goal?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Ship a new project'),
            option('Improve or finish an existing project'),
            option('Build stronger AI engineering skills'),
            option('Learn foundations before choosing a project'),
            option('Strengthen career or portfolio'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'How many hours per week can you realistically commit?',
        'question_type': 'number',
        'help_text': 'Use an average. It is okay if this is rough.',
        'options': [],
    },
    {
        'prompt': 'What should we know about your availability?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Mostly steady'),
            option('Some weeks will be lighter'),
            option('One intense week, then much less'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What tends to slow you down or make projects stall?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Scope gets too broad'),
            option('Hard to get started'),
            option('Losing momentum'),
            option('Finishing and polishing'),
            option('Technical blockers'),
            option('Too many tools or options'),
            option('Limited time'),
            option('Not enough feedback or accountability'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What kind of accountability would help you make progress?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Weekly check-ins'),
            option('Fixed deliverables'),
            option('Demo milestones'),
            option('Async Slack feedback'),
            option('Pair or partner work'),
            option('Public progress updates'),
            option('Reflection prompts'),
            option('Checklists'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Do you already have a project, idea, or direction in mind?',
        'question_type': 'long_text',
        'help_text': (
            'If not, tell us what kinds of problems, domains, or skills interest you.'
        ),
        'options': [],
    },
    {
        'prompt': 'What stage is your project or idea at?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('No idea yet'),
            option('Idea only'),
            option('Scoped'),
            option('Started'),
            option('Built locally but not deployed'),
            option('Deployed and needs hardening'),
            option('Already production-grade'),
            option('Not applicable'),
        ],
    },
    {
        'prompt': 'What would you like us to help with while preparing your plan?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Scoping'),
            option('Architecture'),
            option('Code review'),
            option('Deployment'),
            option('Evaluation plan'),
            option('Portfolio or README'),
            option('Career positioning'),
            option('Keeping scope practical'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Anything else we should know before preparing your plan?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
]


ALEX_DELTAS = [
    {
        'prompt': (
            'If your plan includes a project, what should it demonstrate to '
            'employers or collaborators?'
        ),
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
    {
        'prompt': 'Which AI area would you most like to focus on first?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('RAG or search'),
            option('Agents and tools'),
            option('Evaluation'),
            option('LLM app architecture'),
            option('Deploying an AI app'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What engineering strengths should the plan build on?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
    {
        'prompt': 'Which learning shape fits you best right now?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Project-first'),
            option('Foundations-first'),
            option('Alternate project and foundations'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What is the biggest risk to your progress?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Information overload'),
            option('Finding the right project idea'),
            option('Finishing and polishing'),
            option('Translating SWE skills into AI work'),
            option('Time'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
]


PRIYA_DELTAS = [
    {
        'prompt': 'What existing AI/ML project, workflow, or codebase could this plan build on?',
        'question_type': 'long_text',
        'help_text': (
            'If you do not have one, say so. We can still build a plan from your goals.'
        ),
        'options': [],
    },
    {
        'prompt': (
            'If you want to improve an existing project, what would make it '
            'more useful, reliable, or ready for real users?'
        ),
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Evaluation'),
            option('Deployment quality'),
            option('Monitoring or observability'),
            option('Tests'),
            option('CI/CD'),
            option('Logging'),
            option('Cost or latency'),
            option('Error handling'),
            option('Documentation'),
            option('Architecture'),
            option('User feedback'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Which AI fundamentals would you like to understand more deeply?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Embeddings'),
            option('RAG internals'),
            option('Evaluation'),
            option('Agents or orchestration'),
            option('Fine-tuning'),
            option('Inference cost and latency'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What tends to pull you off track?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Analysis paralysis'),
            option('Perfectionism'),
            option('Parallel projects'),
            option('Tool comparison'),
            option('No deadlines'),
            option('Waiting for the right approach'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'How should the plan balance theory and shipping?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Mostly project work'),
            option('Theory first, then project work'),
            option('Alternate weekly'),
            option('Decide after the first call'),
            option('Other', True),
        ],
    },
]


SAM_DELTAS = [
    {
        'prompt': 'When you say you want to become more technical, what would be useful in practice?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('Python confidence'),
            option('Project structure'),
            option('APIs'),
            option('Docker'),
            option('Cloud basics'),
            option('Building small apps'),
            option('Deployment'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'How comfortable are you with Python for building software?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('I mostly use notebooks or data analysis scripts'),
            option('Functions are okay, classes are shaky'),
            option('I can build small apps or scripts'),
            option('I am comfortable building services or apps'),
            option('Not applicable'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Which AI tools have you used, and what did you use them for?',
        'question_type': 'long_text',
        'help_text': (
            'For example: ChatGPT, Claude, Gemini, Copilot/Cursor, RAG tools, '
            'agents, automations, or none yet.'
        ),
        'options': [],
    },
    {
        'prompt': 'Which pace feels right for you?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Foundations first'),
            option('Start building now with support'),
            option('Phased: foundations, then build'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'How much do you want to understand what is happening behind the AI tools?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('A practical mental model is enough'),
            option('I mainly need to use the tools effectively'),
            option('I want deeper technical understanding'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
]


TAYLOR_DELTAS = [
    {
        'prompt': 'Which production skill would be most useful to practice hands-on?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Deployment'),
            option('CI/CD'),
            option('Monitoring or observability'),
            option('Evaluation loop'),
            option('Cost or latency'),
            option('End-to-end pipeline'),
            option('Not sure yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What backend or deployment experience do you already have?',
        'question_type': 'multiple_choice',
        'help_text': '',
        'options': [
            option('FastAPI or Flask'),
            option('SQL or NoSQL'),
            option('Docker'),
            option('Cloud'),
            option('CI/CD'),
            option('Monitoring'),
            option('None yet'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Which ML or research strengths should the plan make visible?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
    {
        'prompt': 'Which direction are you exploring?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('AI/ML engineer'),
            option('AI platform or MLOps'),
            option('Applied AI product engineering'),
            option('Research engineer'),
            option('Still deciding'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'Is there any type of AI work you want to avoid or de-emphasize?',
        'question_type': 'long_text',
        'help_text': 'Optional. Leave blank if this does not apply.',
        'options': [],
    },
]


FALLBACK_DELTAS = [
    {
        'prompt': 'How comfortable are you with software engineering?',
        'question_type': 'scale',
        'help_text': '1 = very new, 5 = very comfortable building software.',
        'options': [],
    },
    {
        'prompt': 'How comfortable are you with AI/ML concepts?',
        'question_type': 'scale',
        'help_text': '1 = very new, 5 = very comfortable explaining and applying them.',
        'options': [],
    },
    {
        'prompt': 'Have you built and deployed an AI project end to end?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('No'),
            option('Built locally but not deployed'),
            option('Deployed'),
            option('Not sure'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'What feels like the single biggest gap for you right now?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
    {
        'prompt': 'Which description fits you best right now?',
        'question_type': 'single_choice',
        'help_text': '',
        'options': [
            option('Strong software engineering, need AI direction'),
            option('Shipping AI, need depth or reliability'),
            option('Data or analytics background, need software and AI practice'),
            option('Strong theory or research, need production experience'),
            option('Building a product, need a plan'),
            option('More than one'),
            option('None of these'),
            option('Other', True),
        ],
    },
    {
        'prompt': 'If you could get help with only one thing in the next 2 weeks, what should it be?',
        'question_type': 'long_text',
        'help_text': '',
        'options': [],
    },
]


QUESTIONNAIRES = {
    'onboarding-alex': COMMON_SPINE + ALEX_DELTAS,
    'onboarding-priya': COMMON_SPINE + PRIYA_DELTAS,
    'onboarding-sam': COMMON_SPINE + SAM_DELTAS,
    'onboarding-taylor': COMMON_SPINE + TAYLOR_DELTAS,
    'onboarding-general': COMMON_SPINE + FALLBACK_DELTAS,
}

_SCALE_MIN = 1
_SCALE_MAX = 5


def update_questionnaires(apps, schema_editor):
    Questionnaire = apps.get_model('questionnaires', 'Questionnaire')
    Question = apps.get_model('questionnaires', 'Question')
    QuestionOption = apps.get_model('questionnaires', 'QuestionOption')

    for slug, questions in QUESTIONNAIRES.items():
        questionnaire = Questionnaire.objects.filter(
            slug=slug,
            purpose='onboarding',
        ).first()
        if questionnaire is None:
            continue

        questionnaire.questions.all().delete()
        for order, spec in enumerate(questions):
            question = Question.objects.create(
                questionnaire=questionnaire,
                question_type=spec['question_type'],
                prompt=spec['prompt'],
                help_text=spec['help_text'],
                is_required=False,
                order=order,
                scale_min=(
                    _SCALE_MIN if spec['question_type'] == 'scale' else None
                ),
                scale_max=(
                    _SCALE_MAX if spec['question_type'] == 'scale' else None
                ),
            )
            for option_order, opt in enumerate(spec['options']):
                QuestionOption.objects.create(
                    question=question,
                    label=opt['label'],
                    allows_free_text=opt['allows_free_text'],
                    order=option_order,
                )


def noop_reverse(apps, schema_editor):
    """Do not attempt to restore the old seed copy on reverse migration."""


class Migration(migrations.Migration):

    dependencies = [
        ('questionnaires', '0005_questionoption_allows_free_text_and_more'),
    ]

    operations = [
        migrations.RunPython(update_questionnaires, noop_reverse),
    ]
