"""Tests for issue #518 — the GitHub icon must render server-side as an
inline ``<svg>`` instead of an unhydrated ``<i data-lucide="github">``
placeholder.

Recent upstream Lucide releases dropped brand glyphs from the core
package (the ``unpkg.com/lucide@latest`` URL currently resolves to
``lucide@1.14.0``). When ``lucide.createIcons()`` ran on workshop pages
it left ``<i data-lucide="github" class="h-4 w-4">`` untouched, so the
icon slot still consumed ``1rem × 1rem`` plus the parent's ``gap-2``,
producing the phantom left padding the user reported.

We fixed the four affected templates by replacing the ``<i>`` placeholder
with an inline SVG via ``includes/_icon_github.html``. These tests guard
against regression by asserting:

- Each affected page's GitHub button now serves an ``<svg>`` directly in
  the HTML (no JavaScript needed to hydrate it).
- The unhydrated ``<i data-lucide="github">`` marker is gone from those
  buttons so the empty-slot bug cannot come back.
- Sibling Lucide icon placeholders that are still hydrated client-side
  (e.g. ``external-link``, ``book-open``) are untouched — the fix is
  scoped to the GitHub brand glyph.
- The inline SVG uses ``currentColor`` so it inherits the foreground in
  both light and dark themes without needing extra ``dark:`` variants.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Workshop, WorkshopPage
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_workshop(
    slug='ws',
    title='Production Agents',
    code_repo_url='https://github.com/example/repo',
    landing=0,
    pages=0,
    recording=0,
    with_event=False,
):
    event = None
    if with_event:
        event = Event.objects.create(
            slug=f'{slug}-event',
            title=title,
            kind='workshop',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://www.youtube.com/watch?v=abc',
            published=True,
        )
    return Workshop.objects.create(
        slug=slug,
        title=title,
        status='published',
        date=date(2026, 4, 21),
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description='# Hello\n\nDescription.',
        code_repo_url=code_repo_url,
        cover_image_url='',
        tags=['agents'],
        event=event,
    )


def _isolate_repo_button(html, testid):
    """Return the substring of ``html`` between the data-testid anchor and
    the closing ``</a>`` so an icon assertion can't be satisfied by some
    unrelated element elsewhere on the page."""
    marker = f'data-testid="{testid}"'
    start = html.find(marker)
    if start == -1:
        return ''
    end = html.find('</a>', start)
    return html[start:end]


class WorkshopDetailGitHubIconTest(TierSetupMixin, TestCase):
    """The public workshop landing page must render the GitHub icon as an
    inline ``<svg>`` server-side."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop()

    def test_github_button_renders_inline_svg_not_lucide_placeholder(self):
        response = self.client.get('/workshops/ws')
        self.assertEqual(response.status_code, 200)
        button = _isolate_repo_button(
            response.content.decode(), 'workshop-code-repo-link',
        )
        # The button's icon is an SVG, not the unhydrated <i> placeholder.
        self.assertIn('<svg', button)
        self.assertIn('data-icon="github"', button)
        self.assertNotIn('data-lucide="github"', button)

    def test_github_icon_uses_currentcolor_for_theme_inheritance(self):
        response = self.client.get('/workshops/ws')
        button = _isolate_repo_button(
            response.content.decode(), 'workshop-code-repo-link',
        )
        # The icon stroke is currentColor so it inherits the foreground
        # in both light and dark mode without needing a dark: variant.
        self.assertIn('stroke="currentColor"', button)

    def test_button_label_unchanged(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'View code on GitHub')

    def test_sibling_lucide_icons_still_use_placeholder(self):
        """The fix is scoped to the GitHub glyph. The trailing
        ``external-link`` icon next to the button text still uses Lucide
        client-side hydration, so its placeholder must remain."""
        response = self.client.get('/workshops/ws')
        button = _isolate_repo_button(
            response.content.decode(), 'workshop-code-repo-link',
        )
        self.assertIn('data-lucide="external-link"', button)


