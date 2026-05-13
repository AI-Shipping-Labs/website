from django.template import Context, Template
from django.test import SimpleTestCase


class HeaderActionsPartialTest(SimpleTestCase):
    def test_partial_renders_supplied_header_context_and_action_wrapper(self):
        template = Template(
            """
            {% extends "studio/_partials/header_actions.html" %}
            {% block actions %}
              <a href="/target/" data-testid="example-action">Example action</a>
            {% endblock %}
            """,
        )

        html = template.render(Context({
            'eyebrow': 'Member',
            'title': 'person@example.com',
            'subtitle': 'Active Basic member',
            'back_url': '/studio/users/',
            'back_label': 'Back to users',
            'testid': 'custom-header',
            'actions_testid': 'custom-actions',
        }))

        self.assertIn('data-testid="custom-header"', html)
        self.assertIn('data-testid="custom-actions"', html)
        self.assertIn('Member', html)
        self.assertIn('person@example.com', html)
        self.assertIn('Active Basic member', html)
        self.assertIn('href="/studio/users/"', html)
        self.assertIn('&larr; Back to users', html)
        self.assertIn('Example action', html)

    def test_partial_defaults_testids(self):
        template = Template('{% extends "studio/_partials/header_actions.html" %}')

        html = template.render(Context({'title': 'Default IDs'}))

        self.assertIn('data-testid="studio-header"', html)
        self.assertIn('data-testid="studio-header-actions"', html)
