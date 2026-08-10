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
def test_blog_index_selects_one_priority_picture_and_preserves_crop(
    django_server,
    page,
):
    _seed_articles()
    page.set_viewport_size({"width": 1280, "height": 900})
    response = goto_with_retry(page, f"{django_server}/blog", wait_until="networkidle")
    assert response.status == 200

    pictures = page.locator('[data-testid="blog-card-thumbnail"] picture')
    assert pictures.count() == 2
    assert page.locator('img[fetchpriority="high"]').count() == 1
    assert page.locator('img[loading="lazy"]').count() >= 1
    first = pictures.first.locator("img")
    box = first.bounding_box()
    assert round(box["width"]) == 192
    assert round(box["height"]) == 128
    assert first.get_attribute("alt") == "Responsive open article"
    assert "(min-width: 640px) 12rem" in (
        pictures.first.locator("source").first.get_attribute("sizes")
    )

    page.set_viewport_size({"width": 393, "height": 851})
    page.reload(wait_until="networkidle")
    mobile = page.locator('[data-testid="blog-card-thumbnail"] picture img').first.bounding_box()
    assert abs((mobile["width"] / mobile["height"]) - (16 / 9)) < 0.03


@pytest.mark.core
def test_open_article_preserves_link_alt_title_and_intrinsic_size(
    django_server,
    page,
):
    _seed_articles()
    response = goto_with_retry(page, f"{django_server}/blog/responsive-open")
    assert response.status == 200
    assert page.locator("picture").count() == 2
    inline = page.locator(".prose picture img")
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
    assert "cover-original" in markup
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
    assert page.locator('img[src="https://outside.example/manual-cover.jpg"]').count() == 1
    assert page.locator('img[src="https://outside.example/manual-inline.jpg"]').count() == 1