class WorkshopReaderSidebarGitHubIconTest(TierSetupMixin, TestCase):
    """The reader-page sidebar's "View code" button must also render the
    icon server-side (issue #518)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='reader-ws',
            landing=0,
            pages=0,
            recording=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='# Hello',
        )

    def test_sidebar_github_button_renders_inline_svg(self):
        response = self.client.get('/workshops/reader-ws/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        button = _isolate_repo_button(
            response.content.decode(), 'sidebar-code-repo-link',
        )
        self.assertIn('<svg', button)
        self.assertIn('data-icon="github"', button)
        self.assertNotIn('data-lucide="github"', button)


class StudioWorkshopDetailGitHubIconTest(TestCase):
    """The Studio workshop detail page's "Code repo" link must render
    the GitHub icon as an inline SVG (issue #518)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        cls.workshop = Workshop.objects.create(
            slug='studio-ws',
            title='Studio Workshop',
            status='published',
            date=date(2026, 4, 21),
            description='Body.',
            code_repo_url='https://github.com/example/code',
            tags=['agents'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_code_repo_link_renders_inline_svg(self):
        response = self.client.get(
            f'/studio/workshops/{self.workshop.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Find the Code repo block and isolate it.
        start = body.find('Code repo')
        self.assertGreater(start, -1)
        end = body.find('</a>', start)
        block = body[start:end]
        self.assertIn('<svg', block)
        self.assertIn('data-icon="github"', block)
        # No phantom Lucide placeholder remains.
        self.assertNotIn('data-lucide="github"', block)


class StudioStickyActionBarGitHubIconTest(TestCase):
    """The Studio sticky action bar's "Edit on GitHub" button must render
    the icon as an inline SVG when ``github_edit_url`` is set."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_sticky_bar_renders_inline_svg(self):
        """Render the sticky action bar partial directly with a context
        that populates ``github_edit_url`` so the Edit-on-GitHub button
        is emitted."""
        from django.template.loader import render_to_string

        html = render_to_string(
            'studio/includes/sticky_action_bar.html',
            {
                'title': 'Edit Article',
                'subtitle': 'Sync source',
                'can_save': False,
                'github_edit_url': (
                    'https://github.com/example/repo/edit/main/foo.md'
                ),
                'obj': None,
                'form_id': 'form-id',
                'cancel_url': '/studio/',
                'primary_label': 'Save',
            },
        )
        # Isolate the GitHub anchor.
        start = html.find('data-testid="sticky-github-source-link"')
        self.assertGreater(start, -1)
        end = html.find('</a>', start)
        block = html[start:end]
        self.assertIn('<svg', block)
        self.assertIn('data-icon="github"', block)
        self.assertNotIn('data-lucide="github"', block)


class GitHubIconPartialTest(TestCase):
    """The shared icon partial passes the ``css`` argument through to the
    SVG class so every caller controls size and alignment exactly like
    they did with the Lucide ``<i>`` element."""

    def test_partial_applies_custom_css_classes(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            'includes/_icon_github.html',
            {'css': 'mr-2 h-4 w-4'},
        )
        self.assertIn('class="mr-2 h-4 w-4"', html)
        self.assertIn('<svg', html)
        self.assertIn('stroke="currentColor"', html)

    def test_partial_default_css_is_h4_w4(self):
        """When no ``css`` is passed, the SVG falls back to ``h-4 w-4``
        so callers that forget the ``with`` are still sized."""
        from django.template.loader import render_to_string

        html = render_to_string('includes/_icon_github.html', {})
        self.assertIn('class="h-4 w-4"', html)

    def test_partial_marks_icon_as_decorative(self):
        """``aria-hidden="true"`` so screen readers skip it; the
        surrounding link text already labels the action."""
        from django.template.loader import render_to_string

        html = render_to_string('includes/_icon_github.html', {})
        self.assertIn('aria-hidden="true"', html)
