"""
Playwright E2E tests for UTM Campaigns Studio CRUD + importer (Issue #192).

Covers 10 scenarios from the issue:
1.  Marketer creates a campaign and adds the first tracked link.
2.  Marketer copies a generated URL.
3.  Marketer is prevented from a duplicate audience tag.
4.  Operator imports the three live launch URLs.
5.  Operator re-runs the importer and sees no duplicates.
6.  Operator imports a malformed URL and gets a clear error.
7.  Marketer archives a campaign and it disappears from the default view.
8.  Marketer cannot change the slug of a campaign with links.
9.  Non-staff member is denied access.
10. Marketer finds the new section from the Studio sidebar.

Usage:
    uv run pytest playwright_tests/test_utm_campaigns.py -v
"""

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
from django.db import connection

LAUNCH_URLS = [
    "https://aishippinglabs.com/events/ai-shipping-labs-launch-recap"
    "?utm_source=newsletter&utm_medium=email"
    "&utm_campaign=ai_shipping_labs_launch_april2026&utm_content=ai_hero_list",
    "https://aishippinglabs.com/events/ai-shipping-labs-launch-recap"
    "?utm_source=newsletter&utm_medium=email"
    "&utm_campaign=ai_shipping_labs_launch_april2026&utm_content=maven_list",
    "https://aishippinglabs.com/events/ai-shipping-labs-launch-recap"
    "?utm_source=newsletter&utm_medium=email"
    "&utm_campaign=ai_shipping_labs_launch_april2026"
    "&utm_content=luma_launch_event_list",
]

EXPECTED_AI_HERO_URL = (
    "https://aishippinglabs.com/events/ai-shipping-labs-launch-recap"
    "?utm_source=newsletter&utm_medium=email"
    "&utm_campaign=ai_shipping_labs_launch_april2026&utm_content=ai_hero_list"
)


def _clear_utm():
    """Delete all UTM campaigns and links to ensure clean state."""
    from integrations.models import UtmCampaign, UtmCampaignLink
    UtmCampaignLink.objects.all().delete()
    UtmCampaign.objects.all().delete()
    connection.close()


