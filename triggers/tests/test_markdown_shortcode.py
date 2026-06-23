"""Tests for the eventwidget markdown shortcode (issue #1070)."""

from django.test import TestCase, tag

from content.utils.markdown import render_markdown, sanitize_html


@tag("core")
class EventWidgetShortcodeTest(TestCase):
    def test_shortcode_expands_to_placeholder_div(self):
        html = render_markdown("```eventwidget\nslug: v0-claim\n```")
        self.assertIn('data-event-widget="v0-claim"', html)
        self.assertIn('class="event-widget"', html)

    def test_placeholder_not_wrapped_in_paragraph(self):
        html = render_markdown("```eventwidget\nslug: v0-claim\n```")
        # The stashed placeholder is its own block, not <p>-wrapped.
        self.assertNotIn("<p><div", html)

    def test_raw_shortcode_text_does_not_leak(self):
        html = render_markdown("```eventwidget\nslug: v0-claim\n```")
        self.assertNotIn("eventwidget", html)
        self.assertNotIn("slug:", html)

    def test_shortcode_inside_surrounding_markdown(self):
        md = "# Heading\n\nSome text.\n\n```eventwidget\nslug: v0-claim\n```\n\nMore."
        html = render_markdown(md)
        self.assertIn("<h1>Heading</h1>", html)
        self.assertIn('data-event-widget="v0-claim"', html)
        self.assertIn("More.", html)

    def test_missing_slug_renders_nothing(self):
        html = render_markdown("```eventwidget\nfoo: bar\n```")
        self.assertNotIn("event-widget", html)
        self.assertNotIn("eventwidget", html)

    def test_slug_is_slugified(self):
        html = render_markdown("```eventwidget\nslug: V0 Claim!\n```")
        self.assertIn('data-event-widget="v0-claim"', html)

    def test_sanitizer_preserves_data_event_widget_attribute(self):
        # Article surfaces run rendered HTML back through sanitize_html; the
        # hydration hook must survive (issue #1070).
        rendered = render_markdown("```eventwidget\nslug: v0-claim\n```")
        cleaned = sanitize_html(rendered)
        self.assertIn('data-event-widget="v0-claim"', cleaned)

    def test_email_render_drops_the_widget(self):
        from content.utils.markdown import render_email_markdown

        html = render_email_markdown("```eventwidget\nslug: v0-claim\n```")
        self.assertNotIn("event-widget", html)
