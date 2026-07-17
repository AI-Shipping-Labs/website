from django.template import Context, Template
from django.test import SimpleTestCase


class HeaderActionsBlockTagTest(SimpleTestCase):
    def render(self, body, context=None):
        template = Template('{% load studio_filters %}' + body)
        return template.render(Context(context or {}))

    def test_block_tag_renders_context_and_unescaped_action_once(self):
        html = self.render(
            """
            {% studio_header_actions title=title eyebrow=eyebrow subtitle=subtitle back_url=back_url back_label=back_label testid='custom-header' actions_testid='custom-actions' %}
              <a href="/target/" data-testid="example-action">Example action</a>
            {% endstudio_header_actions %}
            """,
            {
                'eyebrow': 'Member',
                'title': 'person@example.com',
                'subtitle': 'Active Basic member',
                'back_url': '/studio/users/',
                'back_label': 'Back to users',
            },
        )

        self.assertIn('data-testid="custom-header"', html)
        self.assertIn('data-testid="custom-actions"', html)
        self.assertIn('Member', html)
        self.assertIn('person@example.com', html)
        self.assertIn('Active Basic member', html)
        self.assertIn('href="/studio/users/"', html)
        self.assertIn('&larr; Back to users', html)
        self.assertEqual(html.count('data-testid="example-action"'), 1)
        self.assertNotIn('&lt;a href=', html)
        self.assertLess(html.index('person@example.com'), html.index('Example action'))

    def test_block_tag_defaults_testids(self):
        html = self.render(
            """
            {% studio_header_actions title='Default IDs' %}
              <button type="button">Action</button>
            {% endstudio_header_actions %}
            """
        )

        self.assertIn('data-testid="studio-header"', html)
        self.assertIn('data-testid="studio-header-actions"', html)
        self.assertIn('class="relative flex flex-wrap items-center gap-2"', html)

    def test_empty_and_whitespace_only_blocks_omit_action_row(self):
        for content in ('', '   \n  '):
            with self.subTest(content=repr(content)):
                html = self.render(
                    '{% studio_header_actions title="No actions" %}'
                    + content
                    + '{% endstudio_header_actions %}'
                )
                self.assertIn('data-testid="studio-header"', html)
                self.assertNotIn('studio-header-actions', html)
                self.assertNotIn('flex flex-wrap items-center gap-2', html)

    def test_template_authored_title_meta_renders_once_and_omits_empty_wrapper(self):
        html = self.render(
            """
            {% studio_header_title_meta as title_meta %}
              <span data-testid="pending-meta">3 pending review</span>
            {% endstudio_header_title_meta %}
            {% studio_header_actions title='Projects' title_meta=title_meta %}
            {% endstudio_header_actions %}
            """
        )

        self.assertEqual(html.count('data-testid="pending-meta"'), 1)
        self.assertIn('data-testid="studio-header-meta"', html)
        self.assertNotIn('&lt;span', html)

        empty_html = self.render(
            """
            {% studio_header_title_meta as title_meta %}   {% endstudio_header_title_meta %}
            {% studio_header_actions title='Projects' title_meta=title_meta %}
            {% endstudio_header_actions %}
            """
        )
        self.assertNotIn('studio-header-meta', empty_html)

        escaped_html = self.render(
            """
            {% studio_header_title_meta as title_meta %}<span>{{ untrusted }}</span>{% endstudio_header_title_meta %}
            {% studio_header_actions title='Safe metadata' title_meta=title_meta %}{% endstudio_header_actions %}
            """,
            {'untrusted': '<script>alert(1)</script>'},
        )
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', escaped_html)
        self.assertNotIn('<script>alert(1)</script>', escaped_html)

    def test_partial_uses_stacked_contract_without_forbidden_classes(self):
        html = self.render(
            """
            {% studio_header_actions title='Stacked' %}
              <button type="button">Action</button>
            {% endstudio_header_actions %}
            """
        )

        self.assertIn('<header class="mb-8 space-y-4"', html)
        self.assertIn(
            'text-2xl font-semibold tracking-tight text-foreground mt-1 break-all',
            html,
        )
        for forbidden in (
            'justify-between',
            'sm:flex-row',
            'sm:justify-end',
            'shrink-0',
            'space-x-',
        ):
            self.assertNotIn(forbidden, html)

    def test_back_link_and_eyebrow_use_canonical_focus_and_typography(self):
        html = self.render(
            """
            {% studio_header_actions title='Canonical header' eyebrow='Member' back_url='/studio/users/' back_label='Back to users' %}
            {% endstudio_header_actions %}
            """
        )
        focus_visible = (
            'focus-visible:outline-none focus-visible:ring-2 '
            'focus-visible:ring-accent focus-visible:ring-offset-2 '
            'focus-visible:ring-offset-background'
        )

        self.assertIn(
            'hover:text-foreground transition-colors ' + focus_visible,
            html,
        )
        self.assertIn(
            'text-xs font-medium uppercase tracking-widest text-muted-foreground',
            html,
        )
        self.assertNotIn('tracking-wide text-muted-foreground', html)
