"""Tests for the shared "Open in Studio" pill partial (issue #667).

The partial lives at ``templates/includes/_studio_edit_button.html``. The
header resolves the staff-only URL (so the staff gate is covered by the
per-page view tests) and passes it in as ``studio_edit_url``. The partial
must:

- render exactly one ``<a data-testid="studio-edit-button">`` labeled
  Open in Studio when given a URL
- render nothing when no URL resolved (no broken ``href=""``)
"""

from django.template import Context, Template
from django.test import SimpleTestCase, tag

PARTIAL = (
    '{% include "includes/_studio_edit_button.html" '
    'with studio_edit_url=url %}'
)


def _render(url):
    return Template(PARTIAL).render(Context({'url': url}))


@tag('core')
class StudioEditButtonPartialTest(SimpleTestCase):
    def test_url_renders_one_labelled_link(self):
        html = _render('/studio/articles/7/edit')
        self.assertEqual(html.count('data-testid="studio-edit-button"'), 1)
        self.assertIn('href="/studio/articles/7/edit"', html)
        self.assertIn('aria-label="Open in Studio"', html)
        self.assertIn('>Open in Studio</span>', html)
        self.assertIn('data-lucide="external-link"', html)
        self.assertNotIn('Edit in Studio', html)

    def test_missing_url_renders_nothing(self):
        for url in (None, ''):
            with self.subTest(url=url):
                html = _render(url)
                self.assertNotIn('studio-edit-button', html)
                self.assertNotIn('href=""', html)
