"""Playwright E2E for theme-paired workshop figures (issue #1725).

A workshop figure with a ``<stem>.dark<ext>`` sibling is emitted at sync
time as two adjacent ``<img>`` tags that Tailwind's ``dark:`` variants swap
against the ``.dark`` class on ``<html>``. These tests drive the real theme
toggle and assert the swap is instant, reload-free, correct on first paint,
and never touches an unpaired screenshot.

Scenarios:

1. Dark-mode first load shows the dark figure, not the white slab.
2. Light-mode first load shows the light figure.
3. Toggling to dark swaps the figure with no page reload.
4. Toggling back to light restores it and the choice survives a reload.
5. A tutorial page's figure swaps in place without jumping the scroll.
6. A screenshot with no dark sibling is untouched by the toggle.
7. A page mixing a paired figure and a screenshot is correct in both themes.
8. The figure renders at its authored size, unbordered, not column-stretched.
9. A cold dark load never puts the light figure in a rendered frame.
10. An operator re-sync turns a stored single-image workshop into a pair.

Test bodies are produced by the production emitter
(``content.sync_parsers.media.rewrite_image_urls``) with a fake
``known_images`` set, so this file cannot drift from the unit tests. Both
variants are served locally through request interception with real intrinsic
dimensions, because a failed image collapses to a zero-size box and would
make ``is_visible()`` lie.

Usage:
    uv run pytest playwright_tests/test_workshop_theme_figures_1725.py -v
"""

import datetime
import os
import shutil
import struct
import tempfile
import zlib
from functools import lru_cache

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.test_theme_toggle import _click_visible_theme_toggle
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.db import connection  # noqa: E402
from django.test import override_settings  # noqa: E402

# Issue #656: this module seeds workshops directly and syncs a local repo
# in-process, so it cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only

CDN = 'https://cdn.figures.invalid/content-images'
REPO = 'AI-Shipping-Labs/workshops-content'
BASE = '2026/2026-04-21-figures'

FIGURE_WIDTH = 320
FIGURE_HEIGHT = 180
SCREENSHOT_WIDTH = 400
SCREENSHOT_HEIGHT = 120

LIGHT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    f'width="{FIGURE_WIDTH}" height="{FIGURE_HEIGHT}">'
    f'<rect width="{FIGURE_WIDTH}" height="{FIGURE_HEIGHT}" fill="#ffffff"/>'
    '<text x="20" y="100" fill="#111111">light</text></svg>'
).encode('utf-8')
DARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    f'width="{FIGURE_WIDTH}" height="{FIGURE_HEIGHT}">'
    f'<rect width="{FIGURE_WIDTH}" height="{FIGURE_HEIGHT}" fill="#0b0f14"/>'
    '<text x="20" y="100" fill="#e6edf3">dark</text></svg>'
).encode('utf-8')

# Shared by the stored workshop row and the re-synced workshop.yaml so the
# sync updates the existing row instead of colliding on its slug.
RESYNC_CONTENT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'

KNOWN_IMAGES = frozenset({
    f'{BASE}/images/cv-pipeline.svg',
    f'{BASE}/images/cv-pipeline.dark.svg',
    f'{BASE}/images/chat.png',
})


def _png_bytes(width, height, rgb):
    """Build a minimal truecolour PNG so the screenshot has real dimensions."""
    row = b'\x00' + bytes(rgb) * width
    raw = row * height

    def _chunk(tag, data):
        payload = tag + data
        return (
            struct.pack('>I', len(data))
            + payload
            + struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF)
        )

    return (
        b'\x89PNG\r\n\x1a\n'
        + _chunk(b'IHDR', struct.pack(
            '>IIBBBBB', width, height, 8, 2, 0, 0, 0,
        ))
        + _chunk(b'IDAT', zlib.compress(raw))
        + _chunk(b'IEND', b'')
    )


SCREENSHOT_PNG = _png_bytes(
    SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT, (120, 120, 120),
)


# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


AUTHORED_PAIRED = (
    'Intro paragraph.\n\n![Pipeline](images/cv-pipeline.svg)\n\n'
    'Closing paragraph.\n'
)
AUTHORED_SCREENSHOT = (
    'Intro paragraph.\n\n![Chat](images/chat.png)\n\nClosing paragraph.\n'
)
AUTHORED_MIXED = (
    'Intro paragraph.\n\n![Pipeline](images/cv-pipeline.svg)\n\n'
    '![Chat](images/chat.png)\n\nClosing paragraph.\n'
)

