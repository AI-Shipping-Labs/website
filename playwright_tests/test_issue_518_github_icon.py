"""Playwright E2E tests for issue #518.

The "View code on GitHub" button on workshop pages was rendering with a
phantom left padding and an invisible icon in dark mode because the
``<i data-lucide="github">`` placeholder no longer hydrates — upstream
Lucide moved brand glyphs out of the core package and the
``unpkg.com/lucide@latest`` URL now resolves to ``lucide@1.14.0``. The
empty ``<i>`` kept its ``h-4 w-4`` slot plus the parent's ``gap-2``,
producing the gap the user reported.

The fix replaces the four ``data-lucide="github"`` placeholders with an
inline SVG via ``includes/_icon_github.html`` that uses ``currentColor``
so it inherits the foreground color in both light and dark themes.

These scenarios exercise every affected page in light + dark mode and
take screenshots saved to ``playwright_tests/screenshots/issue-518/``.

Usage:
    uv run pytest playwright_tests/test_issue_518_github_icon.py -v
"""

import datetime
import os
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("playwright_tests/screenshots/issue-518")
DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 393, "height": 851}


def _screenshot_dir():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_workshop(
    slug="aihero-workshop",
    title="Production Agents",
    landing=0,
    pages=0,
    recording=0,
    code_repo_url="https://github.com/example/repo",
    pages_data=None,
    with_event=False,
    source_repo="AI-Shipping-Labs/workshops-content",
    source_path=None,
):
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    event = None
    if with_event:
        event = Event.objects.create(
            slug=f"{slug}-event",
            title=title,
            start_datetime=timezone.now(),
            status="completed",
            kind="workshop",
            recording_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            published=True,
        )

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=datetime.date(2026, 4, 21),
        status="published",
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description="Workshop description body.",
        code_repo_url=code_repo_url,
        cover_image_url="",
        tags=[],
        event=event,
        source_repo=source_repo,
        source_path=source_path or f"2026/{slug}/workshop.yaml",
        source_commit="abc1234def5678901234567890123456789abcde",
    )

    pages_data = pages_data or [
        ("intro", "Introduction", "# Welcome\n\nThis is the intro."),
    ]
    for i, (s, t, body) in enumerate(pages_data, start=1):
        WorkshopPage.objects.create(
            workshop=workshop,
            slug=s,
            title=t,
            sort_order=i,
            body=body,
        )

    connection.close()
    return workshop


def _set_theme(page, theme):
    """Force a specific theme by toggling the ``dark`` class on
    ``<html>``. The base template's blocking script reads from
    ``localStorage.theme`` on first paint; we set both so reloads stick.
    """
    page.evaluate(
        """(theme) => {
            try { localStorage.setItem('theme', theme); } catch (e) {}
            const html = document.documentElement;
            if (theme === 'dark') {
                html.classList.add('dark');
            } else {
                html.classList.remove('dark');
            }
        }""",
        theme,
    )


def _wait_for_lucide(page):
    """Wait until Lucide has finished hydrating ``<i data-lucide=...>``
    placeholders. Sibling icons like ``external-link`` are non-brand and
    still render, so once one of them is an SVG we know the script ran.
    """
    page.wait_for_function(
        """() => {
            const placeholder = document.querySelector(
                'i[data-lucide="external-link"]'
            );
            // Either the placeholder was replaced by an SVG, or there
            // are no external-link placeholders on this page.
            return placeholder === null;
        }""",
        timeout=5000,
    )


# ---------------------------------------------------------------------
# Scenario 1: Anonymous visitor on workshop landing — light mode 1280
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_landing_light_mode_1280(django_server, browser):
    _clear_workshops()
    _create_workshop()

    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    _set_theme(page, "light")
    page.goto(
        f"{django_server}/workshops/aihero-workshop",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "light")
    _wait_for_lucide(page)

    # Confirm we are not in dark mode.
    is_dark = page.evaluate(
        "() => document.documentElement.classList.contains('dark')",
    )
    assert is_dark is False

    repo_link = page.locator('[data-testid="workshop-outline-material-row"]:has(svg[data-icon="github"])')
    assert repo_link.count() == 1
    assert repo_link.is_visible()
    assert "Code repository" in repo_link.inner_text()

    # The GitHub icon must be a real SVG with non-zero size.
    icon = repo_link.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0
    assert icon_box["height"] > 0

    # The unhydrated <i> placeholder must NOT exist inside the button.
    assert (
        repo_link.locator('i[data-lucide="github"]').count() == 0
    )

    page.screenshot(
        path=str(_screenshot_dir() / "issue-518-workshop-light-1280.png"),
        full_page=True,
    )
    context.close()


