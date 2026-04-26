"""Playwright E2E tests for workshop landing copy via copy_file/README (issue #304).

These tests drive the SYNC pipeline end-to-end: they write a workshop folder
on disk, run ``sync_content_source`` against it, and then verify the rendered
landing page in the browser. The point of this issue is the seam between the
authoring contract (workshop.yaml + README.md / copy_file) and the rendered
output, so the tests are deliberately not pure DB fixture tests.

Scenarios mirror those in the issue:

1. Author replaces yaml description with a README -> landing picks it up.
2. ``copy_file: 01-intro.md`` overrides README; cross-page link routes to landing.
3. ``[README.md](README.md)`` and ``[link](README.md#anchor)`` from a tutorial
   page route back to the landing.
4. Workshop with no source copy renders a clean landing (no empty box).
5. README image references resolve to CDN URLs on the landing.

Usage:
    uv run pytest playwright_tests/test_workshop_copy_file.py -v
"""

import os
import shutil
import tempfile
import uuid

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

from playwright_tests.conftest import (  # noqa: E402
    auth_context as _auth_context,
)
from playwright_tests.conftest import (  # noqa: E402
    create_user as _create_user,
)


def _clear_workshops():
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    from integrations.models import ContentSource
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    # Wipe sync sources so each test starts clean.
    ContentSource.objects.filter(repo_name='AI-Shipping-Labs/workshops-content').delete()
    connection.close()


def _sync_workshop_repo(files):
    """Write ``files`` (rel_path -> contents) into a temp workshop repo and sync.

    ``files`` is a mapping of repo-relative paths to file contents (str). The
    workshops sync expects the layout ``<folder>/workshop.yaml`` with sibling
    ``.md`` pages, optionally a README.md.

    Returns the SyncLog so tests can assert on errors.
    """
    from integrations.models import ContentSource
    from integrations.services.github import sync_content_source

    source, _ = ContentSource.objects.get_or_create(
        repo_name='AI-Shipping-Labs/workshops-content',
        defaults={
            'is_private': False,
        },
    )

    temp_dir = tempfile.mkdtemp(prefix='e2e-workshop-copyfile-')
    try:
        for rel_path, content in files.items():
            full = os.path.join(temp_dir, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)

        sync_log = sync_content_source(source, repo_dir=temp_dir)
        connection.close()
        return sync_log
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _workshop_yaml(
    *,
    slug='ws',
    title='Production Agents',
    pages_required_level=0,
    landing_required_level=0,
    extra='',
):
    """Build a minimal workshop.yaml string, no description: by default."""
    body = (
        f'content_id: {uuid.uuid4()}\n'
        f'slug: {slug}\n'
        f'title: "{title}"\n'
        f'date: 2026-04-21\n'
        f'pages_required_level: {pages_required_level}\n'
        f'landing_required_level: {landing_required_level}\n'
        'instructor_name: Alexey\n'
    )
    body += extra
    return body


def _page_md(*, title, body=''):
    return f'---\ntitle: "{title}"\n---\n{body}'


