from datetime import date

from django.test import TestCase, tag

from content.models import Article
from content.templatetags.article_images import render_article_images


def _entry(prefix="v"):
    return {
        "width": 1200,
        "height": 800,
        "source_hash": "a" * 64,
        "source_type": "image/jpeg",
        "variants": [
            {"url": f"https://cdn.example/{prefix}/320.webp", "width": 320, "height": 213, "type": "image/webp"},
            {"url": f"https://cdn.example/{prefix}/320.jpg", "width": 320, "height": 213, "type": "image/jpeg"},
            {"url": f"https://cdn.example/{prefix}/768.webp", "width": 768, "height": 512, "type": "image/webp"},
            {"url": f"https://cdn.example/{prefix}/768.jpg", "width": 768, "height": 512, "type": "image/jpeg"},
        ],
    }


def _manifest(url, inline_url):
    return {
        url: _entry("cover"),
        inline_url: _entry("inline"),
    }


@tag("core")
class ArticleResponsiveImageTemplateTest(TestCase):
    def setUp(self):
        self.url = "https://cdn.example/content/original.jpg"
        self.inline_url = "https://cdn.example/content/inline.jpg"
        self.article = Article.objects.create(
            title="Responsive & safe",
            slug="responsive-safe",
            date=date(2026, 8, 1),
            cover_image_url=self.url,
            content_markdown=f'Before\n\n![Exact alt]({self.inline_url} "Exact title")\n\nAfter',
            image_manifest=_manifest(self.url, self.inline_url),
            published=True,
        )

    def test_index_is_text_first_without_cover_media(self):
        response = self.client.get("/blog")
        self.assertContains(response, "Responsive &amp; safe")
        self.assertNotContains(response, "<picture>", html=False)
        self.assertNotContains(response, "https://cdn.example/cover/320.webp")

    def test_detail_preserves_inline_alt_title_order_and_social_original(self):
        response = self.client.get(self.article.get_absolute_url())
        body = response.content.decode()
        self.assertEqual(body.count("<picture>"), 1)
        self.assertIn('alt="Exact alt"', body)
        self.assertIn('title="Exact title"', body)
        self.assertLess(body.index("Before"), body.index("Exact alt"))
        self.assertNotEqual(body.find("After", body.index("Exact alt")), -1)
        self.assertIn('loading="lazy" decoding="async"', body)
        self.assertIn(f'property="og:image" content="{self.url}"', body)
        self.assertNotIn('property="og:image" content="https://cdn.example/v/', body)

    def test_gated_shell_has_cover_but_no_protected_inline_url(self):
        self.article.required_level = 20
        self.article.save(update_fields=["required_level"])
        response = self.client.get(self.article.get_absolute_url())
        body = response.content.decode()
        self.assertEqual(body.count("<picture>"), 0)
        self.assertNotIn("Exact alt", body)
        self.assertNotIn("https://cdn.example/inline/320.webp", body)

    def test_empty_manifest_and_external_image_keep_original_fallback(self):
        article = Article(image_manifest={})
        rendered = str(
            render_article_images(
                '<p><img src="https://outside.example/x.jpg" alt="A &amp; B"></p>',
                article,
            )
        )
        self.assertNotIn("<picture>", rendered)
        self.assertIn('src="https://outside.example/x.jpg"', rendered)
        self.assertIn('alt="A &amp; B"', rendered)
        self.assertNotIn("width=", rendered)

    def test_coverless_article_keeps_text_first_row_without_placeholder(self):
        Article.objects.create(
            title="No cover",
            slug="no-cover-responsive",
            date=date(2026, 7, 1),
        )
        response = self.client.get("/blog")
        self.assertContains(response, "No cover")
        self.assertNotContains(
            response, 'data-testid="blog-card-thumbnail-fallback"',
        )
