"""Responsive/theme screenshot evidence for the #1297 release-gate repair."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.manual_visual,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(__file__).parent.parent / ".tmp" / "screenshots" / "issue-1297"


def _assert_rendered(page, *, allow_form_error=False):
    assert page.locator("text=Page not found").count() == 0
    assert page.locator("text=Server Error").count() == 0
    if not allow_form_error:
        assert page.get_by_test_id("form-errors").count() == 0
    assert page.evaluate(
        "document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth + 2"
    )


def _capture(page, name, viewport, theme, *, allow_form_error=False):
    _assert_rendered(page, allow_form_error=allow_form_error)
    assert page.evaluate(
        "document.documentElement.classList.contains('dark')"
    ) is (theme == "dark")
    page.screenshot(
        path=SCREENSHOT_DIR / f"{name}-{viewport}-{theme}.png",
        full_page=True,
    )


def _dismiss_analytics(page):
    deny = page.get_by_test_id("analytics-consent-deny")
    if deny.count() and deny.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            deny.click()


def test_release_gate_changed_states_light_dark_desktop_mobile(
    django_server, browser
):
    staff_email = "release-gate-visual-1297@test.com"
    create_staff_user(staff_email)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    invalid_contacts = SCREENSHOT_DIR / "invalid-contacts.csv"
    invalid_contacts.write_text(
        "email\nnotanemail\nstillbad\n", encoding="utf-8"
    )

    for viewport, size in (
        ("desktop", {"width": 1280, "height": 900}),
        ("393", {"width": 393, "height": 852}),
    ):
        for theme in ("light", "dark"):
            context = auth_context(browser, staff_email)
            context.add_init_script(
                f"localStorage.setItem('theme', '{theme}');"
            )
            page = context.new_page()
            page.set_viewport_size(size)

            response = page.goto(
                f"{django_server}/studio/marketing-pages/new",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            _dismiss_analytics(page)
            title = page.locator('input[name="title"]')
            public_path = page.get_by_test_id("marketing-page-public-path")
            title.fill("Generated Release Guide")
            page.locator('textarea[name="description"]').click()
            expect(public_path).to_have_value("/generated-release-guide")
            _capture(page, "marketing-generated", viewport, theme)

            public_path.fill("/studio-launch-page")
            expect(public_path).to_have_value("/studio-launch-page")
            _capture(page, "marketing-manual", viewport, theme)

            public_path.fill("/events")
            page.locator('textarea[name="content_markdown"]').fill(
                "Reserved route must remain canonical."
            )
            page.get_by_test_id("marketing-page-status").select_option("published")
            page.get_by_test_id("sticky-save-action").click()
            expect(page.get_by_test_id("form-errors")).to_contain_text(
                "Public path conflicts with an existing route or reserved prefix."
            )
            expect(public_path).to_have_value("/events")
            _capture(
                page,
                "marketing-reserved-error",
                viewport,
                theme,
                allow_form_error=True,
            )

            response = page.goto(
                f"{django_server}/studio/utm-campaigns/new",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            name = page.locator('input[name="name"]')
            slug = page.locator('input[name="slug"]')
            name.fill("Generated Release Campaign")
            page.locator('textarea[name="notes"]').click()
            expect(slug).to_have_value("generated_release_campaign")
            expect(page.locator('input[name="default_utm_source"]')).to_have_value(
                "newsletter"
            )
            _capture(page, "utm-generated", viewport, theme)

            slug.fill("ai_shipping_labs_launch_april2026")
            expect(slug).to_have_value("ai_shipping_labs_launch_april2026")
            _capture(page, "utm-manual", viewport, theme)

            response = page.goto(
                f"{django_server}/studio/users/import/",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            page.locator('input[type="file"]').set_input_files(
                str(invalid_contacts)
            )
            page.locator('button[type="submit"]').first.click()
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("heading", name="Warnings (2)")).to_be_visible()
            expect(page.get_by_test_id("import-confirm-submit")).to_be_disabled()
            _capture(page, "import-invalid", viewport, theme)

            response = page.goto(
                f"{django_server}/studio/email-templates/",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            for template_name in (
                "series_registration",
                "series_update",
                "series_cancellation",
            ):
                expect(
                    page.locator(
                        f'tr:has(a[href*="/{template_name}/edit/"]) '
                        '[data-testid="template-sent-when"]'
                    )
                ).not_to_be_empty()
            _capture(page, "email-templates-list", viewport, theme)

            for template_name in (
                "series_registration",
                "series_update",
                "series_cancellation",
            ):
                response = page.goto(
                    f"{django_server}/studio/email-templates/{template_name}/edit/",
                    wait_until="domcontentloaded",
                )
                assert response is not None and response.status == 200
                expect(page.get_by_test_id("template-sent-when")).not_to_be_empty()
                expect(page.get_by_test_id("preview-status")).to_have_text(
                    "Up to date"
                )
                expect(
                    page.frame_locator('[data-testid="email-template-preview"]')
                    .locator("body")
                ).not_to_be_empty()
                _capture(page, f"email-{template_name}", viewport, theme)

            context.close()