# A tutorial page long enough that the reader really scrolls, so a swap
# that jumped the scroll position would be visible.
_FILLER = '\n\n'.join(
    f'Tutorial paragraph {i} explaining the step in enough words to fill '
    'a line of the reading column.' for i in range(1, 26)
)
AUTHORED_LONG_TUTORIAL = (
    f'{_FILLER}\n\n![Pipeline](images/cv-pipeline.svg)\n\n{_FILLER}\n'
)


@lru_cache(maxsize=None)
def _rewrite(markdown_text):
    """Run the production pairing emitter over authored markdown.

    Deferred to call time rather than import time: ``rewrite_image_urls``
    resolves the CDN base through ``get_config``, which reads the database.
    """
    from content.sync_parsers.media import rewrite_image_urls

    with override_settings(CONTENT_CDN_BASE=CDN):
        return rewrite_image_urls(
            markdown_text, REPO, BASE, known_images=KNOWN_IMAGES,
        )


UNPAIRED_LEGACY_BODY = (
    'Intro paragraph.\n\n'
    f'![Pipeline]({CDN}/workshops-content/{BASE}/images/cv-pipeline.svg)\n\n'
    'Closing paragraph.\n'
)


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    from integrations.models import ContentSource

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    ContentSource.objects.filter(repo_name=REPO).delete()
    connection.close()


def _create_workshop(
    *, slug, description=None, pages=0, pages_data=None,
    title='Theme Figure Workshop', content_id=None, source_repo=None,
    source_path=None,
):
    from content.models import Workshop, WorkshopPage

    if description is None:
        description = _rewrite(AUTHORED_PAIRED)
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=pages,
        # The model invariant requires recording >= pages.
        recording_required_level=pages,
        description=description,
        content_id=content_id,
        source_repo=source_repo,
        source_path=source_path,
    )
    for i, (page_slug, page_title, body) in enumerate(
        pages_data or [], start=1,
    ):
        WorkshopPage.objects.create(
            workshop=workshop, slug=page_slug, title=page_title,
            sort_order=i, body=body,
        )
    connection.close()
    return workshop


def _serve_figures(page):
    """Fulfil every CDN image request locally with real image bytes."""
    def _handler(route, request):
        url = request.url
        if url.endswith('.dark.svg'):
            route.fulfill(
                status=200, content_type='image/svg+xml', body=DARK_SVG,
            )
        elif url.endswith('.svg'):
            route.fulfill(
                status=200, content_type='image/svg+xml', body=LIGHT_SVG,
            )
        else:
            route.fulfill(
                status=200, content_type='image/png', body=SCREENSHOT_PNG,
            )

    page.route('**/content-images/**', _handler)


def _pin_theme(context, theme):
    """Seed the stored theme before the pre-paint script in base.html runs.

    Only seeds when nothing is stored yet, so a later toggle is not undone
    on the next navigation -- the persistence scenario depends on that.
    """
    context.add_init_script(
        "try {"
        "  if (window.localStorage.getItem('theme') === null) {"
        "    window.localStorage.setItem('theme', '" + theme + "');"
        "  }"
        "} catch (e) {}"
    )
    # Pin matchMedia so the headless OS preference cannot override the value.
    context.add_init_script(
        'window.matchMedia = function(q) {'
        '  return {'
        '    matches: false,'
        '    media: q,'
        '    addListener: function() {},'
        '    removeListener: function() {},'
        '    addEventListener: function() {},'
        '    removeEventListener: function() {},'
        '    dispatchEvent: function() { return false; }'
        '  };'
        '};'
    )


def _light(page):
    return page.locator('[data-theme-figure="light"]')


def _dark(page):
    return page.locator('[data-theme-figure="dark"]')


def _plain_images(page, testid):
    """Images inside the rendered body that are not part of a theme pair."""
    return page.locator(
        f'[data-testid="{testid}"] img:not([data-theme-figure])'
    )


def _set_sentinel(page):
    page.evaluate("() => { window.__noReloadSentinel = 'alive'; }")


def _sentinel_survived(page):
    return page.evaluate("() => window.__noReloadSentinel") == 'alive'


def _wait_for_images_loaded(page):
    page.wait_for_function(
        """() => {
            const imgs = Array.from(document.querySelectorAll('.prose img'));
            return imgs.length > 0 && imgs.every(i => i.complete);
        }""",
        timeout=10000,
    )


def _figure_document_offset(page, variant):
    """Return the figure's top offset within the document, not the viewport."""
    return page.evaluate(
        """(variant) => {
            const el = document.querySelector(
                '[data-theme-figure="' + variant + '"]'
            );
            return Math.round(
                el.getBoundingClientRect().top + window.scrollY
            );
        }""",
        variant,
    )


