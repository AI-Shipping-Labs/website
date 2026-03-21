import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from content.models import LearningPath


def _create_temp_learning_path(base_dir):
    """Create a temporary content repo with learning path data."""
    lp_dir = base_dir / 'learning-path' / 'ai-engineer'
    lp_dir.mkdir(parents=True, exist_ok=True)

    (lp_dir / 'data.yaml').write_text(
        'title: "AI Engineer Learning Path"\n'
        'description: "A visual learning path for AI engineers."\n'
        '\n'
        'skill_categories:\n'
        '  - id: genai\n'
        '    label: "GenAI Skills"\n'
        '    description: "Core AI skills."\n'
        '    skills:\n'
        '      - name: "RAG"\n'
        '        pct: 35.9\n'
        '        priority: essential\n'
        '      - name: "Prompt Engineering"\n'
        '        pct: 29.1\n'
        '        priority: essential\n'
        '      - name: "Fine-Tuning"\n'
        '        pct: 8.5\n'
        '        priority: nice-to-have\n'
        '\n'
        'tool_categories:\n'
        '  - label: "GenAI Frameworks"\n'
        '    note: "No single framework dominates."\n'
        '    tools:\n'
        '      - name: "LangChain"\n'
        '        pct: 18.8\n'
        '      - name: "LlamaIndex"\n'
        '        pct: 5.8\n'
        '\n'
        'responsibilities:\n'
        '  core:\n'
        '    - title: "Build AI Systems"\n'
        '      description: "Design end-to-end LLM applications."\n'
        '  common:\n'
        '    - title: "RAG & Retrieval"\n'
        '      description: "Build retrieval systems."\n'
        '  secondary:\n'
        '    - "Frontend / UI development"\n'
        '    - "Fine-tuning models"\n'
        '\n'
        'portfolio_projects:\n'
        '  - number: "01"\n'
        '    title: "Production RAG System"\n'
        '    description: "Build a production-ready RAG system."\n'
        '    skills: ["RAG", "Vector DB", "Python"]\n'
        '    difficulty: "Foundational"\n'
        '  - number: "02"\n'
        '    title: "Multi-Step AI Agent"\n'
        '    description: "Build an agent that automates a workflow."\n'
        '    skills: ["Agents", "LLM APIs"]\n'
        '    difficulty: "Intermediate"\n'
        '\n'
        'learning_stages:\n'
        '  - stage: "1"\n'
        '    title: "Python & LLM Foundations"\n'
        '    items:\n'
        '      - "Python fluency"\n'
        '      - "How LLMs work"\n'
        '  - stage: "2"\n'
        '    title: "RAG & Retrieval Systems"\n'
        '    items:\n'
        '      - "Embeddings and semantic search"\n'
        '      - "Vector databases"\n'
    )

    return base_dir


class LearningPathViewTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.content_dir = Path(self.temp_dir)
        _create_temp_learning_path(self.content_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings()
    def test_learning_path_returns_200(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response.status_code, 200)

    @override_settings()
    def test_learning_path_uses_correct_template(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        self.assertTemplateUsed(response, 'content/learning_path_ai_engineer.html')

    @override_settings()
    def test_learning_path_shows_title(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('AI Engineer Learning Path', content)

    @override_settings()
    def test_learning_path_shows_skill_categories(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('GenAI Skills', content)
        self.assertIn('RAG', content)
        self.assertIn('Prompt Engineering', content)
        self.assertIn('35.9%', content)

    @override_settings()
    def test_learning_path_shows_priority_labels(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('essential', content)
        self.assertIn('nice-to-have', content)

    @override_settings()
    def test_learning_path_shows_tool_categories(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('GenAI Frameworks', content)
        self.assertIn('LangChain', content)
        self.assertIn('LlamaIndex', content)

    @override_settings()
    def test_learning_path_shows_responsibilities(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('Build AI Systems', content)
        self.assertIn('RAG &amp; Retrieval', content)
        self.assertIn('Frontend / UI development', content)

    @override_settings()
    def test_learning_path_shows_portfolio_projects(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('Production RAG System', content)
        self.assertIn('Multi-Step AI Agent', content)
        self.assertIn('Foundational', content)
        self.assertIn('Intermediate', content)

    @override_settings()
    def test_learning_path_shows_learning_stages(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('Python &amp; LLM Foundations', content)
        self.assertIn('RAG &amp; Retrieval Systems', content)
        self.assertIn('Python fluency', content)
        self.assertIn('Embeddings and semantic search', content)

    @override_settings()
    def test_learning_path_404_when_no_content_repo(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path('/nonexistent/path')

        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response.status_code, 404)

    @override_settings()
    def test_learning_path_context_data(self):
        from django.conf import settings
        settings.CONTENT_REPO_DIR = Path(self.temp_dir)

        response = self.client.get('/learning-path/ai-engineer')
        self.assertIn('skill_categories', response.context)
        self.assertIn('tool_categories', response.context)
        self.assertIn('responsibilities', response.context)
        self.assertIn('portfolio_projects', response.context)
        self.assertIn('learning_stages', response.context)
        self.assertEqual(len(response.context['skill_categories']), 1)
        self.assertEqual(len(response.context['tool_categories']), 1)
        self.assertEqual(len(response.context['portfolio_projects']), 2)
        self.assertEqual(len(response.context['learning_stages']), 2)


class LearningPathDbViewTest(TestCase):
    """Test learning path reads from the database when data is available."""

    def setUp(self):
        LearningPath.objects.create(
            slug='ai-engineer',
            title='AI Engineer Learning Path',
            description='A visual learning path for AI engineers.',
            data_json={
                'title': 'AI Engineer Learning Path',
                'description': 'A visual learning path for AI engineers.',
                'skill_categories': [
                    {
                        'id': 'genai',
                        'label': 'GenAI Skills',
                        'description': 'Core AI skills.',
                        'skills': [
                            {'name': 'RAG', 'pct': 35.9, 'priority': 'essential'},
                            {'name': 'Fine-Tuning', 'pct': 8.5, 'priority': 'nice-to-have'},
                        ],
                    },
                ],
                'tool_categories': [
                    {
                        'label': 'GenAI Frameworks',
                        'note': 'No single framework dominates.',
                        'tools': [
                            {'name': 'LangChain', 'pct': 18.8},
                        ],
                    },
                ],
                'responsibilities': {
                    'core': [{'title': 'Build AI Systems', 'description': 'Design LLM apps.'}],
                    'common': [{'title': 'RAG & Retrieval', 'description': 'Build retrieval.'}],
                    'secondary': ['Frontend / UI development'],
                },
                'portfolio_projects': [
                    {
                        'number': '01',
                        'title': 'Production RAG System',
                        'description': 'Build a RAG system.',
                        'skills': ['RAG'],
                        'difficulty': 'Foundational',
                    },
                ],
                'learning_stages': [
                    {
                        'stage': '1',
                        'title': 'Python & LLM Foundations',
                        'items': ['Python fluency'],
                    },
                ],
            },
        )

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_returns_200_from_db(self):
        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response.status_code, 200)

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_uses_correct_template_from_db(self):
        response = self.client.get('/learning-path/ai-engineer')
        self.assertTemplateUsed(response, 'content/learning_path_ai_engineer.html')

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_shows_title_from_db(self):
        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('AI Engineer Learning Path', content)

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_shows_skills_from_db(self):
        response = self.client.get('/learning-path/ai-engineer')
        content = response.content.decode()
        self.assertIn('GenAI Skills', content)
        self.assertIn('RAG', content)
        self.assertIn('35.9%', content)

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_context_from_db(self):
        response = self.client.get('/learning-path/ai-engineer')
        self.assertIn('skill_categories', response.context)
        self.assertIn('tool_categories', response.context)
        self.assertIn('portfolio_projects', response.context)
        self.assertIn('learning_stages', response.context)

    @override_settings(CONTENT_REPO_DIR=None)
    def test_learning_path_404_when_no_db_and_no_disk(self):
        LearningPath.objects.all().delete()
        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response.status_code, 404)
