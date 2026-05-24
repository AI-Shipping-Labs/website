"""Playwright E2E tests for Mermaid diagram rendering (issue #300).

Covers the BDD scenarios in the issue:

1. Reader views an architecture diagram on a workshop tutorial page —
   the diagram renders as inline <svg>, the page logs a request to the
   Mermaid CDN, and the raw "flowchart LR" source no longer appears
   outside the SVG.
2. Pages without diagrams do not pay the Mermaid download cost — the
   browser never issues a request to ``cdn.jsdelivr.net/npm/mermaid``.
3. Article author embeds a diagram in a blog post — same shared
   extension also works on the article surface, proving the change is
   not workshop-specific.
4. Mermaid source with HTML-special characters renders safely — no
   alert dialog fires, the literal text remains visible inside the SVG
   labels.

Usage:
    uv run python -m pytest playwright_tests/test_mermaid.py -v
"""

import datetime
import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module seeds Articles and Workshops via *.objects.create
# and cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only

from django.db import connection  # noqa: E402

# Reuse the workshop helper from the workshops E2E module so we
# stay in lock-step with how those tests build fixtures.
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

MERMAID_CDN_HOST = 'cdn.jsdelivr.net/npm/mermaid'


def _clear_articles():
    from content.models import Article

    Article.objects.all().delete()
    connection.close()


# Shared mermaid fence used in scenarios 1 and 2.
WORKSHOP_MERMAID_BODY = (
    "# Architecture\n\n"
    "```mermaid\n"
    "flowchart LR\n"
    '    UI["Frontend UI"] --> API["FastAPI app"]\n'
    '    API --> AGENT["Agent loop"]\n'
    "```\n"
)

# Plain markdown for the no-diagram page in scenario 2.
WORKSHOP_PLAIN_BODY = (
    "# Setup\n\n"
    "Install the dependencies before running the server.\n\n"
    "```python\n"
    "import requests\n"
    "requests.get('https://example.com')\n"
    "```\n"
)


# Mermaid source containing characters that would be dangerous if not escaped.
WORKSHOP_XSS_BODY = (
    "# XSS\n\n"
    "```mermaid\n"
    "flowchart LR\n"
    '    A["<script>alert(1)</script>"] --> B["A & B"]\n'
    "```\n"
)


# Mermaid source that uses ``<br/>`` to force a two-line node label.
# Mirrors the three live workshop pages that triggered issue #791. With
# the server-side ``<br>`` -> ``\n`` substitution in place, Mermaid 10
# (securityLevel: 'strict') renders this as a two-line label instead of
# rejecting the diagram.
WORKSHOP_MERMAID_BR_BODY = (
    "# Architecture\n\n"
    "```mermaid\n"
    "flowchart LR\n"
    '    SEARCH["search tool<br/>Data Engineering Zoomcamp FAQ"] '
    '--> AGENT["Agent"]\n'
    "```\n"
)


@pytest.mark.django_db(transaction=True)
class TestWorkshopMermaidDiagramRenders:
    """Scenario 1: Reader views an architecture diagram."""

    def test_diagram_renders_as_svg_with_node_labels(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='architecture-walk-through',
            title='Architecture Walk-through',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('architecture', 'Architecture', WORKSHOP_MERMAID_BODY),
            ],
        )

        cdn_requests = []
        page.on(
            'request',
            lambda req: cdn_requests.append(req.url)
            if MERMAID_CDN_HOST in req.url else None,
        )
        page.goto(
            f'{django_server}'
            f'/workshops/architecture-walk-through/tutorial/architecture',
            wait_until='domcontentloaded',
        )

        # Body container is present.
        page.locator('[data-testid="page-body"]').wait_for(
            state='attached', timeout=2000,
        )

        # Wait until Mermaid has populated the SVG with the node labels.
        # mermaid.run sets data-processed="true" early (when it claims the
        # node), so we cannot rely on that attribute alone — we wait for
        # the labels to actually appear inside <foreignObject>.
        page.wait_for_function(
            """() => {
                const fos = document.querySelectorAll(
                    'div.mermaid foreignObject'
                );
                const text = Array.from(fos)
                    .map(n => n.textContent).join('|');
                return text.includes('Frontend UI')
                    && text.includes('FastAPI app')
                    && text.includes('Agent loop');
            }""",
            timeout=15000,
        )

        # Sanity check: the rendered SVG is attached.
        page.locator('div.mermaid svg').first.wait_for(
            state='attached', timeout=2000,
        )

        # The page logged at least one request to the Mermaid CDN.
        assert any(MERMAID_CDN_HOST in url for url in cdn_requests), (
            f'expected a request to {MERMAID_CDN_HOST}, '
            f'got: {cdn_requests!r}'
        )

        # The original "flowchart LR" line should no longer be visible
        # outside the SVG: Mermaid replaces the div's contents with the
        # rendered diagram, so the only place 'flowchart LR' could still
        # appear is the data-processed wrapper's data attribute (which
        # is not rendered text). textContent of the body should not
        # include it.
        body_text = page.locator('[data-testid="page-body"]').text_content()
        assert 'flowchart LR' not in (body_text or ''), (
            'raw mermaid source still visible after render'
        )


