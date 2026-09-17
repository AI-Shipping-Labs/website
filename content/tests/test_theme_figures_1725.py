"""Light/dark workshop figure pairing (issue #1725).

A workshop figure whose folder also contains a ``<stem>.dark<ext>`` sibling
is rewritten at sync time into two adjacent ``<img>`` tags that Tailwind's
``dark:`` variants swap against the ``.dark`` class the theme toggle puts on
``<html>``. Everything else — screenshots with no sibling, raw HTML images,
every non-workshop family — keeps the single-image output it has today.
"""

import re
from pathlib import Path

from django.test import TestCase, override_settings, tag

from content.models import Workshop, WorkshopPage
from content.sync_parsers.media import (
    _check_broken_image_refs,
    rewrite_image_urls,
)
from content.templatetags.seo_tags import build_seo_description
from content.utils.markdown import (
    _SANITIZE_ATTRIBUTES,
    _SANITIZE_TAGS,
    render_description_html,
)
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

CDN = 'https://cdn.example.com/content-images'
REPO = 'AI-Shipping-Labs/workshops-content'
BASE = '2026/2026-04-21-demo'

LIGHT_CLASS = 'theme-figure block dark:hidden'
DARK_CLASS = 'theme-figure hidden dark:block'

ROOT = Path(__file__).resolve().parents[2]

# A 320x180 SVG: small enough that a reading column never has to shrink it,
# so a stretched-to-100% regression is visible rather than a no-op.
SVG_LIGHT = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">'
    '<rect width="320" height="180" fill="#ffffff"/></svg>'
)
SVG_DARK = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">'
    '<rect width="320" height="180" fill="#0b0f14"/></svg>'
)


