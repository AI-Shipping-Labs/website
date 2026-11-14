"""CTA button markup styling and sanitization for campaign emails."""

import markdown as markdown_lib
from django.test import SimpleTestCase

from content.utils.markdown import render_email_markdown, sanitize_html
from email_app.services.campaign_message import CampaignMessage, campaign_relay_context
from email_app.services.email_buttons import (
    EMAIL_BUTTON_STYLE,
    prepare_campaign_body_markdown,
    style_markdown_buttons_for_email,
)


class StyleMarkdownButtonsForEmailTest(SimpleTestCase):
    def test_email_btn_link_gets_inline_button_style(self):
        body = (
            "Hello\n\n"
            "[Join the launch stream](https://luma.com/xey7h50c){: .email-btn }\n\n"
            "[Apply for free](https://example.com/apply){: .email-btn}\n"
        )
        styled = style_markdown_buttons_for_email(body)
        self.assertIn(
            f'[Join the launch stream](https://luma.com/xey7h50c){{: .email-btn style="{EMAIL_BUTTON_STYLE}" }}',
            styled,
        )
        self.assertIn(
            f'[Apply for free](https://example.com/apply){{: .email-btn style="{EMAIL_BUTTON_STYLE}" }}',
            styled,
        )

    def test_class_attr_extra_attrs_and_reference_links(self):
        body = (
            '[Join](https://luma.com/xey7h50c){: class="email-btn" }\n'
            '[Join](https://luma.com/xey7h50c){: .email-btn target="_blank" }\n'
            "[Apply][apply-ref]{: .email-btn }\n"
        )
        styled = style_markdown_buttons_for_email(body)
        self.assertEqual(styled.count(f'style="{EMAIL_BUTTON_STYLE}"'), 3)
        self.assertIn('target="_blank"', styled)

    def test_plain_links_explicit_style_and_fenced_code_untouched(self):
        body = (
            "[Plain link](https://example.com)\n"
            "[Other class](https://example.com){: .muted }\n"
            "[Custom](https://example.com){: .email-btn style=\"color:#000\" }\n"
            "```\n[Code](https://example.com){: .email-btn }\n```\n"
        )
        self.assertEqual(style_markdown_buttons_for_email(body), body)

    def test_empty_body(self):
        self.assertEqual(style_markdown_buttons_for_email(""), "")
        self.assertIsNone(style_markdown_buttons_for_email(None))

    def test_prepare_and_relay_context_handle_images_and_buttons(self):
        body = (
            "![Hero](https://cdn.example.com/hero.png)\n\n"
            "[Join the launch stream](https://luma.com/xey7h50c){: .email-btn }\n\n"
            "[Plain link](https://example.com)"
        )
        prepared = prepare_campaign_body_markdown(body)
        context = campaign_relay_context(CampaignMessage(subject="S", body_markdown=body))
        self.assertEqual(context["body_markdown"], prepared)

        html = markdown_lib.markdown(prepared, extensions=["attr_list"])
        self.assertIn('class="email-btn"', html)
        self.assertIn(f'style="{EMAIL_BUTTON_STYLE}"', html)
        self.assertIn('width="536"', html)
        self.assertIn('<a href="https://example.com">Plain link</a>', html)

    def test_email_btn_class_survives_sanitize_html_while_unsafe_attrs_stripped(self):
        md = (
            "[Join](https://luma.com/xey7h50c){: .email-btn }\n\n"
            '<a href="https://evil.example" style="color:red" onclick="alert(1)">Bad</a>'
        )
        cleaned = sanitize_html(render_email_markdown(md))
        self.assertIn('class="email-btn"', cleaned)
        self.assertNotIn("style=", cleaned)
        self.assertNotIn("onclick", cleaned)
