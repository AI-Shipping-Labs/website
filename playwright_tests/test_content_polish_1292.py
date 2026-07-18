"""Required operator journeys for Studio content polish (#1292)."""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1292")


def _staff_page(browser):
    ensure_tiers()
    create_staff_user("admin@test.com")
    return auth_context(browser, "admin@test.com").new_page()


def _clear_content_polish_data():
    from accounts.models import Token, User
    from content.models import (
        Course,
        CourseInstructor,
        Instructor,
        MarketingPage,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    from email_app.models import EmailCampaign, EmailLog
    from events.models import Event, EventRegistration
    from integrations.models import UtmCampaign, UtmCampaignLink

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    EventRegistration.objects.all().delete()
    WorkshopPage.objects.all().delete()
    WorkshopInstructor.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    CourseInstructor.objects.all().delete()
    Course.objects.all().delete()
    Instructor.objects.all().delete()
    MarketingPage.objects.all().delete()
    UtmCampaignLink.objects.all().delete()
    UtmCampaign.objects.all().delete()
    Token.objects.exclude(user__email="admin@test.com").delete()
    User.objects.exclude(email="admin@test.com").delete()
    connection.close()


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / name, full_page=True)


def test_understand_a_template(django_server, browser):
    _clear_content_polish_data()
    page = _staff_page(browser)
    page.goto(f"{django_server}/studio/email-templates/", wait_until="domcontentloaded")
    rows = page.locator("tbody tr")
    assert rows.count() > 0
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")
        assert cells.nth(2).inner_text().strip()
    key = rows.first.locator("td").first.inner_text().strip()
    sent_when = rows.first.locator("td").nth(2).inner_text().strip()
    rows.first.get_by_role("link", name="Edit").click()
    expect(page.get_by_test_id("template-sent-when")).to_contain_text(sent_when)
    expect(page.locator("h1 code")).to_have_text(key)
    _shot(page, "email-template-edit-desktop-light.png")


def test_recount_unsaved_campaign_ignores_stale_and_exposes_retry(django_server, browser):
    from events.models import Event

    _clear_content_polish_data()
    event = Event.objects.create(
        title="Recount Event 1292", slug="recount-event-1292",
        start_datetime=timezone.now() + timedelta(days=30),
    )
    event_id = str(event.pk)
    connection.close()
    page = _staff_page(browser)
    page.add_init_script("""
      window.__recounts = [];
      const nativeFetch = window.fetch.bind(window);
      window.fetch = function(url, options) {
        if (String(url).includes('/studio/campaigns/recount/')) {
          return new Promise(function(resolve, reject) {
            window.__recounts.push({resolve: resolve, reject: reject, options: options});
          });
        }
        return nativeFetch(url, options);
      };
    """)
    page.goto(f"{django_server}/studio/campaigns/new", wait_until="domcontentloaded")
    helper = page.get_by_test_id("recipient-count-helper")
    expect(helper).to_have_attribute("aria-live", "polite")
    page.locator('select[name="target_min_level"]').select_option("20")
    page.wait_for_function("window.__recounts.length === 1")
    page.get_by_test_id("target-tags-any-input").fill("priority")
    page.wait_for_function("window.__recounts.length === 2")
    page.evaluate("window.__recounts[1].resolve({ok:true,json:async()=>({recipient_count:2})})")
    expect(helper).to_contain_text("2 eligible recipients")
    page.evaluate("window.__recounts[0].resolve({ok:true,json:async()=>({recipient_count:99})})")
    expect(helper).to_contain_text("2 eligible recipients")
    page.get_by_test_id("target-tags-none-input").fill("blocked")
    page.get_by_test_id("campaign-slack-filter").select_option("yes")
    page.get_by_test_id("campaign-audience-verification").select_option("everyone")
    page.wait_for_function("window.__recounts.length === 3")
    page.evaluate("window.__recounts[2].resolve({ok:true,json:async()=>({recipient_count:1})})")
    expect(helper).to_contain_text("1 eligible recipient")
    page.get_by_test_id("campaign-target-event").select_option(event_id)
    page.wait_for_function("window.__recounts.length === 4")
    page.evaluate("window.__recounts[3].reject(new Error('deliberate failure'))")
    expect(helper).to_contain_text("Last confirmed count: 1")
    expect(helper.get_by_role("button", name="Retry")).to_be_visible()
    _shot(page, "campaign-recount-error-desktop-light.png")


def test_create_utm_prefill_manual_and_validation_rerender(django_server, browser):
    _clear_content_polish_data()
    page = _staff_page(browser)
    page.goto(f"{django_server}/studio/utm-campaigns/new", wait_until="domcontentloaded")
    source = page.get_by_label("utm_source (required)", exact=True)
    expect(source).to_have_value("newsletter")
    name = page.locator('input[name="name"]')
    slug = page.locator('input[name="slug"]')
    name.fill("July Launch: AI + Shipping")
    name.blur()
    expect(slug).to_have_value("july_launch_ai_shipping")
    slug.fill("manual_campaign_1292")
    name.fill("Changed name")
    name.blur()
    expect(slug).to_have_value("manual_campaign_1292")
    page.get_by_role("button", name="Create Campaign").click()
    expect(slug).to_have_value("manual_campaign_1292")
    expect(source).to_have_value("newsletter")


