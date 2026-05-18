"""Tests for the shared "Edit in Studio" partial (issue #667).

The partial lives at ``templates/includes/_studio_edit_button.html``. It
must:

- render nothing for non-staff users (server-side gate, not CSS)
- render nothing when ``obj.get_studio_edit_url()`` returns ``None`` /
  empty (defensive: no broken ``href=""``)
- render exactly one ``<a data-testid="studio-edit-button">`` linking to
  ``obj.get_studio_edit_url()`` for staff users
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase, tag

User = get_user_model()

PARTIAL = (
    '{% include "includes/_studio_edit_button.html" with obj=obj %}'
)


def _render(obj, *, is_staff, is_authenticated=True):
    request = RequestFactory().get('/')
    if is_authenticated:
        request.user = SimpleNamespace(
            is_authenticated=True, is_staff=is_staff,
        )
    else:
        request.user = SimpleNamespace(
            is_authenticated=False, is_staff=False,
        )
    return Template(PARTIAL).render(Context({'obj': obj, 'request': request}))


@tag('core')
class StudioEditButtonPartialTest(TestCase):
    """The partial's three branches: staff, non-staff, missing URL."""

    def test_staff_sees_button_with_correct_href(self):
        obj = SimpleNamespace(
            get_studio_edit_url=lambda: '/studio/articles/7/edit',
        )
        html = _render(obj, is_staff=True)
        self.assertIn('data-testid="studio-edit-button"', html)
        self.assertIn('href="/studio/articles/7/edit"', html)
        self.assertIn('aria-label="Edit in Studio"', html)
        # Exactly one link emitted.
        self.assertEqual(html.count('data-testid="studio-edit-button"'), 1)

    def test_anonymous_renders_nothing(self):
        obj = SimpleNamespace(
            get_studio_edit_url=lambda: '/studio/articles/7/edit',
        )
        html = _render(obj, is_staff=False, is_authenticated=False)
        self.assertEqual(html.strip(), '')

    def test_non_staff_authenticated_renders_nothing(self):
        obj = SimpleNamespace(
            get_studio_edit_url=lambda: '/studio/articles/7/edit',
        )
        html = _render(obj, is_staff=False)
        self.assertEqual(html.strip(), '')

    def test_none_url_renders_nothing_for_staff(self):
        """Models that opt out of the button (return None) render nothing."""
        obj = SimpleNamespace(get_studio_edit_url=lambda: None)
        html = _render(obj, is_staff=True)
        self.assertNotIn('data-testid="studio-edit-button"', html)
        self.assertNotIn('href=""', html)

    def test_empty_url_renders_nothing_for_staff(self):
        obj = SimpleNamespace(get_studio_edit_url=lambda: '')
        html = _render(obj, is_staff=True)
        self.assertNotIn('data-testid="studio-edit-button"', html)
        self.assertNotIn('href=""', html)