# ----------------------------------------------------------------------
# Scenario 1: README replaces yaml description.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestReadmeBecomesLandingDescription:
    def test_readme_drives_landing_description(self, django_server, page):
        _clear_workshops()
        folder = '2026-04-21-readme-driven'
        readme = (
            '# Title we expect stripped\n'
            '\n'
            'This is the README intro.\n'
            '\n'
            'It has multiple paragraphs.\n'
            '\n'
            '```python\n'
            'print("hello")\n'
            '```\n'
        )
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='readme-driven', title='Readme Driven',
            ),
            f'{folder}/README.md': readme,
            # Need at least one tutorial page so the layout is realistic.
            f'{folder}/01-overview.md': _page_md(
                title='Overview', body='Overview body.\n',
            ),
        })
        # No non-info errors expected.
        non_info = [
            e for e in (sync_log.errors or []) if e.get('severity') != 'info'
        ]
        assert non_info == [], non_info

        page.goto(
            f'{django_server}/workshops/readme-driven',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="workshop-description"' in body
        # Multi-paragraph body rendered.
        assert 'This is the README intro.' in body
        assert 'It has multiple paragraphs.' in body
        # Code block has the codehilite class from render_markdown.
        assert 'codehilite' in body

        # Leading H1 was stripped — must NOT appear inside the description
        # block. Check it's not present anywhere in the rendered page.
        assert '<h1>Title we expect stripped</h1>' not in body
        # Yaml-derived placeholder is gone (we never wrote one).
        assert 'Workshop description body.' not in body


# ----------------------------------------------------------------------
# Scenario 2: copy_file overrides + tutorial-page link routes to landing.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCopyFileOverrideAndLink:
    def test_copy_file_drives_landing_and_link_routes_to_landing(
        self, browser, django_server,
    ):
        _clear_workshops()
        folder = '2026-04-21-copy-override'
        intro = (
            '# Intro heading\n'
            '\n'
            'Intro body content for the landing.\n'
            '\n'
            '```mermaid\n'
            'graph TD; A-->B;\n'
            '```\n'
        )
        next_page = (
            'Read the [Read the intro](01-intro.md) before continuing.\n'
        )
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='copy-override', title='Copy Override',
                # pages level 10 so a basic user is required to view tutorials.
                pages_required_level=10,
                # Landing level 0 so anonymous can see the description.
                landing_required_level=0,
                extra='copy_file: 01-intro.md\n',
            ),
            # README also exists but copy_file should win.
            f'{folder}/README.md': '# README\n\nReadme content (should be ignored).\n',
            f'{folder}/01-intro.md': _page_md(title='Intro', body=intro),
            f'{folder}/02-next.md': _page_md(title='Next', body=next_page),
        })
        non_info = [
            e for e in (sync_log.errors or []) if e.get('severity') != 'info'
        ]
        assert non_info == [], non_info

        # Anonymous user can see the landing (level 0).
        page = browser.new_page()
        page.goto(
            f'{django_server}/workshops/copy-override',
            wait_until='domcontentloaded',
        )
        body = page.content()
        assert 'data-testid="workshop-description"' in body
        # Intro file content is what shows on the landing.
        assert 'Intro body content for the landing.' in body
        # README content does NOT show.
        assert 'Readme content (should be ignored).' not in body
        # Mermaid block placeholder is rendered (issue #300 extension).
        assert 'class="mermaid"' in body
        # Leading H1 from 01-intro.md was stripped.
        assert '<h1>Intro heading</h1>' not in body
        page.close()

        # Basic user can see tutorial pages — verify the link to the intro
        # routes to the LANDING URL, not the tutorial URL.
        _create_user('basic@test.com', tier_slug='basic')
        ctx = _auth_context(browser, 'basic@test.com')
        tut_page = ctx.new_page()
        tut_page.goto(
            f'{django_server}/workshops/copy-override/tutorial/next',
            wait_until='domcontentloaded',
        )
        body = tut_page.content()
        # The link in 02-next.md routes to /workshops/copy-override (landing),
        # NOT to /workshops/copy-override/tutorial/intro.
        link = tut_page.locator('a:has-text("Read the intro")').first
        href = link.get_attribute('href')
        assert href == '/workshops/copy-override', (
            f'Expected link to landing /workshops/copy-override, got {href!r}'
        )
        # The tutorial URL for 01-intro.md must NOT appear in the link.
        assert '/tutorial/intro' not in href

        # Click the link and verify we land on the workshop landing.
        link.click()
        tut_page.wait_for_load_state('domcontentloaded')
        assert tut_page.url.rstrip('/').endswith('/workshops/copy-override')
        # Intro content is on this page.
        assert 'Intro body content for the landing.' in tut_page.content()

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 3: README links from a tutorial page route back to the landing.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestReadmeLinkRoutesBackToLanding:
    def test_readme_link_with_anchor_and_title_substitution(
        self, browser, django_server,
    ):
        _clear_workshops()
        folder = '2026-04-21-readme-links'
        readme = (
            '## Getting started\n'
            '\n'
            'This is the README content.\n'
        )
        qa = (
            'See [README.md](README.md) for the overview.\n'
            '\n'
            'Or jump to [see the overview](README.md#getting-started).\n'
        )
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='readme-links', title='Production Agents',
                pages_required_level=10, landing_required_level=0,
            ),
            f'{folder}/README.md': readme,
            f'{folder}/10-qa.md': _page_md(title='Q&A', body=qa),
        })
        non_info = [
            e for e in (sync_log.errors or []) if e.get('severity') != 'info'
        ]
        # No README.md unresolvable warnings.
        assert not any(
            'Unresolvable' in e.get('error', '')
            and 'README.md' in e.get('error', '')
            for e in non_info
        ), non_info

        _create_user('basic@test.com', tier_slug='basic')
        ctx = _auth_context(browser, 'basic@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/readme-links/tutorial/qa',
            wait_until='domcontentloaded',
        )

        # Bare-filename label is title-substituted to the workshop title.
        title_link = page.locator(
            'a[href="/workshops/readme-links"]'
        ).first
        assert title_link.inner_text().strip() == 'Production Agents'

        # Anchor-preserving link.
        anchor_link = page.locator(
            'a[href="/workshops/readme-links#getting-started"]'
        ).first
        assert anchor_link.inner_text().strip() == 'see the overview'

        # Click the bare-filename link -> lands on the landing.
        title_link.click()
        page.wait_for_load_state('domcontentloaded')
        assert page.url.rstrip('/').endswith('/workshops/readme-links')
        # README content visible on the landing.
        assert 'This is the README content.' in page.content()

        ctx.close()


