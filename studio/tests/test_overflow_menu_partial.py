from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


class OverflowMenuBlockTagTest(SimpleTestCase):
    def test_block_tag_renders_shell_and_unescaped_items_once(self):
        html = Template(
            """
            {% load studio_filters %}
            {% studio_overflow_menu %}
              <a href="/admin/example/" data-testid="rare-link">Open in Django admin</a>
              <form method="post" data-testid="guarded-form">
                {% csrf_token %}<button type="submit">Archive</button>
              </form>
              <div class="border-t border-border">
                <button class="text-red-400 hover:bg-red-500/10" onclick="return confirm('Delete?')">Delete</button>
              </div>
            {% endstudio_overflow_menu %}
            """
        ).render(Context({'csrf_token': 'test-token'}))

        self.assertEqual(html.count('data-studio-overflow'), 1)
        self.assertEqual(html.count('data-testid="studio-header-overflow"'), 1)
        self.assertEqual(html.count('data-testid="rare-link"'), 1)
        self.assertEqual(html.count('data-testid="guarded-form"'), 1)
        self.assertNotIn('&lt;a href=', html)
        self.assertNotIn('&lt;form', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('aria-label="More actions"', html)
        self.assertIn('h-[38px] w-[38px]', html)
        self.assertIn('absolute left-0', html)
        self.assertIn('w-64', html)
        self.assertIn('focus-visible:ring-offset-background', html)

    def test_partial_keeps_canonical_item_shape_comments(self):
        source = (
            REPO_ROOT / 'templates/studio/_partials/overflow_menu.html'
        ).read_text()

        self.assertIn('Canonical link item', source)
        self.assertIn('min-h-[44px]', source)
        self.assertIn('Canonical POST item', source)
        self.assertIn('method="post"', source)
        self.assertIn('CSRF', source)
        self.assertIn('Canonical destructive item', source)
        self.assertIn('border-t border-border', source)
        self.assertIn('text-red-400 hover:bg-red-500/10', source)
        self.assertIn('confirm() guard', source)


class OverflowMenuDismissalScriptTest(SimpleTestCase):
    def test_base_template_has_one_delegated_outside_click_listener(self):
        source = (REPO_ROOT / 'templates/studio/base.html').read_text()
        selector = "details[data-studio-overflow][open]"

        self.assertEqual(source.count(selector), 1)
        self.assertIn("document.addEventListener('click', function(event)", source)
        self.assertIn('if (!menu.contains(event.target))', source)
        self.assertIn("menu.removeAttribute('open')", source)