def test_create_marketing_page_prefill_manual_and_conflict(django_server, browser):
    from content.models import MarketingPage

    _clear_content_polish_data()
    MarketingPage.objects.create(title="Conflict", public_path="/manual-path-1292")
    connection.close()
    page = _staff_page(browser)
    page.goto(f"{django_server}/studio/marketing-pages/new", wait_until="domcontentloaded")
    title = page.locator('input[name="title"]')
    path = page.locator('input[name="public_path"]')
    title.fill("Marketing Page 1292")
    title.blur()
    expect(path).to_have_value("/marketing-page-1292")
    path.fill("/manual-path-1292")
    title.fill("Changed title")
    title.blur()
    expect(path).to_have_value("/manual-path-1292")
    page.get_by_role("button", name="Create Marketing Page").click()
    expect(page.locator('#marketing-page-form').get_by_text("already uses this public path", exact=False)).to_be_visible()
    expect(path).to_have_value("/manual-path-1292")


def test_share_and_revoke_workshop_draft(django_server, browser):
    from content.models import Workshop

    _clear_content_polish_data()
    workshop = Workshop.objects.create(
        slug="share-draft-1292", title="Share Draft 1292",
        description="Draft landing review copy", date=timezone.localdate() + timedelta(days=30),
        status="draft",
    )
    connection.close()
    page = _staff_page(browser)
    page.goto(f"{django_server}/studio/workshops/{workshop.pk}/", wait_until="domcontentloaded")
    preview_url = page.get_by_test_id("workshop-preview-url").input_value()
    preview = page.context.new_page()
    preview_response = preview.goto(preview_url, wait_until="domcontentloaded")
    assert preview_response.header_value("referrer-policy") == "no-referrer"
    cache_control = preview_response.header_value("cache-control") or ""
    assert "private" in cache_control
    assert "no-store" in cache_control
    expect(preview.get_by_text("Draft landing review copy")).to_be_visible()
    normal = page.context.request.get(f"{django_server}/workshops/{workshop.slug}")
    assert normal.status == 404
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("workshop-preview-regenerate").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_test_id("workshop-preview-url").input_value() != preview_url
    assert page.context.request.get(preview_url).status == 404
    _shot(page, "workshop-preview-desktop-light.png")

    workshop.status = "published"
    workshop.save(update_fields=["status"])
    connection.close()
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("workshop-public-link-panel")).to_be_visible()
    expect(page.get_by_test_id("workshop-public-open")).to_have_text("View on site")
    expect(page.get_by_text("Anyone with this private link", exact=False)).to_have_count(0)
    expect(page.get_by_text("Preview draft", exact=True)).to_have_count(0)
    _shot(page, "workshop-published-desktop-light.png")


