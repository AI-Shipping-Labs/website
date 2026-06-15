"""Issue #989: email markdown must use the one canonical renderer.

The email body used to be parsed with ``markdown.markdown(body, extensions=
["extra"])`` — a DIFFERENT extension set than the website's
``content.utils.markdown.render_markdown``. The same source markdown therefore
rendered differently in email vs on-site (and the Studio campaign preview
inherited the divergence).

These tests pin the fix: ``render_markdown_email`` / ``render_email_markdown``
produce exactly the canonical website render of the same markdown, with only
the two browser-only features disabled for email (mermaid JS + codehilite CSS).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from content.utils.markdown import render_email_markdown, render_markdown
from email_app.services.email_service import EmailService

User = get_user_model()

# Representative markdown exercising the features that "extra" handled
# differently from the canonical set: headings, lists, tables, links,
# emphasis, inline code, and HTML escaping.
REPRESENTATIVE_MARKDOWN = """\
## Heading

Some **bold** and _italic_ text with `inline code`.

- first item
- second item

| Col A | Col B |
|-------|-------|
| 1     | 2     |

A [docs link](https://example.com/docs) and a bare https://example.org link.

Escaping check: 1 < 2 & 3 > 0.
"""


@tag('core')
class EmailMarkdownParityTest(TestCase):
    """``render_email_markdown`` matches the canonical website render."""

    def test_matches_canonical_render_without_mermaid_or_codehilite(self):
        """Email render == website render with mermaid + codehilite off.

        This is the parity contract: identical parsing for lists, tables,
        links, emphasis, and escaping — only the browser-only extensions
        differ between email and on-site.
        """
        expected = render_markdown(
            REPRESENTATIVE_MARKDOWN,
            include_mermaid=False,
            include_codehilite=False,
        )
        self.assertEqual(render_email_markdown(REPRESENTATIVE_MARKDOWN), expected)

    def test_lists_tables_links_emphasis_render_as_html(self):
        """Spot-check that the canonical features actually render, not raw."""
        html = render_email_markdown(REPRESENTATIVE_MARKDOWN)
        self.assertIn('<h2>Heading</h2>', html)
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<em>italic</em>', html)
        self.assertIn('<code>inline code</code>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<li>first item</li>', html)
        # ``tables`` extension is part of the canonical set; "extra" also had
        # it, but the point is the SAME renderer now produces it.
        self.assertIn('<table>', html)
        self.assertIn('<td>1</td>', html)
        self.assertIn('href="https://example.com/docs"', html)

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_external_links_open_in_new_tab(self):
        """External-link handling is on for email (canonical behaviour)."""
        html = render_email_markdown('[docs](https://example.com)')
        self.assertIn('target="_blank"', html)
        self.assertIn('noopener', html)

    def test_html_is_escaped_like_the_website(self):
        """Raw ``<`` / ``&`` are escaped identically to the canonical render."""
        text = 'inequality: 1 < 2 & 3'
        self.assertEqual(
            render_email_markdown(text),
            render_markdown(text, include_mermaid=False, include_codehilite=False),
        )

    def test_no_codehilite_classes_in_email_code_blocks(self):
        """Decision: codehilite OFF in email — no highlight CSS classes.

        Inboxes ship no codehilite stylesheet, so the highlight wrapper
        classes would be dead markup. Fenced code still renders as a plain
        ``<pre><code>`` block.
        """
        fenced = "```python\nprint('hi')\n```"
        html = render_email_markdown(fenced)
        self.assertNotIn('codehilite', html)
        self.assertIn('<pre>', html)
        self.assertIn('<code', html)
        self.assertIn("print('hi')", html)

    def test_no_mermaid_block_in_email(self):
        """Mermaid is disabled for email (no JS to run in an inbox)."""
        mermaid = "```mermaid\ngraph TD; A-->B;\n```"
        html = render_email_markdown(mermaid)
        # The mermaid extension emits a ``<div class="mermaid">``; with it
        # disabled the fenced block stays a code block instead.
        self.assertNotIn('class="mermaid"', html)


@tag('core')
class RenderMarkdownEmailParityTest(TestCase):
    """``EmailService.render_markdown_email`` (campaign preview path) parity."""

    def test_campaign_preview_body_matches_canonical(self):
        """Campaign preview body HTML == canonical email render of the same md.

        ``render_markdown_email`` wraps the body in the base email chrome; the
        body must be the canonical email render, proving the Studio campaign
        preview no longer diverges from the website.
        """
        service = EmailService()
        full_html = service.render_markdown_email('Subject', REPRESENTATIVE_MARKDOWN)
        expected_body = render_email_markdown(REPRESENTATIVE_MARKDOWN)
        # The rendered body fragment is embedded verbatim in the email chrome.
        self.assertIn(expected_body, full_html)

    def test_transactional_body_uses_canonical_render(self):
        """The transactional ``_render_template_with_footer`` path also matches.

        Renders a template body through the service (via a DB override so the
        source markdown is known) and asserts the body HTML equals the
        canonical email render of that same source — so transactional emails
        share the single renderer too, not a parallel ``markdown.markdown``.
        """
        from email_app.models import EmailTemplateOverride

        source = "## Hi\n\n- a\n- b\n\n[docs](https://example.com)"
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Subject',
            body_markdown=source,
        )
        user = User.objects.create_user(email='parity@example.com', first_name='Pat')

        service = EmailService()
        _subject, body_html, _footer = service._render_template_with_footer(
            'welcome', user, {},
        )

        self.assertEqual(body_html, render_email_markdown(source))
        self.assertIn('<h2>Hi</h2>', body_html)
        self.assertIn('<li>a</li>', body_html)


@tag('core')
class NoParallelMarkdownEntryPointTest(TestCase):
    """No second markdown library entry point remains for emails (#989)."""

    def test_email_modules_do_not_call_markdown_library_directly(self):
        """Grep guard: email code paths must not call ``markdown.markdown``.

        The only allowed entry point is the canonical helper in
        ``content/utils/markdown.py``. A reintroduced parallel call would let
        email markdown silently diverge from the website again.
        """
        import inspect
        import re

        from email_app.admin import email_campaign as admin_campaign
        from email_app.services import email_service
        from email_app.tasks import send_campaign
        from studio.views import email_templates

        pattern = re.compile(r'\b(markdown|md)\.markdown\s*\(')
        for module in (
            email_service,
            send_campaign,
            admin_campaign,
            email_templates,
        ):
            source = inspect.getsource(module)
            matches = pattern.findall(source)
            self.assertEqual(
                matches,
                [],
                f"{module.__name__} still calls the markdown library directly; "
                f"route email markdown through content.utils.markdown instead.",
            )
