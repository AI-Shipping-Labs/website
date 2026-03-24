"""Tests for the widget expansion system."""

from django.template import TemplateDoesNotExist
from django.test import TestCase

from content.utils.widgets import expand_widgets


class ExpandWidgetsTest(TestCase):
    """Test the expand_widgets() utility function."""

    def test_replaces_single_widget_marker(self):
        html = '<h2>Stages</h2>\n<!-- widget:learning_stages data=stages -->'
        data = {
            'stages': [
                {'stage': '1', 'title': 'Foundations', 'items': ['Python', 'Math']},
            ],
        }
        result = expand_widgets(html, data)
        self.assertNotIn('<!-- widget:', result)
        self.assertIn('Foundations', result)
        self.assertIn('Python', result)

    def test_replaces_multiple_widget_markers(self):
        html = (
            '<!-- widget:learning_stages data=stages -->\n'
            '<!-- widget:skill_chart data=skills -->'
        )
        data = {
            'stages': [
                {'stage': '1', 'title': 'Stage One', 'items': ['Item 1']},
            ],
            'skills': [
                {
                    'label': 'GenAI',
                    'description': 'Core skills',
                    'skills': [
                        {'name': 'RAG', 'pct': 35.9, 'priority': 'essential'},
                    ],
                },
            ],
        }
        result = expand_widgets(html, data)
        self.assertNotIn('<!-- widget:', result)
        self.assertIn('Stage One', result)
        self.assertIn('GenAI', result)
        self.assertIn('RAG', result)

    def test_returns_html_unchanged_when_no_markers(self):
        html = '<h1>Hello</h1><p>World</p>'
        result = expand_widgets(html, {})
        self.assertEqual(result, html)

    def test_raises_key_error_for_missing_data_key(self):
        html = '<!-- widget:skill_chart data=missing_key -->'
        data = {'other_key': []}
        with self.assertRaises(KeyError) as ctx:
            expand_widgets(html, data)
        self.assertIn('missing_key', str(ctx.exception))
        self.assertIn('other_key', str(ctx.exception))

    def test_raises_template_does_not_exist_for_missing_template(self):
        html = '<!-- widget:nonexistent_widget data=items -->'
        data = {'items': []}
        with self.assertRaises(TemplateDoesNotExist):
            expand_widgets(html, data)

    def test_responsibilities_widget_renders_dict_data(self):
        html = '<!-- widget:responsibilities data=resp -->'
        data = {
            'resp': {
                'core': [{'title': 'Build AI', 'description': 'Design systems.'}],
                'common': [{'title': 'RAG', 'description': 'Build retrieval.'}],
                'secondary': ['Frontend dev'],
            },
        }
        result = expand_widgets(html, data)
        self.assertIn('Build AI', result)
        self.assertIn('Design systems.', result)
        self.assertIn('RAG', result)
        self.assertIn('Frontend dev', result)

    def test_project_grid_widget_renders(self):
        html = '<!-- widget:project_grid data=projects -->'
        data = {
            'projects': [
                {
                    'number': '01',
                    'title': 'RAG System',
                    'description': 'Build a RAG.',
                    'skills': ['Python', 'Docker'],
                    'difficulty': 'Foundational',
                },
            ],
        }
        result = expand_widgets(html, data)
        self.assertIn('RAG System', result)
        self.assertIn('Build a RAG.', result)
        self.assertIn('Foundational', result)
        self.assertIn('Python', result)

    def test_tool_chart_widget_renders(self):
        html = '<!-- widget:tool_chart data=tools -->'
        data = {
            'tools': [
                {
                    'label': 'GenAI Frameworks',
                    'note': 'No single framework dominates.',
                    'tools': [
                        {'name': 'LangChain', 'pct': 18.8},
                    ],
                },
            ],
        }
        result = expand_widgets(html, data)
        self.assertIn('GenAI Frameworks', result)
        self.assertIn('LangChain', result)
        self.assertIn('18.8%', result)