def test_workshop_preview_preserves_gates(django_server, browser):
    from content.models import Workshop, WorkshopPage
    from events.models import Event

    _clear_content_polish_data()
    event = Event.objects.create(
        title="Private recording", slug="private-recording-1292",
        start_datetime=timezone.now() + timedelta(days=30),
        recording_url="https://youtube.com/watch?v=private1292",
        materials=[{"title": "Private material 1292", "url": "https://secret.test"}],
    )
    workshop = Workshop.objects.create(
        slug="gated-draft-1292", title="Gated Draft 1292",
        date=timezone.localdate() + timedelta(days=30),
        status="draft", pages_required_level=20, recording_required_level=30, event=event,
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug="private-page", title="Private page",
        body="PRIVATE TUTORIAL BODY 1292", sort_order=0,
    )
    preview_url = workshop.get_preview_url()
    connection.close()
    page = browser.new_page()
    page.goto(f"{django_server}{preview_url}", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Gated Draft 1292")).to_be_visible()
    body = page.content()
    assert "PRIVATE TUTORIAL BODY 1292" not in body
    assert "private1292" not in body
    assert "Private material 1292" not in body
    assert "Mark complete" not in body
    assert page.locator('meta[name="robots"]').get_attribute("content") == "noindex,nofollow,noarchive"


@pytest.mark.visual_regression
def test_tables_responsive_constraints(django_server, browser):
    from content.models import Course, Workshop

    _clear_content_polish_data()
    Course.objects.create(
        title="A long course title that keeps both actions reachable 1292",
        slug="responsive-course-1292", status="published",
    )
    Workshop.objects.create(
        slug="responsive-workshop-1292",
        title="A long workshop title that remains readable at 1280 pixels",
        date=timezone.localdate() + timedelta(days=30), status="published",
    )
    connection.close()
    page = _staff_page(browser)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")
    action_cell = page.locator('td[data-label="Actions"]').first
    assert action_cell.evaluate("el => getComputedStyle(el).whiteSpace") == "nowrap"
    expect(action_cell.get_by_role("link", name="View")).to_be_visible()
    expect(action_cell.get_by_role("link", name="Edit")).to_be_visible()
    page.goto(f"{django_server}/studio/workshops/", wait_until="domcontentloaded")
    title_cell = page.get_by_test_id("workshop-row").first.locator("td").first
    date_cell = page.locator('td[data-label="Date"]').first
    assert title_cell.evaluate("el => parseFloat(getComputedStyle(el).minWidth)") >= 256
    assert date_cell.evaluate("el => getComputedStyle(el).whiteSpace") == "nowrap"
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    _shot(page, "workshops-list-mobile-dark.png")


def test_manage_database_owned_instructors_and_public_primary(django_server, browser):
    from content.models import Course, Instructor

    _clear_content_polish_data()
    course = Course.objects.create(
        title="Instructor Course 1292", slug="instructor-course-1292", status="published"
    )
    Instructor.objects.create(instructor_id="ada-1292", name="Ada 1292", status="published")
    Instructor.objects.create(instructor_id="grace-1292", name="Grace 1292", status="published")
    connection.close()
    page = _staff_page(browser)
    edit_url = f"{django_server}/studio/courses/{course.pk}/edit#instructors"
    page.goto(edit_url, wait_until="domcontentloaded")
    select = page.locator('#course-instructor-add-id')
    select.select_option("ada-1292")
    page.locator('#course-instructor-add-position').fill("0")
    page.get_by_test_id("course-instructor-add-form").get_by_role("button", name="Add").click()
    page.wait_for_load_state("domcontentloaded")
    select = page.locator('#course-instructor-add-id')
    select.select_option("grace-1292")
    page.locator('#course-instructor-add-position').fill("0")
    page.get_by_test_id("course-instructor-add-form").get_by_role("button", name="Add").click()
    page.wait_for_load_state("domcontentloaded")
    rows = page.get_by_test_id("course-instructor-row")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text("Grace 1292")
    rows.first.locator('input[name="position"]').fill("1")
    rows.nth(1).locator('input[name="position"]').fill("0")
    page.get_by_role("button", name="Save order").click()
    page.wait_for_load_state("domcontentloaded")
    rows = page.get_by_test_id("course-instructor-row")
    expect(rows.first).to_contain_text("Ada 1292")
    page.goto(f"{django_server}/courses", wait_until="domcontentloaded")
    card = page.locator('a[href="/courses/instructor-course-1292"]')
    expect(card).to_contain_text("by Ada 1292")
    page.goto(edit_url, wait_until="domcontentloaded")
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_test_id("course-instructor-row").first.get_by_role("button", name="Remove").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("course-instructor-row")).to_have_count(1)
    expect(page.get_by_test_id("course-instructor-row").first).to_contain_text("Grace 1292")
    _shot(page, "course-instructors-desktop-light.png")


def test_source_owned_and_nonstaff_boundaries(django_server, browser):
    from content.models import Course, CourseInstructor, Instructor

    _clear_content_polish_data()
    course = Course.objects.create(
        title="Source Course 1292", slug="source-course-1292", status="published",
        source_repo="owner/repo", source_path="course.yaml",
    )
    instructor = Instructor.objects.create(
        instructor_id="source-instructor-1292", name="Source Instructor 1292"
    )
    CourseInstructor.objects.create(course=course, instructor=instructor, position=0)
    member = create_user("member-boundary-1292@example.com", tier_slug="free")
    connection.close()
    page = _staff_page(browser)
    page.goto(f"{django_server}/studio/courses/{course.pk}/edit#instructors", wait_until="domcontentloaded")
    expect(page.get_by_text("course.yaml instructors:", exact=True)).to_be_visible()
    expect(page.get_by_test_id("course-instructor-add-form")).to_have_count(0)
    member_context = auth_context(browser, member.email)
    response = member_context.request.post(
        f"{django_server}/studio/courses/{course.pk}/instructors/add",
        form={"instructor_id": "source-instructor-1292", "position": "0"},
    )
    assert response.status == 403


def test_email_log_regression_and_notifications_unchanged(django_server, browser):
    from accounts.models import User
    from email_app.models import EmailLog

    _clear_content_polish_data()
    member = User.objects.create_user(email="log-regression-1292@example.com", password="pw")
    EmailLog.objects.create(
        user=member, recipient_email=member.email, email_type="welcome",
        subject="Log regression 1292",
    )
    connection.close()
    page = _staff_page(browser)
    page.goto(
        f"{django_server}/studio/email-log/?q=log-regression-1292&kind=welcome",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_text("Log regression 1292", exact=True)).to_be_visible()
    expect(page.locator('input[name="q"]')).to_have_value("log-regression-1292")
    expect(page.locator('select[name="kind"]')).to_have_value("welcome")
    page.goto(f"{django_server}/studio/notifications/", wait_until="domcontentloaded")
    expect(page.locator('input[name="q"]')).to_have_count(0)
    expect(page.locator('select[name="kind"]')).to_have_count(0)