# ----------------------------------------------------------------------
# Scenario 4: Workshop with no source copy renders a clean landing.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopWithoutDescriptionRendersCleanLanding:
    def test_landing_has_no_empty_description_box(self, django_server, page):
        _clear_workshops()
        folder = '2026-04-21-no-copy'
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='no-copy', title='No Copy Workshop',
                landing_required_level=0, pages_required_level=0,
            ),
            f'{folder}/01-only.md': _page_md(
                title='Only', body='Tutorial body.\n',
            ),
        })
        # No errors expected.
        assert sync_log.errors == [], sync_log.errors

        page.goto(
            f'{django_server}/workshops/no-copy',
            wait_until='domcontentloaded',
        )
        body = page.content()

        # Landing rendered: title is visible.
        assert 'data-testid="workshop-title"' in body
        assert 'No Copy Workshop' in body
        # Tutorial page card visible.
        assert 'Only' in body
        # Empty description box is suppressed by the template's
        # `{% if workshop.description_html %}` guard.
        assert 'data-testid="workshop-description"' not in body


# ----------------------------------------------------------------------
# Scenario 5: README images resolve to CDN URLs.
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestReadmeImagesResolveToCdnUrls:
    def test_readme_image_path_rewritten_on_landing(
        self, django_server, page,
    ):
        _clear_workshops()
        folder = '2026-04-21-image-readme'
        readme = (
            'See architecture: ![architecture](images/architecture.png)\n'
        )
        sync_log = _sync_workshop_repo({
            f'{folder}/workshop.yaml': _workshop_yaml(
                slug='image-readme', title='Image Readme',
                landing_required_level=0, pages_required_level=0,
            ),
            f'{folder}/README.md': readme,
            f'{folder}/01-only.md': _page_md(
                title='Only', body='Tutorial body.\n',
            ),
        })
        non_info = [
            e for e in (sync_log.errors or []) if e.get('severity') != 'info'
        ]
        assert non_info == [], non_info

        page.goto(
            f'{django_server}/workshops/image-readme',
            wait_until='domcontentloaded',
        )
        # Find the architecture image inside the description block.
        img = page.locator(
            '[data-testid="workshop-description"] img[alt="architecture"]'
        ).first
        src = img.get_attribute('src')
        # The bare path `images/architecture.png` must NOT appear as the src.
        assert src is not None
        assert 'images/architecture.png' in src
        # The src is prefixed with the CDN base.
        from integrations.config import get_config
        cdn_base = get_config('CONTENT_CDN_BASE', '/static/content-images')
        assert cdn_base in src, (
            f'Expected CDN base {cdn_base!r} in img src {src!r}'
        )