@tag('core')
@override_settings(CONTENT_CDN_BASE=CDN)
class ThemeFigurePairingTest(TestCase):
    """``rewrite_image_urls`` emits the swap only when the sibling exists."""

    known = frozenset({
        f'{BASE}/images/cv-pipeline.svg',
        f'{BASE}/images/cv-pipeline.dark.svg',
        f'{BASE}/images/chat.png',
    })

    def _rewrite(self, text, known_images=None):
        return rewrite_image_urls(text, REPO, BASE, known_images=known_images)

    def test_without_known_images_output_is_unchanged_from_today(self):
        # The opt-in kwarg defaults to None, so every non-workshop call site
        # keeps byte-identical output even for a reference that *does* have
        # a dark sibling on disk.
        body = (
            '# Title\n\n![Pipeline](images/cv-pipeline.svg)\n\n'
            '![Chat](images/chat.png)\n'
        )
        expected = (
            '# Title\n\n'
            f'![Pipeline]({CDN}/workshops-content/{BASE}/images/cv-pipeline.svg)'
            '\n\n'
            f'![Chat]({CDN}/workshops-content/{BASE}/images/chat.png)\n'
        )
        self.assertEqual(self._rewrite(body), expected)
        self.assertEqual(rewrite_image_urls(body, REPO, BASE), expected)

    def test_paired_reference_emits_both_variants(self):
        result = self._rewrite(
            '![Pipeline](images/cv-pipeline.svg)', known_images=self.known,
        )
        self.assertEqual(
            result,
            f'<img src="{CDN}/workshops-content/{BASE}/images/cv-pipeline.svg" '
            f'alt="Pipeline" class="{LIGHT_CLASS}" data-theme-figure="light">'
            f'<img src="{CDN}/workshops-content/{BASE}/images/'
            'cv-pipeline.dark.svg" '
            f'alt="Pipeline" class="{DARK_CLASS}" data-theme-figure="dark">',
        )
        # No whitespace between the tags: python-markdown must keep them in
        # one paragraph with no baseline gap text node.
        self.assertNotIn('> <img', result)

    def test_alt_text_cannot_break_out_of_the_attribute(self):
        result = self._rewrite(
            '![The "big" <picture>](images/cv-pipeline.svg)',
            known_images=self.known,
        )
        self.assertEqual(result.count('alt="The &quot;big&quot; '), 2)
        self.assertIn('&lt;picture&gt;"', result)
        self.assertNotIn('<picture>', result)
        # The escaping must not have swallowed the swap attributes.
        self.assertIn('data-theme-figure="light"', result)
        self.assertIn('data-theme-figure="dark"', result)

    def test_reference_without_a_dark_sibling_stays_a_single_image(self):
        result = self._rewrite(
            '![Chat](images/chat.png)', known_images=self.known,
        )
        self.assertEqual(
            result,
            f'![Chat]({CDN}/workshops-content/{BASE}/images/chat.png)',
        )

    def test_reference_already_ending_in_dark_is_never_a_light_base(self):
        # A ``.dark.dark.svg`` exists on purpose: the reserved-name rule has
        # to skip it rather than happen to miss it.
        known = self.known | {
            f'{BASE}/images/cv-pipeline.dark.dark.svg',
        }
        result = self._rewrite(
            '![Pipeline](images/cv-pipeline.dark.svg)', known_images=known,
        )
        self.assertEqual(
            result,
            f'![Pipeline]({CDN}/workshops-content/{BASE}/images/'
            'cv-pipeline.dark.svg)',
        )
        self.assertNotIn('dark.dark', result)

    def test_title_syntax_reference_is_not_paired(self):
        result = self._rewrite(
            '![Pipeline](images/cv-pipeline.svg "The pipeline")',
            known_images=self.known,
        )
        self.assertNotIn('data-theme-figure', result)
        self.assertIn('The pipeline', result)

    def test_authored_raw_html_image_is_not_paired(self):
        result = self._rewrite(
            '<img src="images/cv-pipeline.svg" alt="Pipeline">',
            known_images=self.known,
        )
        self.assertEqual(
            result,
            f'<img src="{CDN}/workshops-content/{BASE}/images/cv-pipeline.svg" '
            'alt="Pipeline">',
        )
        self.assertNotIn('data-theme-figure', result)

    def test_pairing_is_idempotent(self):
        body = (
            '![Pipeline](images/cv-pipeline.svg)\n\n![Chat](images/chat.png)\n'
        )
        once = self._rewrite(body, known_images=self.known)
        twice = self._rewrite(once, known_images=self.known)
        self.assertEqual(once, twice)

    def test_absolute_and_data_urls_are_never_paired(self):
        body = (
            '![Remote](https://example.com/cv-pipeline.svg)\n\n'
            '![Inline](data:image/svg+xml;base64,AAA)\n'
        )
        self.assertEqual(self._rewrite(body, known_images=self.known), body)


@tag('core')
class ThemeFigureBrokenRefTest(TestCase):
    """An unreferenced dark sibling is not a broken reference."""

    def test_unreferenced_dark_sibling_produces_no_error(self):
        errors = []
        known = frozenset({
            f'{BASE}/images/cv-pipeline.svg',
            f'{BASE}/images/cv-pipeline.dark.svg',
        })
        _check_broken_image_refs(
            '![Pipeline](images/cv-pipeline.svg)',
            f'{BASE}/tutorial.md', REPO, BASE, known, errors,
        )
        self.assertEqual(errors, [])

    def test_missing_light_reference_still_reports(self):
        errors = []
        _check_broken_image_refs(
            '![Gone](images/missing.svg)',
            f'{BASE}/tutorial.md', REPO, BASE,
            frozenset({f'{BASE}/images/cv-pipeline.dark.svg'}), errors,
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['step'], 'image_reference_missing')