@pytest.mark.django_db(transaction=True)
class TestPagesWithoutDiagramsSkipMermaidDownload:
    """Scenario 2: pages without diagrams must not fetch the CDN bundle."""

    def test_no_request_to_mermaid_cdn_on_plain_page(
        self, browser, django_server,
    ):
        _clear_workshops()
        _create_workshop(
            slug='architecture-walk-through',
            title='Architecture Walk-through',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                # Page 1 has the diagram; visited only by scenario 1.
                ('architecture', 'Architecture', WORKSHOP_MERMAID_BODY),
                # Page 2 is plain markdown — what we visit here.
                ('setup', 'Setup', WORKSHOP_PLAIN_BODY),
            ],
        )

        # Fresh context so cached resources from prior tests are not
        # counted as "this page's network activity".
        ctx = browser.new_context(viewport={'width': 1280, 'height': 720})
        page = ctx.new_page()

        cdn_requests = []
        page.on(
            'request',
            lambda req: cdn_requests.append(req.url)
            if MERMAID_CDN_HOST in req.url else None,
        )

        page.goto(
            f'{django_server}'
            f'/workshops/architecture-walk-through/tutorial/setup',
            wait_until='domcontentloaded',
        )
        # The assertion below is "no CDN requests fired"; wait for the
        # network to actually idle instead of sleeping a fixed 500ms so
        # any in-flight import would have to complete first (#290).
        page.wait_for_load_state('networkidle', timeout=3000)

        # Sanity check: no mermaid divs on this page.
        assert page.locator('div.mermaid').count() == 0

        assert cdn_requests == [], (
            f'expected zero mermaid CDN requests, got {cdn_requests!r}'
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestArticleMermaidDiagramRenders:
    """Scenario 3: a published article also renders mermaid via the same
    shared extension."""

    def test_blog_post_diagram_renders_as_svg(
        self, django_server, page,
    ):
        _clear_articles()
        from content.models import Article

        body_md = (
            "# Sequence\n\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    Browser->>Server: GET /api\n"
            "    Server-->>Browser: 200 OK\n"
            "```\n"
        )
        Article.objects.create(
            title='Mermaid Article',
            slug='mermaid-article',
            description='Article with a sequence diagram.',
            content_markdown=body_md,
            author='Tester',
            tags=['test'],
            published=True,
            date=datetime.date(2026, 4, 1),
        )
        connection.close()

        page.goto(
            f'{django_server}/blog/mermaid-article',
            wait_until='domcontentloaded',
        )

        # Sequence diagrams use SVG <text> elements (not <foreignObject>),
        # so we sweep both possibilities.
        page.wait_for_function(
            """() => {
                const labels = document.querySelectorAll(
                    'div.mermaid foreignObject, div.mermaid text'
                );
                const text = Array.from(labels)
                    .map(n => n.textContent).join('|');
                return text.includes('Browser')
                    && text.includes('Server');
            }""",
            timeout=15000,
        )
        page.locator('div.mermaid svg').first.wait_for(
            state='attached', timeout=2000,
        )


@pytest.mark.django_db(transaction=True)
class TestMermaidEscapesHtmlSpecialCharacters:
    """Scenario 4: HTML-special chars in mermaid labels must not execute
    or break the renderer. The XSS payload's <script> must never run."""

    def test_xss_payload_does_not_execute(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='xss-walkthrough',
            title='XSS Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('xss', 'XSS', WORKSHOP_XSS_BODY),
            ],
        )

        # If a JS alert ever fires, we'd hang waiting for a dialog.
        # Register a handler that auto-dismisses but flags the failure.
        dialogs = []

        def _on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on('dialog', _on_dialog)

        page.goto(
            f'{django_server}/workshops/xss-walkthrough/tutorial/xss',
            wait_until='domcontentloaded',
        )

        # Mermaid renders the diagram (or fails gracefully — either way
        # no alert can fire because the source was escaped server-side).
        page.locator('[data-testid="page-body"]').wait_for(
            state='attached', timeout=2000,
        )
        # Wait for mermaid to either finish (post-render marker) or for
        # the network to idle if it bailed out, instead of a fixed 2s
        # sleep (#290). A mermaid-rendered diagram either gets an SVG
        # child or a `data-processed="true"` attribute on the div.
        try:
            page.wait_for_function(
                """
                () => {
                  const nodes = document.querySelectorAll('div.mermaid');
                  if (nodes.length === 0) return false;
                  return Array.from(nodes).every(
                    n => n.getAttribute('data-processed') === 'true'
                       || n.querySelector('svg') !== null
                  );
                }
                """,
                timeout=3000,
            )
        except Exception:
            # Mermaid may bail entirely on the malicious source; fall
            # back to a network-idle wait so any in-flight CDN fetch or
            # dialog dispatch would have completed by now.
            page.wait_for_load_state('networkidle', timeout=3000)

        assert dialogs == [], (
            f'unexpected dialogs fired: {dialogs!r}'
        )

        # Whatever Mermaid does (renders or errors), the raw <script>
        # tag must not be present anywhere in the rendered DOM.
        html = page.content()
        assert '<script>alert(1)</script>' not in html, (
            'unescaped <script> leaked into the DOM'
        )


