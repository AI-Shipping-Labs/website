"""Browser journeys for responsive Article images (issue #1385)."""

import datetime
import os

import pytest

from playwright_tests.conftest import goto_with_retry

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _entry(prefix):
    return {
        "width": 1200,
        "height": 800,
        "source_hash": prefix * 64,
        "source_type": "image/png",
        "variants": [
            {
                "url": f"/static/placeholder-logo.png?{prefix}-320-webp",
                "width": 320,
                "height": 213,
                "type": "image/webp",
            },
            {
                "url": f"/static/placeholder-logo.png?{prefix}-320-png",
                "width": 320,
                "height": 213,
                "type": "image/png",
            },
            {
                "url": f"/static/placeholder-logo.png?{prefix}-768-webp",
                "width": 768,
                "height": 512,
                "type": "image/webp",
            },
            {
                "url": f"/static/placeholder-logo.png?{prefix}-768-png",
                "width": 768,
                "height": 512,
                "type": "image/png",
            },
        ],
    }


def _seed_articles():
    from content.models import Article

    Article.objects.all().delete()
    cover = "/static/placeholder-logo.png?cover-original"
    inline = "/static/placeholder-logo.png?protected-inline-original"
    common = {
        "date": datetime.date(2026, 8, 1),
        "cover_image_url": cover,
        "content_markdown": (
            "Before image.\n\n"
            f'[![Inline exact alt]({inline} "Inline exact title")]'
            "(https://example.com/destination)\n\n"
            "After image."
        ),
        "image_manifest": {cover: _entry("c"), inline: _entry("i")},
    }
    open_article = Article.objects.create(
        title="Responsive open article",
        slug="responsive-open",
        **common,
    )
    gated = Article.objects.create(
        title="Responsive gated article",
        slug="responsive-gated",
        required_level=20,
        **common,
    )
    draft = Article.objects.create(
        title="External draft image",
        slug="external-draft-image",
        published=False,
        cover_image_url="https://outside.example/manual-cover.jpg",
        content_markdown=("![Manual inline](https://outside.example/manual-inline.jpg)"),
        image_manifest={},
        date=datetime.date(2026, 8, 1),
    )
    connection.close()
    return open_article, gated, draft


@pytest.mark.core
def test_blog_index_is_text_first_without_cover_media(
    django_server,
    page,
):
    _seed_articles()
    response = goto_with_retry(page, f"{django_server}/blog")
    assert response.status == 200

    cards = page.get_by_test_id("blog-card")
    assert cards.count() == 2
    assert cards.filter(has_text="Responsive open article").count() == 1
    assert cards.filter(has_text="Responsive gated article").count() == 1
    assert page.get_by_test_id("blog-card-thumbnail").count() == 0
    assert cards.locator("picture, img").count() == 0
    assert "cover-original" not in page.content()


@pytest.mark.core
def test_open_article_preserves_link_alt_title_and_intrinsic_size(
    django_server,
    page,
):
    _seed_articles()
    response = goto_with_retry(page, f"{django_server}/blog/responsive-open")
    assert response.status == 200
    assert page.locator("picture").count() == 1
    assert "cover-original" not in page.locator("main").inner_html()
    picture = page.locator(".prose picture")
    assert picture.count() == 1
    sources = picture.locator("source")
    assert sources.count() == 2
    assert sources.nth(0).get_attribute("type") == "image/webp"
    assert sources.nth(1).get_attribute("type") == "image/png"
    assert sources.nth(0).get_attribute("srcset") == (
        "/static/placeholder-logo.png?i-320-webp 320w, "
        "/static/placeholder-logo.png?i-768-webp 768w"
    )
    assert sources.nth(0).get_attribute("sizes") == (
        "(min-width: 768px) 48rem, calc(100vw - 2rem)"
    )
    inline = picture.locator("img")
    assert inline.get_attribute("alt") == "Inline exact alt"
    assert inline.get_attribute("title") == "Inline exact title"
    assert inline.get_attribute("width") == "1200"
    assert inline.get_attribute("height") == "800"
    assert inline.get_attribute("loading") == "lazy"
    assert inline.locator("xpath=ancestor::a").count() == 1
    content = page.locator(".prose").inner_text()
    assert content.index("Before image.") < content.index("After image.")


@pytest.mark.core
def test_gated_shell_never_exposes_protected_inline_reference(
    django_server,
    page,
):
    _seed_articles()
    requested = []
    page.on("request", lambda request: requested.append(request.url))
    response = goto_with_retry(page, f"{django_server}/blog/responsive-gated")
    assert response.status == 200
    markup = page.content()
    social_image = page.locator('meta[property="og:image"]')
    assert "cover-original" in social_image.get_attribute("content")
    assert page.locator('main img[src*="cover-original"]').count() == 0
    assert "protected-inline-original" not in markup
    assert not any("protected-inline" in url for url in requested)
    assert page.locator(".prose").count() == 0


@pytest.mark.core
def test_draft_external_images_remain_original_fallbacks(django_server, page):
    _, _, draft = _seed_articles()
    response = goto_with_retry(page, f"{django_server}{draft.get_preview_url()}")
    assert response.status == 200
    assert page.get_by_test_id("draft-preview-banner").is_visible()
    assert page.locator("picture").count() == 0
    assert page.locator('img[src="https://outside.example/manual-cover.jpg"]').count() == 0
    inline = page.locator('img[src="https://outside.example/manual-inline.jpg"]')
    assert inline.count() == 1
    assert inline.get_attribute("alt") == "Manual inline"
    assert inline.get_attribute("loading") == "lazy"
    assert inline.get_attribute("decoding") == "async"
    assert inline.get_attribute("width") is None
    assert inline.get_attribute("height") is None