@override_settings(CONTENT_CDN_BASE=CDN)
class ThemeFigureWorkshopSyncTest(TestCase):
    """A real workshop sync stores the paired markup and renders it."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name=REPO,
            is_private=False,
            prefix='theme-figure-sync-',
        )
        self.repo.write_yaml(f'{BASE}/workshop.yaml', {
            'content_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            'slug': 'demo',
            'title': 'Demo Workshop',
            'date': '2026-04-21',
            'pages_required_level': 10,
        })
        self.repo.write_text(f'{BASE}/images/cv-pipeline.svg', SVG_LIGHT)
        self.repo.write_text(f'{BASE}/images/cv-pipeline.dark.svg', SVG_DARK)
        self.repo.write_text(f'{BASE}/images/chat.png', 'not-a-real-png')
        self.repo.write_text(
            f'{BASE}/README.md',
            '# Demo Workshop\n\n![Pipeline](images/cv-pipeline.svg)\n\n'
            'Learn how to tailor your CV with AI.\n',
        )
        self.repo.write_markdown(
            f'{BASE}/tutorial.md',
            {'title': 'Tutorial'},
            'Body text.\n\n![Pipeline](images/cv-pipeline.svg)\n\n'
            '![Chat](images/chat.png)\n',
            ensure_content_id=False,
        )

    def test_sync_stores_and_renders_the_paired_figure(self):
        sync_repo(self.source, self.repo)

        workshop = Workshop.objects.get(slug='demo')
        self.assertIn('data-theme-figure="light"', workshop.description)
        self.assertIn('data-theme-figure="dark"', workshop.description)
        self.assertIn(
            f'{CDN}/workshops-content/{BASE}/images/cv-pipeline.dark.svg',
            workshop.description,
        )
        self.assertEqual(
            workshop.description_html.count('data-theme-figure='), 2,
        )
        self.assertIn(LIGHT_CLASS, workshop.description_html)
        self.assertIn(DARK_CLASS, workshop.description_html)

        page = WorkshopPage.objects.get(workshop=workshop, slug='tutorial')
        self.assertEqual(page.body_html.count('data-theme-figure='), 2)
        # The unpaired screenshot on the same page stays a single plain image.
        self.assertEqual(page.body_html.count('chat.png'), 1)
        self.assertNotIn('chat.dark.png', page.body_html)

    def test_seo_description_carries_no_markup_from_the_figure(self):
        sync_repo(self.source, self.repo)

        workshop = Workshop.objects.get(slug='demo')
        description = build_seo_description(workshop, 'workshop')
        self.assertIn('Learn how to tailor your CV with AI.', description)
        self.assertNotIn('<img', description)
        self.assertNotIn('theme-figure', description)


@tag('core')
class ThemeFigureSanitizerTest(TestCase):
    """The nh3 allowlist keeps the swap instead of silently stripping it."""

    def test_img_allowlist_carries_the_swap_attributes(self):
        self.assertEqual(
            _SANITIZE_ATTRIBUTES['img'],
            {'src', 'alt', 'title', 'class', 'data-theme-figure'},
        )

    def test_rendered_description_preserves_the_swap(self):
        html = render_description_html(
            f'<img src="https://cdn.example.com/a.svg" alt="Pipeline" '
            f'class="{LIGHT_CLASS}" data-theme-figure="light">'
            f'<img src="https://cdn.example.com/a.dark.svg" alt="Pipeline" '
            f'class="{DARK_CLASS}" data-theme-figure="dark">',
        )
        self.assertIn(LIGHT_CLASS, html)
        self.assertIn(DARK_CLASS, html)
        self.assertIn('data-theme-figure="light"', html)
        self.assertIn('data-theme-figure="dark"', html)

    def test_inline_svg_is_still_stripped(self):
        self.assertNotIn('svg', _SANITIZE_TAGS)
        html = render_description_html('<svg><rect width="10"/></svg>')
        self.assertNotIn('<svg', html)


@tag('core')
class ThemeFigureStylesheetTest(TestCase):
    """The figure keeps its authored size, and the utilities own `display`."""

    def test_source_rule_overrides_the_prose_image_defaults(self):
        css = (ROOT / 'assets/css/tailwind.css').read_text()
        match = re.search(
            r'\.prose img\.theme-figure\s*\{([^}]*)\}', css,
        )
        self.assertIsNotNone(match, '.prose img.theme-figure rule is missing')
        rule = match.group(1)
        self.assertIn('width: auto', rule)
        self.assertIn('max-width: 100%', rule)
        self.assertIn('border: none', rule)
        # `display` belongs to the block/hidden/dark: utilities; declaring it
        # here would outrank them and freeze the figure in one theme.
        self.assertNotIn('display', rule)
