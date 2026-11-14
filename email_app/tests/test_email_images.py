"""Markdown images in campaign emails are sized to the email container."""

from django.test import SimpleTestCase

from email_app.services.campaign_message import CampaignMessage, campaign_relay_context
from email_app.services.email_images import (
    EMAIL_IMAGE_ATTRS,
    size_markdown_images_for_email,
)

HERO = "![AI Shipping Labs](https://cdn.jsdelivr.net/gh/x/y/social-preview.png)"


class SizeMarkdownImagesForEmailTest(SimpleTestCase):
    def test_attrs_scale_with_css_and_carry_outlook_width(self):
        self.assertEqual(
            EMAIL_IMAGE_ATTRS, "{: style=max-width:100%;height:auto width=536 }"
        )

    def test_inline_image_gets_sizing_attrs(self):
        body = f"Hello\n\n{HERO}\n\nMore text"
        self.assertEqual(
            size_markdown_images_for_email(body),
            f"Hello\n\n{HERO}{EMAIL_IMAGE_ATTRS}\n\nMore text",
        )

    def test_titled_linked_and_reference_images(self):
        body = (
            '![a](https://x/a.png "Title")\n'
            "[![b](https://x/b.png)](https://site)\n"
            "![c][hero]\n"
        )
        self.assertEqual(
            size_markdown_images_for_email(body),
            f'![a](https://x/a.png "Title"){EMAIL_IMAGE_ATTRS}\n'
            f"[![b](https://x/b.png){EMAIL_IMAGE_ATTRS}](https://site)\n"
            f"![c][hero]{EMAIL_IMAGE_ATTRS}\n",
        )

    def test_author_attrs_links_and_code_fences_untouched(self):
        body = (
            "![a](https://x/a.png){: width=200 }\n"
            "[not an image](https://x)\n"
            "```\n![code](https://x/c.png)\n```\n"
        )
        self.assertEqual(size_markdown_images_for_email(body), body)

    def test_empty_body(self):
        self.assertEqual(size_markdown_images_for_email(""), "")
        self.assertIsNone(size_markdown_images_for_email(None))

    def test_campaign_relay_context_sizes_images(self):
        context = campaign_relay_context(CampaignMessage(subject="S", body_markdown=HERO))
        self.assertEqual(context["body_markdown"], HERO + EMAIL_IMAGE_ATTRS)
