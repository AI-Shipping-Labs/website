"""
Playwright E2E tests for the Copy button on code blocks (issue #228).

The base template injects a Copy button next to every <pre> inside a .prose
container. These tests verify:

1. The button is rendered for every code block on a real article page.
2. Clicking the button writes the raw code text to the clipboard
   (no HTML, no Pygments line-number gutter, no trailing newline).
3. The button text flips to "Copied!" briefly, then reverts.

Usage:
    uv run python -m pytest playwright_tests/test_code_copy.py -v
"""

import datetime
import os

import pytest

# Playwright creates an async event loop internally. Django's async safety
# check would otherwise raise SynchronousOnlyOperation when we make ORM calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402


def _clear_articles():
    from content.models import Article

    Article.objects.all().delete()
    connection.close()


def _create_article_with_code(slug):
    """Create a published article whose body has two fenced code blocks."""
    from content.models import Article

    body_md = (
        "# Code Copy Demo\n\n"
        "Here is a Python snippet:\n\n"
        "```python\n"
        "def greet(name):\n"
        "    return f\"hello {name}\"\n"
        "```\n\n"
        "And a shell snippet:\n\n"
        "```bash\n"
        "echo \"hello world\"\n"
        "```\n"
    )
    article = Article(
        title="Code Copy Demo",
        slug=slug,
        description="Demo article for the copy button.",
        content_markdown=body_md,
        author="Tester",
        tags=["test"],
        published=True,
        date=datetime.date(2026, 4, 1),
    )
    article.save()
    connection.close()
    return article


@pytest.mark.django_db(transaction=True)
class TestCodeCopyButton:

    def test_copy_button_renders_on_every_code_block(self, django_server, page):
        """Each <pre> inside .prose should get a Copy button."""
        _clear_articles()
        _create_article_with_code("code-copy-demo")

        page.goto(
            f"{django_server}/blog/code-copy-demo",
            wait_until="domcontentloaded",
        )

        # Two code blocks → two copy buttons.
        buttons = page.locator('[data-testid="code-copy-btn"]')
        buttons.first.wait_for(state="attached", timeout=2000)
        assert buttons.count() == 2, (
            f"expected 2 copy buttons, got {buttons.count()}"
        )

        # Each button must sit inside a .code-copy-wrapper (so the
        # absolute-positioned button doesn't fly to the page corner).
        wrappers = page.locator('.prose .code-copy-wrapper')
        assert wrappers.count() == 2

    def test_clicking_copies_raw_code_to_clipboard(
        self, django_server, browser
    ):
        """Click the first Copy button and verify the clipboard contents."""
        _clear_articles()
        _create_article_with_code("code-copy-demo")

        context = browser.new_context(
            viewport={"width": 1280, "height": 720}
        )
        # Headless Chromium needs explicit clipboard permissions.
        context.grant_permissions(
            ["clipboard-read", "clipboard-write"]
        )
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/code-copy-demo",
            wait_until="domcontentloaded",
        )

        btn = page.locator('[data-testid="code-copy-btn"]').first
        btn.wait_for(state="attached", timeout=2000)
        # Force-click: hover-revealed buttons are not "visible" without hover
        # in headless mode, but the click handler still fires.
        btn.click(force=True)

        # Button text flips to "Copied!" within the 1.5s window.
        page.locator(
            '[data-testid="code-copy-btn"].is-copied'
        ).first.wait_for(state="attached", timeout=2000)

        # Clipboard contains the raw Python code, no HTML, no trailing newline.
        clipboard_text = page.evaluate(
            "() => navigator.clipboard.readText()"
        )
        expected = 'def greet(name):\n    return f"hello {name}"'
        assert clipboard_text == expected, (
            f"clipboard mismatch:\n--- expected ---\n{expected!r}\n"
            f"--- got ---\n{clipboard_text!r}"
        )

        context.close()

    def test_copied_label_reverts_after_timeout(
        self, django_server, browser
    ):
        """After ~1.5s, the button text should revert to 'Copy'."""
        _clear_articles()
        _create_article_with_code("code-copy-demo")

        context = browser.new_context(
            viewport={"width": 1280, "height": 720}
        )
        context.grant_permissions(
            ["clipboard-read", "clipboard-write"]
        )
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/code-copy-demo",
            wait_until="domcontentloaded",
        )

        btn = page.locator('[data-testid="code-copy-btn"]').first
        btn.wait_for(state="attached", timeout=2000)
        btn.click(force=True)

        # Confirm we hit the "Copied!" state first.
        page.locator(
            '[data-testid="code-copy-btn"].is-copied'
        ).first.wait_for(state="attached", timeout=2000)

        # Then it reverts within ~2s (timeout in JS is 1500ms).
        page.wait_for_function(
            """() => {
                const b = document.querySelector(
                    '[data-testid=\\"code-copy-btn\\"]'
                );
                return b && b.textContent.trim() === 'Copy'
                    && !b.classList.contains('is-copied');
            }""",
            timeout=3000,
        )

        context.close()