def _visible_theme_figure_count(page):
    return page.evaluate(
        """() => Array.from(
            document.querySelectorAll('[data-theme-figure]')
        ).filter(i => getComputedStyle(i).display !== 'none').length"""
    )


# ---------------------------------------------------------------------
# Scenario 1: dark-mode first load shows the dark figure
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDarkModeFirstLoad:
    @browser_journey
    def test_dark_reader_sees_the_dark_figure(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='dark-first-ws')
        _pin_theme(page.context, 'dark')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/dark-first-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)

        assert _dark(page).is_visible()
        assert _light(page).count() == 1
        assert _light(page).is_visible() is False
        assert _dark(page).get_attribute('src').endswith('.dark.svg')


# ---------------------------------------------------------------------
# Scenario 2: light-mode first load shows the light figure
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestLightModeFirstLoad:
    @browser_journey
    def test_light_reader_sees_the_light_figure(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='light-first-ws')
        _pin_theme(page.context, 'light')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/light-first-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)

        assert _light(page).is_visible()
        src = _light(page).get_attribute('src')
        assert src.endswith('.svg') and not src.endswith('.dark.svg')
        assert _dark(page).is_visible() is False


# ---------------------------------------------------------------------
# Scenario 3: the toggle swaps the figure without reloading the page
# ---------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestToggleToDarkKeepsThePage:
    @browser_journey
    def test_toggle_swaps_figure_with_no_reload(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='toggle-dark-ws')
        _pin_theme(page.context, 'light')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/toggle-dark-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)
        assert _light(page).is_visible()
        _set_sentinel(page)

        _click_visible_theme_toggle(page)

        # The swap is pure CSS, so exactly one variant is painted at every
        # sampled moment -- the figure never blanks out mid-swap.
        for _ in range(10):
            assert _visible_theme_figure_count(page) == 1
        assert _dark(page).is_visible()
        assert _light(page).is_visible() is False
        assert _sentinel_survived(page), (
            'the page reloaded: a CSS-only theme swap must not navigate'
        )


# ---------------------------------------------------------------------
# Scenario 4: toggling back restores the light figure and it persists
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestToggleBackToLightPersists:
    @browser_journey
    def test_toggle_back_and_reload_keeps_light(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='toggle-light-ws')
        _pin_theme(page.context, 'dark')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/toggle-light-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)
        assert _dark(page).is_visible()
        _set_sentinel(page)

        _click_visible_theme_toggle(page)

        assert _light(page).is_visible()
        assert _dark(page).is_visible() is False
        assert _sentinel_survived(page)

        page.reload(wait_until='domcontentloaded')
        _wait_for_images_loaded(page)
        assert _light(page).is_visible(), (
            'the light choice did not persist across a reload'
        )
        assert _dark(page).is_visible() is False


# ---------------------------------------------------------------------
# Scenario 5: tutorial page figures swap in place without a scroll jump
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTutorialPageFigureSwaps:
    @browser_journey
    def test_tutorial_figure_swaps_in_place(self, django_server, browser):
        _clear_workshops()
        _create_user('basic@test.com', tier_slug='basic')
        _create_workshop(
            slug='tutorial-figure-ws',
            pages=10,
            pages_data=[('setup', 'Setup', _rewrite(AUTHORED_LONG_TUTORIAL))],
        )
        context = _auth_context(browser, 'basic@test.com')
        _pin_theme(context, 'dark')
        page = context.new_page()
        _serve_figures(page)
        try:
            page.goto(
                f'{django_server}/workshops/tutorial-figure-ws/setup',
                wait_until='domcontentloaded',
            )
            _wait_for_images_loaded(page)

            body = page.locator('[data-testid="page-body"]')
            assert body.locator('[data-theme-figure="dark"]').is_visible()

            # Read the figure deep in a long page, then measure where it
            # sits in the document (immune to the scrolling the account
            # menu does when it opens).
            # `behavior: instant` because the site sets
            # `html { scroll-behavior: smooth }`, which would leave the
            # scroll still animating when the next line reads it.
            page.evaluate(
                """() => document
                    .querySelector('[data-theme-figure="dark"]')
                    .scrollIntoView({ block: 'center', behavior: 'instant' })"""
            )
            page.wait_for_function('() => window.scrollY > 0', timeout=5000)
            before = page.evaluate('() => Math.round(window.scrollY)')
            offset_before = _figure_document_offset(page, 'dark')
            text_before = body.inner_text()

            _click_visible_theme_toggle(page)

            assert body.locator('[data-theme-figure="light"]').is_visible()
            assert (
                body.locator('[data-theme-figure="dark"]').is_visible()
                is False
            )
            # The replacement sits at the same document position: the
            # surrounding tutorial text did not reflow around the swap.
            assert _figure_document_offset(page, 'light') == offset_before
            after = page.evaluate('() => Math.round(window.scrollY)')
            assert after > 0, (
                f'the theme swap threw the reader back to the top: '
                f'{before} -> {after}'
            )
            assert body.inner_text() == text_before
        finally:
            context.close()


