"""Public/member canonical action journeys for issue #1280."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from events.tests.test_cancel_registration_view import (
    _expired_token,
    _make_registration,
    _tampered_token,
    generate_cancel_token,
)
from playwright_tests.conftest import auth_context
from playwright_tests.test_course_cohorts import (
    _clear_courses,
    _create_cohort,
    _create_course,
    _create_user,
    _ensure_tiers,
    _future_cohort_window,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1280")


def _assert_focus(locator):
    locator.focus()
    expect(locator).to_be_focused()
    expect(locator).to_have_attribute("class", re.compile(r"\bfocus-visible:ring-2\b"))


def _set_analytics_off(context):
    context.add_cookies([
        {
            "name": "aslab_analytics_consent",
            "value": "denied",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])


@pytest.mark.django_db(transaction=True)
def test_certificate_and_roadmap_actions_keep_destinations_and_keyboard_focus(
    django_server, browser,
):
    from content.models import (
        Article,
        CourseCertificate,
        Project,
        ProjectSubmission,
        TagRule,
    )

    _clear_courses()
    _ensure_tiers()
    user = _create_user("actions-1280@example.com", tier_slug="main")
    course = _create_course(
        "Action Course", "action-course", required_level=20,
    )
    course.peer_review_enabled = True
    course.save(update_fields=["peer_review_enabled"])
    submission = ProjectSubmission.objects.create(
        user=user, course=course, project_url="https://example.com/project",
        status="certified",
    )
    certificate = CourseCertificate.objects.create(
        user=user, course=course, submission=submission,
    )
    Article.objects.create(
        title="Roadmap Actions", slug="roadmap-actions-1280",
        content_markdown="Roadmap body", tags=["roadmap-1280"], published=True,
        date=timezone.localdate(),
    )
    Project.objects.create(
        title="Roadmap Project Actions", slug="roadmap-project-actions-1280",
        content_markdown="Project roadmap body", tags=["roadmap-1280"],
        published=True, date=timezone.localdate(),
    )
    TagRule.objects.create(
        tag="roadmap-1280", component_type="roadmap_signup",
        component_config={"url": "/pricing", "cta_text": "Open roadmap"},
        position="after_content",
    )
    connection.close()

    context = auth_context(browser, user.email)
    _set_analytics_off(context)
    page = context.new_page()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses/action-course/reviews", wait_until="domcontentloaded")
    certificate_cta = page.get_by_test_id("peer-review-certificate-cta")
    _assert_focus(certificate_cta)
    expect(certificate_cta).to_have_attribute("href", certificate.get_absolute_url())
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / "certificate-light-desktop.png")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    _assert_focus(page.get_by_test_id("peer-review-certificate-cta"))
    page.screenshot(path=SCREENSHOT_DIR / "certificate-dark-desktop.png")
    certificate_cta.press("Enter")
    expect(page).to_have_url(f"{django_server}{certificate.get_absolute_url()}")

    page.goto(f"{django_server}/blog/roadmap-actions-1280", wait_until="domcontentloaded")
    roadmap = page.get_by_test_id("tag-rule-roadmap-cta")
    _assert_focus(roadmap)
    expect(roadmap).to_have_attribute("href", "/pricing")
    page.evaluate("localStorage.setItem('theme', 'light')")
    page.reload(wait_until="domcontentloaded")
    _assert_focus(page.get_by_test_id("tag-rule-roadmap-cta"))
    page.screenshot(path=SCREENSHOT_DIR / "roadmap-article-light-desktop.png")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    roadmap = page.get_by_test_id("tag-rule-roadmap-cta")
    _assert_focus(roadmap)
    page.screenshot(path=SCREENSHOT_DIR / "roadmap-dark-desktop.png")
    roadmap.press("Enter")
    expect(page).to_have_url(f"{django_server}/pricing")

    for theme in ("light", "dark"):
        page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
        page.goto(
            f"{django_server}/projects/roadmap-project-actions-1280",
            wait_until="domcontentloaded",
        )
        project_roadmap = page.get_by_test_id("tag-rule-roadmap-cta")
        _assert_focus(project_roadmap)
        expect(project_roadmap).to_have_attribute("href", "/pricing")
        page.screenshot(
            path=SCREENSHOT_DIR / f"roadmap-project-{theme}-desktop.png",
        )


@pytest.mark.django_db(transaction=True)
def test_cancellation_actions_preserve_get_keep_and_post_semantics(
    django_server, browser,
):
    from events.models import EventRegistration

    registration = _make_registration(email="cancel-actions-1280@example.com")
    registration_id = registration.pk
    token = generate_cancel_token(registration)
    expired_token = _expired_token(registration)
    tampered_token = _tampered_token(registration)
    event_url = registration.event.get_absolute_url()
    connection.close()

    context = browser.new_context(viewport={"width": 320, "height": 720})
    _set_analytics_off(context)
    page = context.new_page()
    cancel_url = f"{django_server}/events/community-lunch/cancel-registration?token={token}"
    page.goto(cancel_url, wait_until="domcontentloaded")
    page.evaluate("localStorage.setItem('theme', 'light')")
    page.reload(wait_until="domcontentloaded")
    submit = page.get_by_test_id("cancel-registration-submit")
    keep = page.get_by_test_id("cancel-registration-keep")
    _assert_focus(submit)
    _assert_focus(keep)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    )
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    submit.focus()
    page.screenshot(path=SCREENSHOT_DIR / "cancel-light-mobile-destructive-focus.png")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    keep = page.get_by_test_id("cancel-registration-keep")
    _assert_focus(keep)
    page.screenshot(path=SCREENSHOT_DIR / "cancel-dark-mobile-secondary-focus.png")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.screenshot(path=SCREENSHOT_DIR / "cancel-dark-desktop.png")
    page.evaluate("localStorage.setItem('theme', 'light')")
    page.reload(wait_until="domcontentloaded")
    page.get_by_test_id("cancel-registration-submit").focus()
    page.screenshot(path=SCREENSHOT_DIR / "cancel-light-desktop.png")
    keep.press("Enter")
    expect(page).to_have_url(f"{django_server}{event_url}")
    assert EventRegistration.objects.filter(pk=registration_id).exists()

    page.goto(cancel_url, wait_until="domcontentloaded")
    page.get_by_test_id("cancel-registration-submit").click()
    expect(page.get_by_text("has been cancelled", exact=False)).to_be_visible()
    assert not EventRegistration.objects.filter(pk=registration_id).exists()

    page.goto(cancel_url, wait_until="domcontentloaded")
    expect(page.get_by_test_id("cancel-registration-already-cancelled-destination")).to_be_visible()
    expect(page.get_by_test_id("cancel-registration-submit")).to_have_count(0)
    page.goto(
        f"{django_server}/events/community-lunch/cancel-registration?token={expired_token}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("cancel-registration-expired-destination")).to_be_visible()
    expect(page.get_by_test_id("cancel-registration-submit")).to_have_count(0)
    page.goto(
        f"{django_server}/events/community-lunch/cancel-registration?token={tampered_token}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("cancel-registration-invalid-destination")).to_be_visible()
    expect(page.get_by_test_id("cancel-registration-submit")).to_have_count(0)
    connection.close()


@pytest.mark.django_db(transaction=True)
def test_cohort_actions_keep_endpoint_behavior_and_canonical_opposite_states(
    django_server, browser,
):
    _clear_courses()
    _ensure_tiers()
    user = _create_user("cohort-actions-1280@example.com", tier_slug="main")
    course = _create_course("Cohort Actions", "cohort-actions", required_level=20)
    start, end = _future_cohort_window()
    cohort = _create_cohort(course, "Action Cohort", start, end)

    context = auth_context(browser, user.email)
    _set_analytics_off(context)
    page = context.new_page()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/courses/cohort-actions", wait_until="domcontentloaded")
    enroll = page.get_by_test_id(f"cohort-enroll-{cohort.pk}")
    _assert_focus(enroll)
    expect(enroll).to_have_attribute("data-action", "enroll")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / "cohort-enroll-light-desktop.png")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    enroll = page.get_by_test_id(f"cohort-enroll-{cohort.pk}")
    _assert_focus(enroll)
    page.screenshot(path=SCREENSHOT_DIR / "cohort-enroll-dark-desktop.png")
    enroll.click()
    unenroll = page.get_by_test_id(f"cohort-unenroll-{cohort.pk}")
    expect(unenroll).to_be_visible(timeout=10000)
    _assert_focus(unenroll)
    expect(unenroll).to_have_attribute("data-action", "unenroll")
    page.evaluate("localStorage.setItem('theme', 'light')")
    page.reload(wait_until="domcontentloaded")
    unenroll = page.get_by_test_id(f"cohort-unenroll-{cohort.pk}")
    _assert_focus(unenroll)
    page.screenshot(path=SCREENSHOT_DIR / "cohort-unenroll-light-desktop.png")
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    unenroll = page.get_by_test_id(f"cohort-unenroll-{cohort.pk}")
    _assert_focus(unenroll)
    page.screenshot(path=SCREENSHOT_DIR / "cohort-unenroll-dark-desktop.png")
    unenroll.click()
    enroll = page.get_by_test_id(f"cohort-enroll-{cohort.pk}")
    expect(enroll).to_be_visible(timeout=10000)
    page.set_viewport_size({"width": 320, "height": 720})
    page.evaluate("localStorage.setItem('theme', 'light')")
    page.reload(wait_until="domcontentloaded")
    enroll = page.get_by_test_id(f"cohort-enroll-{cohort.pk}")
    _assert_focus(enroll)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=SCREENSHOT_DIR / "cohort-enroll-light-mobile.png")
    enroll.click()
    expect(page.get_by_test_id(f"cohort-unenroll-{cohort.pk}")).to_be_visible(timeout=10000)
    page.evaluate("localStorage.setItem('theme', 'dark')")
    page.reload(wait_until="domcontentloaded")
    unenroll = page.get_by_test_id(f"cohort-unenroll-{cohort.pk}")
    _assert_focus(unenroll)
    page.screenshot(path=SCREENSHOT_DIR / "cohort-unenroll-dark-mobile.png")

    for theme in ("light", "dark"):
        page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        header = page.get_by_test_id("dashboard-header")
        onboarding = page.get_by_test_id("onboarding-prompt")
        onboarding_cta = page.get_by_test_id("onboarding-prompt-cta")
        expect(header).to_be_visible()
        expect(onboarding).to_be_visible()
        expect(onboarding_cta).to_have_attribute("href", "/onboarding/")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(
            path=SCREENSHOT_DIR / f"dashboard-header-{theme}-mobile.png",
        )
        onboarding.scroll_into_view_if_needed()
        _assert_focus(onboarding_cta)
        page.screenshot(
            path=SCREENSHOT_DIR / f"dashboard-onboarding-{theme}-mobile.png",
        )
    with page.expect_request(f"{django_server}/onboarding/") as navigation:
        onboarding_cta.press("Enter")
    assert navigation.value.url == f"{django_server}/onboarding/"