# ---------------------------------------------------------------------
# Scenario 2: Anonymous visitor on workshop landing — dark mode 1280
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_landing_dark_mode_1280(django_server, browser):
    _clear_workshops()
    _create_workshop()

    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    _set_theme(page, "dark")
    page.goto(
        f"{django_server}/workshops/aihero-workshop",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    is_dark = page.evaluate(
        "() => document.documentElement.classList.contains('dark')",
    )
    assert is_dark is True

    repo_link = page.locator('[data-testid="workshop-outline-material-row"]:has(svg[data-icon="github"])')
    icon = repo_link.locator('svg[data-icon="github"]').first
    assert icon.count() == 1

    # Stroke must be currentColor so the icon inherits foreground in
    # dark mode. We assert via the underlying attribute rather than the
    # computed style because Tailwind doesn't define a colour for raw
    # SVG strokes — the inheritance happens through the CSS color
    # property on the parent <a>.
    stroke = icon.get_attribute("stroke")
    assert stroke == "currentColor"

    # The parent <a> inherits text-foreground; resolve its computed
    # color and assert it isn't transparent.
    parent_color = page.evaluate(
        """() => {
            const a = document.querySelector(
                '[data-testid="workshop-outline-material-row"]:has(svg[data-icon="github"])'
            );
            return getComputedStyle(a).color;
        }""",
    )
    assert "rgba(0, 0, 0, 0)" not in parent_color
    assert parent_color != "transparent"

    page.screenshot(
        path=str(_screenshot_dir() / "issue-518-workshop-dark-1280.png"),
        full_page=True,
    )
    context.close()


# ---------------------------------------------------------------------
# Scenario 3: Mobile visitor — both themes at 393x851
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_landing_mobile_light_and_dark(django_server, browser):
    _clear_workshops()
    _create_workshop()

    context = browser.new_context(viewport=MOBILE)
    page = context.new_page()

    # Light mode 393.
    _set_theme(page, "light")
    page.goto(
        f"{django_server}/workshops/aihero-workshop",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "light")
    _wait_for_lucide(page)

    repo_link = page.locator('[data-testid="workshop-outline-material-row"]:has(svg[data-icon="github"])')
    repo_link.scroll_into_view_if_needed()
    assert repo_link.is_visible()
    icon = repo_link.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0

    # Button must not overflow the viewport horizontally.
    repo_box = repo_link.bounding_box()
    assert repo_box is not None
    assert repo_box["x"] >= 0
    assert repo_box["x"] + repo_box["width"] <= MOBILE["width"]

    page.screenshot(
        path=str(_screenshot_dir() / "issue-518-workshop-light-393.png"),
        full_page=True,
    )

    # Dark mode 393.
    _set_theme(page, "dark")
    page.reload(wait_until="domcontentloaded")
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    repo_link = page.locator('[data-testid="workshop-outline-material-row"]:has(svg[data-icon="github"])')
    repo_link.scroll_into_view_if_needed()
    icon = repo_link.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0

    page.screenshot(
        path=str(_screenshot_dir() / "issue-518-workshop-dark-393.png"),
        full_page=True,
    )
    context.close()


# ---------------------------------------------------------------------
# Scenario 4: Workshop reader sidebar — dark mode
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_workshop_reader_sidebar_dark(django_server, browser):
    _clear_workshops()
    _create_workshop(
        slug="reader-ws",
        landing=0,
        pages=0,
        recording=0,
    )
    _create_user("reader@test.com", tier_slug="basic")

    ctx = _auth_context(browser, "reader@test.com")
    ctx.set_default_timeout(8000)
    # Use desktop viewport so the desktop sidebar renders (not mobile drawer).
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)

    _set_theme(page, "dark")
    page.goto(
        f"{django_server}/workshops/reader-ws/tutorial/intro",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    sidebar_link = page.locator('[data-testid="sidebar-code-repo-link"]')
    assert sidebar_link.count() >= 1
    icon = sidebar_link.first.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0
    assert icon_box["height"] > 0

    sidebar_link.first.screenshot(
        path=str(_screenshot_dir() / "issue-518-reader-sidebar-dark.png"),
    )
    ctx.close()


# ---------------------------------------------------------------------
# Scenario 5: Studio workshop detail — dark mode
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_studio_workshop_detail_dark(django_server, browser):
    _clear_workshops()
    _ensure_tiers()
    workshop = _create_workshop(
        slug="studio-ws",
        landing=0,
        pages=0,
        recording=0,
    )
    _create_staff_user(email="staff-518@test.com")

    ctx = _auth_context(browser, "staff-518@test.com")
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)
    _set_theme(page, "dark")

    page.goto(
        f"{django_server}/studio/workshops/{workshop.pk}/",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    # Find the Code repo block.
    code_repo_block = page.locator('text=Code repo').first
    assert code_repo_block.count() == 1

    # The link inside the Code repo card should have the inline SVG.
    repo_anchor = page.locator(
        'a[href="https://github.com/example/repo"]',
    ).first
    assert repo_anchor.count() == 1
    icon = repo_anchor.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0
    assert icon_box["height"] > 0

    page.screenshot(
        path=str(_screenshot_dir() / "issue-518-studio-workshop-dark.png"),
        full_page=True,
    )
    ctx.close()


# ---------------------------------------------------------------------
# Scenario 6: Studio sticky action bar — light + dark
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_studio_sticky_action_bar_both_themes(django_server, browser):
    """The sticky action bar's Edit-on-GitHub button shows for synced
    content (objects with ``source_repo`` set). We seed a synced article
    and open its Studio edit page in light + dark mode."""
    from content.models import Article

    Article.objects.all().delete()
    article = Article.objects.create(
        title="Synced Article",
        slug="synced-article-518",
        date=datetime.date(2026, 4, 21),
        published=True,
        content_markdown="Body.",
        source_repo="AI-Shipping-Labs/content",
        source_path="articles/synced-article-518.md",
    )
    connection.close()

    _ensure_tiers()
    _create_staff_user(email="staff-518@test.com")
    ctx = _auth_context(browser, "staff-518@test.com")
    page = ctx.new_page()
    page.set_viewport_size(DESKTOP)

    # Light mode.
    _set_theme(page, "light")
    page.goto(
        f"{django_server}/studio/articles/{article.pk}/edit",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "light")
    _wait_for_lucide(page)

    sticky = page.locator('[data-testid="sticky-github-source-link"]')
    assert sticky.count() == 1
    icon = sticky.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0
    sticky.screenshot(
        path=str(_screenshot_dir() / "issue-518-sticky-light.png"),
    )

    # Dark mode.
    _set_theme(page, "dark")
    page.reload(wait_until="domcontentloaded")
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    sticky = page.locator('[data-testid="sticky-github-source-link"]')
    assert sticky.count() == 1
    icon = sticky.locator('svg[data-icon="github"]').first
    assert icon.count() == 1
    icon_box = icon.bounding_box()
    assert icon_box is not None
    assert icon_box["width"] > 0
    sticky.screenshot(
        path=str(_screenshot_dir() / "issue-518-sticky-dark.png"),
    )
    ctx.close()


# ---------------------------------------------------------------------
# Scenario 7: Sibling Lucide icons must not regress
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_sibling_lucide_icons_still_hydrate(django_server, browser):
    """The fix is scoped to the GitHub brand glyph. Other Lucide icons
    on the workshop landing page must still hydrate to ``<svg>`` so the
    fix does not break neighbours."""
    _clear_workshops()
    _create_workshop(
        slug="sibling-ws",
        landing=0,
        pages=0,
        recording=0,
        with_event=False,
    )

    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    _set_theme(page, "dark")
    page.goto(
        f"{django_server}/workshops/sibling-ws",
        wait_until="domcontentloaded",
    )
    _set_theme(page, "dark")
    _wait_for_lucide(page)

    # No <i data-lucide="external-link"> placeholders should remain on
    # the page after Lucide has run.
    leftover_external = page.locator('i[data-lucide="external-link"]').count()
    assert leftover_external == 0

    # No console errors related to Lucide hydration.
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    # Reload to capture any errors at hydration time.
    page.reload(wait_until="domcontentloaded")
    _wait_for_lucide(page)
    assert not any("lucide" in e.lower() for e in errors), errors

    context.close()