# ---------------------------------------------------------------------
# Scenario 6: a screenshot with no dark sibling ignores the theme
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUnpairedScreenshotIgnoresTheme:
    @browser_journey
    def test_single_screenshot_is_untouched(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='screenshot-ws', description=_rewrite(AUTHORED_SCREENSHOT))
        _pin_theme(page.context, 'light')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/screenshot-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)

        images = _plain_images(page, 'workshop-description')
        assert images.count() == 1
        assert images.first.is_visible()
        assert page.locator('[data-theme-figure]').count() == 0

        _click_visible_theme_toggle(page)

        assert images.count() == 1
        assert images.first.is_visible()
        # A broken image collapses to a zero-size box; a real render does not.
        assert page.evaluate(
            """() => {
                const img = document.querySelector(
                    '[data-testid="workshop-description"] img'
                );
                return img.naturalWidth;
            }"""
        ) == SCREENSHOT_WIDTH


# ---------------------------------------------------------------------
# Scenario 7: a mixed page is correct in both themes
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestMixedFigureAndScreenshot:
    @browser_journey
    def test_two_images_visible_in_each_theme(self, django_server, page):
        _clear_workshops()
        _create_workshop(slug='mixed-ws', description=_rewrite(AUTHORED_MIXED))
        _pin_theme(page.context, 'dark')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/mixed-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)

        visible = page.locator(
            '[data-testid="workshop-description"] img:visible'
        )
        assert visible.count() == 2
        assert _dark(page).is_visible()
        assert _light(page).is_visible() is False

        _click_visible_theme_toggle(page)

        assert visible.count() == 2
        assert _light(page).is_visible()
        assert _dark(page).is_visible() is False
        assert _plain_images(page, 'workshop-description').count() == 1


# ---------------------------------------------------------------------
# Scenario 8: the figure keeps its authored size and loses the card border
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFigureKeepsAuthoredSize:
    @browser_journey
    def test_figure_is_not_stretched_to_the_reading_column(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='authored-size-ws', description=_rewrite(AUTHORED_MIXED))
        _pin_theme(page.context, 'light')
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/authored-size-ws',
            wait_until='domcontentloaded',
        )
        _wait_for_images_loaded(page)

        metrics = page.evaluate(
            """() => {
                const figure = document.querySelector(
                    '[data-theme-figure="light"]'
                );
                const shot = document.querySelector(
                    '[data-testid="workshop-description"] '
                    + 'img:not([data-theme-figure])'
                );
                const container = figure.parentElement.closest('.prose');
                return {
                    figureWidth: figure.getBoundingClientRect().width,
                    figureIntrinsic: figure.naturalWidth,
                    figureBorder: getComputedStyle(figure).borderTopWidth,
                    shotWidth: shot.getBoundingClientRect().width,
                    shotBorder: getComputedStyle(shot).borderTopWidth,
                    containerWidth: container.getBoundingClientRect().width,
                };
            }"""
        )

        assert metrics['figureIntrinsic'] == FIGURE_WIDTH
        assert round(metrics['figureWidth']) == FIGURE_WIDTH, (
            'the paired figure was rescaled away from its authored width'
        )
        assert metrics['figureWidth'] <= metrics['containerWidth']
        assert metrics['figureBorder'] == '0px'
        # The plain screenshot on the same page keeps the .prose img card
        # treatment, proving the override is scoped to theme figures.
        assert metrics['shotBorder'] != '0px'
        assert round(metrics['shotWidth']) > FIGURE_WIDTH


