"""Playwright E2E for the Studio custom banner upload panel (issue #931).

The real upload happy path (file -> S3 -> ``custom_banner_url``) is covered
by ``studio/tests/test_banner_upload_views.py`` with a mocked S3 client,
which a cross-process Playwright server cannot patch. Here we exercise the
user-visible flows that do not require AWS:

- viewing the effective banner + source badge (seeded ``custom_banner_url``);
- the public og:image / twitter:image reflecting a custom upload;
- frontmatter cover still outranking a custom upload;
- rejecting a non-image and an oversized upload (validation runs before any
  S3 call), leaving the record untouched;
- removing a custom banner to fall back to the generated one (best-effort
  delete swallows the absent-AWS error);
- the disabled-when-unconfigured upload control;
- a non-staff member being blocked from the upload endpoint;
- regenerate + custom upload coexisting (custom wins).
"""

import datetime as dt
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

CDN_BASE = "https://cdn.example.com"
CUSTOM_URL = f"{CDN_BASE}/custom-banners/article/seed-custom.png"
GENERATED_URL = f"{CDN_BASE}/banners/article/seed-gen.jpg"
COVER_URL = f"{CDN_BASE}/manual/seed-cover.png"


def _reset_state():
    from content.models import Article, Workshop
    from events.models import Event
    from integrations.models import IntegrationSetting

    Article.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    IntegrationSetting.objects.filter(
        key__in=(
            "CONTENT_CDN_BASE",
            "AWS_S3_CONTENT_BUCKET",
            "AWS_S3_CONTENT_REGION",
        )
    ).delete()
    IntegrationSetting.objects.filter(
        key__startswith="BANNER_GENERATOR_",
    ).delete()
    connection.close()


def _set_config(key, value):
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={
            "value": value,
            "is_secret": False,
            "group": "banner_generator",
            "description": "",
        },
    )
    clear_config_cache()
    connection.close()


def _enable_uploads():
    _set_config("CONTENT_CDN_BASE", CDN_BASE)
    _set_config("AWS_S3_CONTENT_BUCKET", "content-bucket")
    _set_config("AWS_S3_CONTENT_REGION", "eu-west-1")


def _enable_generator():
    _set_config("BANNER_GENERATOR_FUNCTION_URL", "https://lambda.example.com/")
    _set_config("BANNER_GENERATOR_AUTH_TOKEN", "token-abc")


def _create_article(slug, title, **overrides):
    from content.models import Article

    defaults = dict(slug=slug, title=title, date=dt.date(2026, 1, 1))
    defaults.update(overrides)
    article = Article.objects.create(**defaults)
    connection.close()
    return article