@pytest.mark.django_db(transaction=True)
class TestWorkshopMermaidBrTagInLabel:
    """Issue #791: a node label written with ``<br/>`` must render as a
    two-line label, not raw ``flowchart LR`` source. Reproduces the three
    live workshop pages broken in prod, scoped to a fixture page so the
    test does not depend on synced content."""

    @pytest.mark.core
    def test_br_tag_label_renders_as_svg_two_lines(
        self, django_server, page,
    ):
        _clear_workshops()
        _create_workshop(
            slug='br-tag-walkthrough',
            title='Br Tag Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BR_BODY),
            ],
        )

        # Guard against the XSS regression slipping into this flow: any
        # alert() during load would hang the page wait_for_function below.
        dialogs = []

        def _on_dialog(d):
            dialogs.append(d.message)
            d.dismiss()

        page.on('dialog', _on_dialog)

        page.goto(
            f'{django_server}/workshops/br-tag-walkthrough/tutorial/arch',
            wait_until='domcontentloaded',
        )

        page.locator('[data-testid="page-body"]').wait_for(
            state='attached', timeout=2000,
        )

        # Wait until Mermaid finishes and the two label fragments are
        # present in the rendered SVG. If the <br/> survived as literal
        # text, the substring would be 'search tool<br/>Data Engineering
        # Zoomcamp FAQ' inside a single label and 'Data Engineering
        # Zoomcamp FAQ' would not appear as its own line.
        page.wait_for_function(
            """() => {
                const fos = document.querySelectorAll(
                    'div.mermaid foreignObject'
                );
                const text = Array.from(fos)
                    .map(n => n.textContent).join('|');
                return text.includes('search tool')
                    && text.includes('Data Engineering Zoomcamp FAQ')
                    && text.includes('Agent');
            }""",
            timeout=15000,
        )

        # The SVG was actually emitted (i.e. Mermaid didn't bail with a
        # parse error).
        assert page.locator('div.mermaid svg').count() == 1, (
            'expected exactly one rendered SVG inside div.mermaid'
        )

        # The label must be split across two visual lines. Mermaid 10
        # turns the ``\n`` in the source into a real ``<br>`` element
        # inside the node's ``<span class="nodeLabel">`` (proven by
        # manual inspection — see issue #791). Assert on the rendered
        # innerHTML of the SEARCH node's label, not on textContent (which
        # collapses ``<br>`` to an empty string).
        label_html = page.evaluate(
            """() => {
                const fos = document.querySelectorAll(
                    'div.mermaid foreignObject'
                );
                for (const fo of fos) {
                    const text = fo.textContent || '';
                    if (text.includes('search tool')
                        && text.includes('Data Engineering Zoomcamp FAQ')) {
                        const label = fo.querySelector('.nodeLabel');
                        return label ? label.innerHTML : '';
                    }
                }
                return '';
            }"""
        )
        # The pre-fix bug emitted '&lt;br/&gt;' (a literal four-char
        # entity Mermaid wrote into the label as visible text) because
        # html.escape ran AFTER the source was captured. After the fix,
        # Mermaid receives '\n' which it turns into a real <br>.
        assert '<br' in label_html.lower(), (
            f'expected a <br> inside the rendered label so the two '
            f'fragments display on separate lines; got {label_html!r}'
        )
        assert 'search tool' in label_html, (
            f'first line missing from rendered label; got {label_html!r}'
        )
        assert 'Data Engineering Zoomcamp FAQ' in label_html, (
            f'second line missing from rendered label; got {label_html!r}'
        )
        # Regression guard: the escaped entity from the broken state
        # must NOT appear anywhere in the rendered label.
        assert '&lt;br' not in label_html, (
            f'escaped <br> entity leaked into the rendered label '
            f'(pre-fix behaviour); got {label_html!r}'
        )

        # The raw fence source must not still be visible — that was the
        # symptom of the prod bug.
        body_text = page.locator('[data-testid="page-body"]').text_content()
        assert 'flowchart LR' not in (body_text or ''), (
            'raw mermaid source still visible after render'
        )

        assert dialogs == [], (
            f'no dialog should fire on the <br/> label page; '
            f'got: {dialogs!r}'
        )