# ---------------------------------------------------------------------
# Scenario 9: a cold dark load never paints the light figure
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestColdDarkLoadHasNoWhiteFlash:
    @browser_journey
    def test_light_figure_is_hidden_the_moment_it_enters_the_dom(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(slug='cold-dark-ws')
        _pin_theme(page.context, 'dark')
        # Record the computed display of each variant at the instant it is
        # inserted -- before any frame containing it can be painted.
        page.context.add_init_script(
            """(() => {
                window.__figureFirstPaint = {};
                const record = () => {
                    for (const variant of ['light', 'dark']) {
                        if (window.__figureFirstPaint[variant]) { continue; }
                        const el = document.querySelector(
                            '[data-theme-figure="' + variant + '"]'
                        );
                        if (el) {
                            window.__figureFirstPaint[variant] =
                                getComputedStyle(el).display;
                        }
                    }
                };
                // Observe `document`, not `document.documentElement`:
                // an init script runs before <html> is parsed, so the
                // element is still null at this point.
                new MutationObserver(record).observe(
                    document,
                    { childList: true, subtree: true },
                );
            })();"""
        )
        _serve_figures(page)

        page.goto(
            f'{django_server}/workshops/cold-dark-ws',
            wait_until='domcontentloaded',
        )

        first_paint = page.evaluate('() => window.__figureFirstPaint')
        assert first_paint.get('light') == 'none', (
            'the light figure was visible when it entered the DOM: '
            'a white slab flashed before the theme applied'
        )
        assert first_paint.get('dark') not in (None, 'none'), (
            'the dark figure was not rendered on the frame it entered the DOM'
        )
        _wait_for_images_loaded(page)
        assert _dark(page).is_visible()


# ---------------------------------------------------------------------
# Scenario 10: an operator re-sync themes an already-stored workshop
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestOperatorResyncThemesStoredWorkshop:
    @browser_journey
    def test_resync_replaces_the_stored_single_image_body(
        self, django_server, browser,
    ):
        from content.models import Workshop
        from integrations.models import ContentSource
        from integrations.services.github import sync_content_source

        _clear_workshops()
        _create_staff_user('figures-staff@test.com')
        workshop = _create_workshop(
            slug='resync-figures-ws',
            description=UNPAIRED_LEGACY_BODY,
            content_id=RESYNC_CONTENT_ID,
            source_repo=REPO,
            source_path=BASE,
        )
        workshop_id = workshop.id
        source, _ = ContentSource.objects.get_or_create(
            repo_name=REPO, defaults={'is_private': False},
        )
        connection.close()

        context = _auth_context(browser, 'figures-staff@test.com')
        _pin_theme(context, 'dark')
        page = context.new_page()
        _serve_figures(page)
        try:
            # The operator starts from the sync dashboard.
            page.goto(
                f'{django_server}/studio/sync/',
                wait_until='domcontentloaded',
            )
            assert 'workshops-content' in page.content()

            page.goto(
                f'{django_server}/workshops/resync-figures-ws',
                wait_until='domcontentloaded',
            )
            _wait_for_images_loaded(page)
            assert page.locator('[data-theme-figure]').count() == 0

            # The Studio "Force resync" button clones from GitHub, which is
            # unavailable here, so the same sync entry point runs in-process
            # against a checkout that now contains the dark siblings.
            repo_dir = tempfile.mkdtemp(prefix='e2e-theme-figures-')
            try:
                _write_workshop_checkout(repo_dir)
                with override_settings(CONTENT_CDN_BASE=CDN):
                    sync_content_source(source, repo_dir=repo_dir)
            finally:
                shutil.rmtree(repo_dir, ignore_errors=True)
            stored = Workshop.objects.get(id=workshop_id)
            connection.close()
            assert 'data-theme-figure' in stored.description_html

            page.reload(wait_until='domcontentloaded')
            _wait_for_images_loaded(page)
            assert page.locator('[data-theme-figure="dark"]').is_visible()
            assert (
                page.locator('[data-theme-figure="light"]').is_visible()
                is False
            )
        finally:
            context.close()


def _write_workshop_checkout(repo_dir):
    """Write a workshops-content checkout carrying the dark sibling."""
    files = {
        f'{BASE}/workshop.yaml': (
            f'content_id: {RESYNC_CONTENT_ID}\n'
            'slug: resync-figures-ws\n'
            'title: "Theme Figure Workshop"\n'
            'date: 2026-04-21\n'
            'pages_required_level: 0\n'
            'landing_required_level: 0\n'
        ),
        f'{BASE}/README.md': (
            '# Theme Figure Workshop\n\nIntro paragraph.\n\n'
            '![Pipeline](images/cv-pipeline.svg)\n\nClosing paragraph.\n'
        ),
    }
    for rel_path, text in files.items():
        full = os.path.join(repo_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as handle:
            handle.write(text)
    for rel_path, blob in (
        (f'{BASE}/images/cv-pipeline.svg', LIGHT_SVG),
        (f'{BASE}/images/cv-pipeline.dark.svg', DARK_SVG),
    ):
        full = os.path.join(repo_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as handle:
            handle.write(blob)