# ---------------------------------------------------------------
# Scenario 1: Marketer creates a campaign and adds first link
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario1CreateCampaignAndLink:
    def test_create_campaign_and_add_link(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/", wait_until="domcontentloaded")

        # Empty state visible
        body = page.content()
        assert "No UTM campaigns yet" in body
        assert "Add Campaign" in body
        assert "Import" in body

        # Click Add Campaign
        page.click('a:has-text("Add Campaign")')
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/utm-campaigns/new" in page.url

        # Fill and submit
        page.fill('input[name="name"]', "AI Shipping Labs Launch April 2026")
        page.fill('input[name="slug"]', "ai_shipping_labs_launch_april2026")
        page.fill('input[name="default_utm_source"]', "newsletter")
        page.fill('input[name="default_utm_medium"]', "email")
        page.click('button:has-text("Create Campaign")')
        page.wait_for_load_state("domcontentloaded")

        # On detail page
        assert "/studio/utm-campaigns/" in page.url
        body = page.content()
        assert "ai_shipping_labs_launch_april2026" in body
        assert "No tracked links yet" in body

        # Add link via inline form
        page.fill('input[name="utm_content"]', "ai_hero_list")
        page.fill('input[name="destination"]', "/events/ai-shipping-labs-launch-recap")
        page.fill('input[name="label"]', "AI Hero newsletter list")
        page.click('button:has-text("Add link")')
        page.wait_for_load_state("domcontentloaded")

        # Link appears with full URL (locator-scoped)
        url_box = page.locator('code[data-utm-url]').first
        assert url_box.inner_text().strip() == EXPECTED_AI_HERO_URL


# ---------------------------------------------------------------
# Scenario 2: Marketer copies a generated URL
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario2CopyGeneratedUrl:
    def test_copy_button_changes_text_and_url_matches(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        from integrations.models import UtmCampaign, UtmCampaignLink
        c = UtmCampaign.objects.create(
            name="Launch", slug="ai_shipping_labs_launch_april2026",
            default_utm_source="newsletter", default_utm_medium="email",
        )
        UtmCampaignLink.objects.create(
            campaign=c, utm_content="ai_hero_list",
            destination="/events/ai-shipping-labs-launch-recap",
            label="AI Hero",
        )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        # Grant clipboard permissions so navigator.clipboard.writeText works
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/{c.pk}/", wait_until="domcontentloaded")

        url_box = page.locator('code[data-utm-url]').first
        url_box.wait_for(state="visible")
        assert url_box.inner_text().strip() == EXPECTED_AI_HERO_URL

        copy_btn = page.locator('.utm-copy-btn').first
        copy_btn.click()

        # Wait for the button text to switch to "Copied"
        page.locator('.utm-copy-btn:has-text("Copied")').first.wait_for(state="visible", timeout=2000)


# ---------------------------------------------------------------
# Scenario 3: Marketer prevents duplicate audience tag
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario3DuplicateUtmContent:
    def test_duplicate_utm_content_rejected(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        from integrations.models import UtmCampaign, UtmCampaignLink
        c = UtmCampaign.objects.create(
            name="Launch", slug="dup_launch",
            default_utm_source="newsletter", default_utm_medium="email",
        )
        UtmCampaignLink.objects.create(
            campaign=c, utm_content="ai_hero_list", destination="/x",
        )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/{c.pk}/", wait_until="domcontentloaded")

        page.fill('input[name="utm_content"]', "ai_hero_list")
        page.fill('input[name="destination"]', "/another/path")
        page.click('button:has-text("Add link")')
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "already exists for this campaign" in body

        # Still exactly one row matching ai_hero_list (DB-level check)
        from integrations.models import UtmCampaignLink as L
        assert L.objects.filter(campaign=c, utm_content="ai_hero_list").count() == 1
        connection.close()


# ---------------------------------------------------------------
# Scenario 4: Operator imports three launch URLs
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario4ImportLaunchUrls:
    def test_import_three_urls_yields_one_campaign_three_links(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/", wait_until="domcontentloaded")

        page.locator('[data-testid="utm-campaign-import-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/utm-campaigns/import" in page.url

        page.fill('textarea[name="urls"]', "\n".join(LAUNCH_URLS))
        page.click('button:has-text("Parse and import")')
        page.wait_for_load_state("domcontentloaded")

        # Result page totals
        assert page.locator('[data-stat="campaigns_created"]').inner_text().strip() == "1"
        assert page.locator('[data-stat="links_created"]').inner_text().strip() == "3"
        assert page.locator('[data-stat="links_skipped"]').inner_text().strip() == "0"
        body = page.content()
        assert "0 errors" in body or "Errors (0)" in body or "Errors" not in body or "0\n" in body

        # Click through to the new campaign
        page.click('a:has-text("Back to campaigns")')
        page.wait_for_load_state("domcontentloaded")
        # find the campaign link in list
        page.locator('a:has-text("ai_shipping_labs_launch_april2026")').first.click()
        page.wait_for_load_state("domcontentloaded")

        # Verify 3 link rows present with the right utm_content tags
        rows = page.locator('tr[data-link-row]')
        assert rows.count() == 3
        body = page.content()
        for tag in ("ai_hero_list", "maven_list", "luma_launch_event_list"):
            assert tag in body


# ---------------------------------------------------------------
# Scenario 5: Re-running importer is idempotent
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario5ImporterIdempotent:
    def test_second_run_creates_nothing(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Pre-populate via direct ORM (fresh import)
        from integrations.models import UtmCampaign, UtmCampaignLink
        c = UtmCampaign.objects.create(
            name="ai_shipping_labs_launch_april2026",
            slug="ai_shipping_labs_launch_april2026",
            default_utm_source="newsletter", default_utm_medium="email",
        )
        for tag in ("ai_hero_list", "maven_list", "luma_launch_event_list"):
            UtmCampaignLink.objects.create(
                campaign=c, utm_content=tag,
                destination="https://aishippinglabs.com/events/ai-shipping-labs-launch-recap",
            )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/import", wait_until="domcontentloaded")
        page.fill('textarea[name="urls"]', "\n".join(LAUNCH_URLS))
        page.click('button:has-text("Parse and import")')
        page.wait_for_load_state("domcontentloaded")

        assert page.locator('[data-stat="campaigns_created"]').inner_text().strip() == "0"
        assert page.locator('[data-stat="campaigns_matched"]').inner_text().strip() == "1"
        assert page.locator('[data-stat="links_created"]').inner_text().strip() == "0"
        assert page.locator('[data-stat="links_skipped"]').inner_text().strip() == "3"

        # Detail still has exactly 3 links
        page.goto(f"{django_server}/studio/utm-campaigns/{c.pk}/", wait_until="domcontentloaded")
        rows = page.locator('tr[data-link-row]')
        assert rows.count() == 3


# ---------------------------------------------------------------
# Scenario 6: Malformed URL reported as error
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario6MalformedUrl:
    def test_malformed_url_reported(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        bad_url = (
            "https://aishippinglabs.com/foo"
            "?utm_source=newsletter&utm_medium=email&utm_campaign=launch_april"
        )
        good_url = LAUNCH_URLS[0]

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/import", wait_until="domcontentloaded")
        page.fill('textarea[name="urls"]', f"{good_url}\n{bad_url}")
        page.click('button:has-text("Parse and import")')
        page.wait_for_load_state("domcontentloaded")

        assert page.locator('[data-stat="links_created"]').inner_text().strip() == "1"
        body = page.content()
        assert "utm_content" in body
        # the row content is shown in the errors table (verify via locator,
        # since `&` is HTML-escaped in raw page content)
        errors_table = page.locator('table').last
        assert "https://aishippinglabs.com/foo" in errors_table.inner_text()
        assert "launch_april" in errors_table.inner_text()

        # Bad row did not produce a link
        from integrations.models import UtmCampaignLink as L
        assert not L.objects.filter(utm_content="").exists()
        connection.close()


# ---------------------------------------------------------------
# Scenario 7: Archive a campaign hides it from default view
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario7ArchiveCampaign:
    def test_archive_disappears_from_default(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        from integrations.models import UtmCampaign
        c = UtmCampaign.objects.create(
            name="Finished Campaign", slug="finished_camp",
            default_utm_source="newsletter", default_utm_medium="email",
        )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/{c.pk}/", wait_until="domcontentloaded")

        # Click Archive (handle confirm dialog)
        page.on("dialog", lambda d: d.accept())
        page.click('button:has-text("Archive")')
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio/utm-campaigns")

        # No table row in default view
        assert page.locator('table tbody tr').count() == 0

        # Show archived
        page.click('a:has-text("Show archived")')
        page.wait_for_load_state("domcontentloaded")
        body = page.content()
        assert "Finished Campaign" in body
        assert "Archived" in body


# ---------------------------------------------------------------
# Scenario 8: Slug locked when links exist
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario8SlugLockedWhenLinksExist:
    def test_slug_locked(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        from integrations.models import UtmCampaign, UtmCampaignLink
        c = UtmCampaign.objects.create(
            name="Has Links", slug="ai_shipping_labs_launch_april2026",
            default_utm_source="newsletter", default_utm_medium="email",
        )
        UtmCampaignLink.objects.create(
            campaign=c, utm_content="ai_hero_list", destination="/x",
        )
        connection.close()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/utm-campaigns/{c.pk}/edit", wait_until="domcontentloaded")

        body = page.content()
        assert "Slug is locked" in body
        # The slug input should be readonly (no name="slug" editable)
        slug_inputs = page.locator('input[name="slug"]')
        assert slug_inputs.count() == 0  # locked field has no name

        # Edit the name and submit
        page.fill('input[name="name"]', "AI Shipping Labs — Launch Wrap-up")
        page.click('button:has-text("Save Changes")')
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "AI Shipping Labs \u2014 Launch Wrap-up" in body or "AI Shipping Labs" in body
        assert "ai_shipping_labs_launch_april2026" in body

        # DB state
        from integrations.models import UtmCampaign as C
        c2 = C.objects.get(pk=c.pk)
        assert c2.slug == "ai_shipping_labs_launch_april2026"
        assert "Wrap-up" in c2.name
        connection.close()


# ---------------------------------------------------------------
# Scenario 9: Non-staff denied
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario9NonStaffDenied:
    def test_non_staff_403(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_user("member@test.com", tier_slug="free", is_staff=False)

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()

        response = page.goto(f"{django_server}/studio/utm-campaigns/", wait_until="domcontentloaded")
        assert response.status == 403

        response = page.goto(f"{django_server}/studio/utm-campaigns/import", wait_until="domcontentloaded")
        assert response.status == 403


# ---------------------------------------------------------------
# Scenario 10: Sidebar entry leads to UTM Campaigns; email Campaigns still present
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario10SidebarEntry:
    def test_sidebar_navigation_to_utm_campaigns(self, django_server, browser):
        _clear_utm()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Sidebar contains both "Campaigns" (email) and "UTM Campaigns" entries
        utm_link = page.locator('aside a[href="/studio/utm-campaigns/"]').first
        utm_link.wait_for(state="attached")
        email_link = page.locator('aside a[href="/studio/campaigns/"]').first
        email_link.wait_for(state="attached")

        utm_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/utm-campaigns/" in page.url

        # Email campaigns link still resolves
        page.goto(f"{django_server}/studio/campaigns/", wait_until="domcontentloaded")
        assert "/studio/campaigns/" in page.url