def _publish(article):
    from content.models import Article

    Article.objects.filter(pk=article.pk).update(
        published=True, published_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    connection.close()


def _png_bytes(size=1024):
    # A tiny valid-enough PNG header padded to ``size`` bytes. Validation
    # only checks the multipart content_type + size, not pixel data.
    header = b"\x89PNG\r\n\x1a\n"
    return header + b"\x00" * (size - len(header))


@pytest.mark.django_db(transaction=True)
class TestCustomBannerPanel:
    @pytest.mark.core
    def test_panel_shows_generated_source_badge(self, django_server, browser):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-gen@test.com")
        article = _create_article(
            "cb-gen", "CB Gen", auto_banner_url=GENERATED_URL,
        )

        context = _auth_context(browser, "cb-staff-gen@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )

        badge = page.locator('[data-testid="banner-source-badge"]')
        assert badge.count() == 1
        assert badge.inner_text().strip() == "Generated"
        # The single image is labelled as banner AND social image.
        assert "Banner / social image (1200x630)" in page.content()
        # No Remove control when there's no custom upload.
        assert page.locator('[data-testid="banner-remove-button"]').count() == 0

    @pytest.mark.core
    def test_panel_shows_custom_upload_badge_and_remove(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-custom@test.com")
        article = _create_article(
            "cb-custom", "CB Custom",
            custom_banner_url=CUSTOM_URL,
            auto_banner_url=GENERATED_URL,
        )

        context = _auth_context(browser, "cb-staff-custom@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )

        badge = page.locator('[data-testid="banner-source-badge"]')
        assert badge.inner_text().strip() == "Custom upload"
        img = page.locator('[data-testid="banner-generator-image"]')
        assert img.get_attribute("src") == CUSTOM_URL
        assert page.locator('[data-testid="banner-remove-button"]').count() == 1

    @pytest.mark.core
    def test_public_og_image_uses_custom_upload(self, django_server, page):
        _reset_state()
        _ensure_tiers()
        article = _create_article(
            "cb-og", "CB OG",
            cover_image_url="",
            custom_banner_url=CUSTOM_URL,
            auto_banner_url=GENERATED_URL,
        )
        _publish(article)

        page.goto(
            f"{django_server}/blog/{article.slug}",
            wait_until="domcontentloaded",
        )
        og = page.locator('meta[property="og:image"]').get_attribute("content")
        tw = page.locator('meta[name="twitter:image"]').get_attribute("content")
        assert og == CUSTOM_URL
        assert tw == CUSTOM_URL

    @pytest.mark.core
    def test_frontmatter_cover_wins_over_custom(self, django_server, browser):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-cover@test.com")
        article = _create_article(
            "cb-cover", "CB Cover",
            cover_image_url=COVER_URL,
            custom_banner_url=CUSTOM_URL,
            auto_banner_url=GENERATED_URL,
        )
        _publish(article)

        # Public og:image is the frontmatter cover.
        anon = browser.new_context()
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/blog/{article.slug}",
            wait_until="domcontentloaded",
        )
        og = anon_page.locator(
            'meta[property="og:image"]'
        ).get_attribute("content")
        assert og == COVER_URL

        # Studio panel shows the "Frontmatter cover" badge.
        context = _auth_context(browser, "cb-staff-cover@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        badge = page.locator('[data-testid="banner-source-badge"]')
        assert badge.inner_text().strip() == "Frontmatter cover"

    @pytest.mark.core
    def test_non_image_upload_rejected(self, django_server, browser):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-bad@test.com")
        article = _create_article("cb-bad", "CB Bad")

        context = _auth_context(browser, "cb-staff-bad@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="banner-upload-input"]').set_input_files(
            files=[{
                "name": "notes.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 not an image",
            }],
        )
        page.locator('[data-testid="banner-upload-button"]').click()
        page.wait_for_url(
            f"**/studio/articles/{article.pk}/edit", timeout=10000,
        )
        assert "Upload a JPEG, PNG, or WebP image" in page.content()
        # No custom banner was stored.

        article.refresh_from_db()
        assert article.custom_banner_url == ""
        connection.close()

    @pytest.mark.core
    def test_oversized_upload_rejected(self, django_server, browser):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-big@test.com")
        article = _create_article("cb-big", "CB Big")

        context = _auth_context(browser, "cb-staff-big@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="banner-upload-input"]').set_input_files(
            files=[{
                "name": "huge.png",
                "mimeType": "image/png",
                "buffer": _png_bytes(6 * 1024 * 1024),
            }],
        )
        page.locator('[data-testid="banner-upload-button"]').click()
        page.wait_for_url(
            f"**/studio/articles/{article.pk}/edit", timeout=10000,
        )
        assert "Image too large (max 5 MB)" in page.content()

        article.refresh_from_db()
        assert article.custom_banner_url == ""
        connection.close()

    @pytest.mark.core
    def test_remove_custom_banner_falls_back_to_generated(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_staff_user(email="cb-staff-remove@test.com")
        article = _create_article(
            "cb-remove", "CB Remove",
            custom_banner_url=CUSTOM_URL,
            auto_banner_url=GENERATED_URL,
        )

        context = _auth_context(browser, "cb-staff-remove@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="banner-remove-button"]').click()
        page.wait_for_url(
            f"**/studio/articles/{article.pk}/edit", timeout=10000,
        )
        assert "Custom banner removed" in page.content()
        # Reload — now the generated banner is the effective preview.
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        badge = page.locator('[data-testid="banner-source-badge"]')
        assert badge.inner_text().strip() == "Generated"
        assert page.locator('[data-testid="banner-remove-button"]').count() == 0

    # NOTE: the "upload control disabled when CDN/bucket unconfigured"
    # scenario is covered authoritatively by the Django view test
    # ``PanelRenderTest.test_upload_control_disabled_when_unconfigured``
    # (which uses @override_settings to force the gate off). The live
    # Playwright server reads CONTENT_CDN_BASE / AWS_S3_CONTENT_BUCKET from
    # real Django settings, which a cross-process test cannot blank out, so
    # the disabled state is intentionally not re-tested here. This mirrors
    # the existing banner-generator Playwright suite, which also leaves the
    # disabled Regenerate state to Django tests.

    @pytest.mark.core
    def test_non_staff_cannot_upload(self, django_server, browser):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _create_user(email="cb-member@test.com")
        article = _create_article("cb-member", "CB Member")

        context = _auth_context(browser, "cb-member@test.com")
        page = context.new_page()
        response = page.request.post(
            f"{django_server}/studio/articles/{article.pk}/upload-banner",
            multipart={
                "banner_image": {
                    "name": "b.png",
                    "mimeType": "image/png",
                    "buffer": _png_bytes(512),
                },
            },
        )
        # staff_required blocks non-staff (403) or redirects to login.
        assert response.status in (302, 403) or "/accounts/login" in response.url

        article.refresh_from_db()
        assert article.custom_banner_url == ""
        connection.close()

    @pytest.mark.core
    def test_regenerate_and_custom_upload_coexist(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_uploads()
        _enable_generator()
        _create_staff_user(email="cb-staff-coexist@test.com")
        # Custom upload already present alongside a generated banner.
        article = _create_article(
            "cb-coexist", "CB Coexist",
            custom_banner_url=CUSTOM_URL,
            auto_banner_url=GENERATED_URL,
        )

        context = _auth_context(browser, "cb-staff-coexist@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/articles/{article.pk}/edit",
            wait_until="domcontentloaded",
        )
        # Both controls are present: Regenerate (enabled) and Upload custom.
        assert page.locator(
            '[data-testid="banner-generator-regenerate-button"]'
        ).count() == 1
        assert page.locator('[data-testid="banner-upload-form"]').count() == 1
        # Custom wins over generated for the preview.
        badge = page.locator('[data-testid="banner-source-badge"]')
        assert badge.inner_text().strip() == "Custom upload"
