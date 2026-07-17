"""Live mobile route matrix for the Studio detail-header migration (#1275)."""

import datetime
import os
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1275")
ACTIONLESS = {
    "worker-task",
    "worker-inspect",
    "ses-detail",
    "response-detail",
    "event-create",
    "marketing-create",
    "sprint-create",
    "redirect-create",
    "persona-create",
    "questionnaire-create",
    "utm-create",
}
ROUTES = (
    "plan-detail",
    "sprint-detail",
    "crm-detail",
    "series-detail",
    "campaign-detail",
    "workshop-detail",
    "utm-detail",
    "persona-detail",
    "questionnaire-detail",
    "project-review",
    "event-form",
    "article-form",
    "course-form",
    "marketing-form",
    "workshop-form",
    "download-form",
    "recording-form",
    "sprint-form",
    "redirect-form",
    "persona-form",
    "questionnaire-form",
    "utm-form",
    "utm-link-form",
    "unit-form",
    "peer-reviews",
    "plan-editor",
    "worker-task",
    "worker-inspect",
    "ses-detail",
    "response-detail",
    "event-create",
    "marketing-create",
    "sprint-create",
    "redirect-create",
    "persona-create",
    "questionnaire-create",
    "utm-create",
)


def _member(email):
    ensure_tiers()
    return create_user(email, tier_slug="free", email_verified=True)


def _base_records(member_email):
    from accounts.models import User
    from plans.models import Plan, Sprint

    _member(member_email)
    member = User.objects.get(email=member_email)
    sprint = Sprint.objects.create(
        name="Header matrix sprint",
        slug=f"header-matrix-{member.pk}",
        start_date=timezone.localdate(),
        duration_weeks=4,
    )
    plan = Plan.objects.create(member=member, sprint=sprint, title="Ship a compact header")
    return member, sprint, plan


def _seed_route(key, member_email):
    member, sprint, plan = _base_records(member_email)
    create_routes = {
        "event-create": "/studio/events/new",
        "marketing-create": "/studio/marketing-pages/new",
        "sprint-create": "/studio/sprints/new",
        "redirect-create": "/studio/redirects/new",
        "persona-create": "/studio/personas/new",
        "questionnaire-create": "/studio/questionnaires/new",
        "utm-create": "/studio/utm-campaigns/new",
    }
    if key in create_routes:
        return create_routes[key]
    if key == "plan-detail":
        return f"/studio/plans/{plan.pk}/"
    if key == "sprint-detail":
        return f"/studio/sprints/{sprint.pk}/"
    if key == "sprint-form":
        return f"/studio/sprints/{sprint.pk}/edit"
    if key == "plan-editor":
        return f"/studio/plans/{plan.pk}/edit/"
    if key == "crm-detail":
        from crm.models import CRMRecord

        return f"/studio/crm/{CRMRecord.objects.create(user=member).pk}/"
    if key in {"series-detail", "event-form", "recording-form"}:
        from events.models import Event, EventSeries

        series = EventSeries.objects.create(
            name="Header series", slug=f"header-series-{member.pk}", start_time=datetime.time(18)
        )
        event = Event.objects.create(
            title="Header event",
            slug=f"header-event-{member.pk}",
            start_datetime=timezone.now() + datetime.timedelta(days=7),
            status="upcoming",
            event_series=series,
        )
        if key == "series-detail":
            return f"/studio/event-series/{series.pk}/"
        if key == "event-form":
            return f"/studio/events/{event.pk}/edit"
        event.status, event.recording_url = "completed", "https://example.test/recording"
        event.save(update_fields=["status", "recording_url"])
        return f"/studio/recordings/{event.pk}/edit"
    if key == "campaign-detail":
        from email_app.models import EmailCampaign

        return f"/studio/campaigns/{EmailCampaign.objects.create(subject='Header campaign', body='Body').pk}/"
    if key in {"workshop-detail", "workshop-form"}:
        from content.models import Workshop

        workshop = Workshop.objects.create(
            title="Header workshop", slug=f"header-workshop-{member.pk}", date=timezone.localdate(), status="published"
        )
        return f"/studio/workshops/{workshop.pk}/" + ("edit" if key == "workshop-form" else "")
    if key in {"utm-detail", "utm-form", "utm-link-form"}:
        from integrations.models import UtmCampaign, UtmCampaignLink

        campaign = UtmCampaign.objects.create(
            name="Header UTM",
            slug=f"header_utm_{member.pk}",
            default_utm_source="newsletter",
            default_utm_medium="email",
            notes="",
        )
        if key == "utm-detail":
            return f"/studio/utm-campaigns/{campaign.pk}/"
        if key == "utm-form":
            return f"/studio/utm-campaigns/{campaign.pk}/edit"
        link = UtmCampaignLink.objects.create(
            campaign=campaign,
            utm_content="header",
            destination="/events",
            utm_term="",
            utm_source="",
            utm_medium="",
            label="Header",
        )
        return f"/studio/utm-campaigns/{campaign.pk}/links/{link.pk}/edit"
    if key in {"persona-detail", "persona-form"}:
        from questionnaires.models import Persona

        persona = Persona.objects.create(name="Header persona", archetype="Builder", slug=f"header-persona-{member.pk}")
        return f"/studio/personas/{persona.pk}/" + ("edit" if key == "persona-form" else "")
    if key in {"questionnaire-detail", "questionnaire-form", "response-detail"}:
        from questionnaires.models import Questionnaire, Response

        questionnaire = Questionnaire.objects.create(
            title="Header questionnaire", slug=f"header-questionnaire-{member.pk}"
        )
        if key == "questionnaire-detail":
            return f"/studio/questionnaires/{questionnaire.pk}/"
        if key == "questionnaire-form":
            return f"/studio/questionnaires/{questionnaire.pk}/edit"
        response = Response.objects.create(questionnaire=questionnaire, respondent=member)
        return f"/studio/questionnaires/{questionnaire.pk}/responses/{response.pk}/"
    if key == "project-review":
        from content.models import Project

        project = Project.objects.create(
            title="Header project",
            slug=f"header-project-{member.pk}",
            date=timezone.localdate(),
            status="pending_review",
            published=False,
        )
        return f"/studio/projects/{project.pk}/review"
    if key == "article-form":
        from content.models import Article

        article = Article.objects.create(
            title="Header article",
            slug=f"header-article-{member.pk}",
            date=timezone.localdate(),
            source_repo="AI-Shipping-Labs/content",
            source_path="articles/header.md",
        )
        return f"/studio/articles/{article.pk}/edit"
    if key in {"course-form", "unit-form", "peer-reviews"}:
        from content.models import Course, Module, Unit

        course = Course.objects.create(title="Header course", slug=f"header-course-{member.pk}")
        if key == "course-form":
            return f"/studio/courses/{course.pk}/edit"
        if key == "peer-reviews":
            return f"/studio/courses/{course.pk}/peer-reviews"
        module = Module.objects.create(course=course, title="Header module", slug="header-module")
        unit = Unit.objects.create(module=module, title="Header unit", slug="header-unit")
        return f"/studio/units/{unit.pk}/edit"
    if key == "marketing-form":
        from content.models import MarketingPage

        page = MarketingPage.objects.create(title="Header marketing", public_path=f"/header-marketing-{member.pk}")
        return f"/studio/marketing-pages/{page.pk}/edit"
    if key == "download-form":
        from content.models import Download

        obj = Download.objects.create(
            title="Header download", slug=f"header-download-{member.pk}", file_url="https://example.test/file.pdf"
        )
        return f"/studio/downloads/{obj.pk}/edit"
    if key == "redirect-form":
        from integrations.models import Redirect

        obj = Redirect.objects.create(source_path=f"/old-{member.pk}", target_path="/new")
        return f"/studio/redirects/{obj.pk}/edit"
    if key == "worker-task":
        from django_q.models import Task

        task = Task.objects.create(
            id=uuid.uuid4().hex,
            name="Header task",
            func="tests.fake",
            started=timezone.now(),
            stopped=timezone.now(),
            success=True,
            result="ok",
        )
        return f"/studio/worker/task/{task.id}/"
    if key == "worker-inspect":
        from django_q.models import OrmQ
        from django_q.signing import SignedPackage

        payload = SignedPackage.dumps(
            {"id": uuid.uuid4().hex, "name": "Queued header task", "func": "tests.fake", "args": (), "kwargs": {}}
        )
        task = OrmQ.objects.create(key="default", payload=payload)
        return f"/studio/worker/queue/{task.pk}/inspect/"
    if key == "ses-detail":
        from email_app.models import SesEvent

        event = SesEvent.objects.create(
            event_type="bounce_transient", message_id=uuid.uuid4().hex, raw_payload={}, recipient_email=member.email
        )
        return f"/studio/ses-events/{event.pk}/"
    raise AssertionError(key)


def _assert_no_overflow(page):
    widths = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert widths[0] <= widths[1] + 2, widths


def _tab_to_and_assert_canonical_ring(page, selector):
    page.locator("body").click(position={"x": 1, "y": 1})
    for _ in range(100):
        page.keyboard.press("Tab")
        if page.evaluate("selector => document.activeElement?.matches(selector)", selector):
            break
    else:
        raise AssertionError(f"Tab order never reached {selector}")
    styles = page.locator(selector).evaluate(
        "el => { const style = getComputedStyle(el); return {"
        " outlineColor: style.outlineColor, boxShadow: style.boxShadow"
        "}; }"
    )
    assert styles["outlineColor"] in {"rgba(0, 0, 0, 0)", "transparent"}, styles
    assert styles["boxShadow"] != "none", styles


@pytest.mark.parametrize("route_key", ROUTES)
def test_primary_route_matrix_is_title_first_and_mobile_safe(route_key, django_server, browser):
    staff_email = f"header-{route_key}@test.com"
    create_staff_user(staff_email)
    path = _seed_route(route_key, f"member-{route_key}@test.com")
    connection.close()
    context = auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size({"width": 393, "height": 851})
    response = page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    header = (
        page.get_by_test_id("peer-review-course-header")
        if route_key == "peer-reviews"
        else page.locator("header[data-testid]").first
    )
    assert header.locator("h1").is_visible()
    actions = page.get_by_test_id("studio-header-actions")
    if route_key in ACTIONLESS:
        assert actions.count() == 0
    elif actions.count():
        assert header.locator("h1").evaluate(
            "(title, actions) => Boolean(title.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING)",
            actions.element_handle(),
        )
    if page.get_by_test_id("studio-header-overflow").count():
        page.get_by_label("More actions").click()
        for item in page.get_by_test_id("studio-header-overflow").locator(":scope > div > *").all():
            assert item.bounding_box()["height"] >= 44
    _assert_no_overflow(page)
    context.close()


def test_representative_light_dark_desktop_mobile_screenshots(django_server, browser):
    staff_email = "header-screenshots@test.com"
    create_staff_user(staff_email)
    paths = {
        key: _seed_route(key, f"screens-{key}@test.com")
        for key in (
            "plan-detail",
            "plan-editor",
            "sprint-detail",
            "campaign-detail",
            "crm-detail",
            "series-detail",
            "article-form",
            "ses-detail",
        )
    }
    paths["dashboard-worker-section"] = "/studio/"
    connection.close()
    context = auth_context(browser, staff_email)
    page = context.new_page()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    analytics_off = page.get_by_role("button", name="Keep analytics off")
    if analytics_off.count() and analytics_off.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            analytics_off.click()
    for theme in ("light", "dark"):
        page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
        for width, height, label in ((1280, 900, "desktop"), (393, 851, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            for key, path in paths.items():
                response = page.goto(f"{django_server}{path}", wait_until="domcontentloaded")
                assert response is not None and response.status == 200
                _assert_no_overflow(page)
                page.screenshot(path=SCREENSHOT_DIR / f"{key}-{label}-{theme}.png", full_page=True)
    context.close()


def test_repaired_metadata_links_have_keyboard_focus_ring(django_server, browser):
    staff_email = "header-focus-rings@test.com"
    create_staff_user(staff_email)
    plan_path = _seed_route("plan-detail", "focus-plan-member@test.com")
    editor_path = _seed_route("plan-editor", "focus-editor-member@test.com")
    utm_path = _seed_route("utm-link-form", "focus-utm-member@test.com")
    connection.close()
    context = auth_context(browser, staff_email)
    page = context.new_page()

    page.goto(f"{django_server}{plan_path}", wait_until="domcontentloaded")
    analytics_off = page.get_by_role("button", name="Keep analytics off")
    if analytics_off.count() and analytics_off.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            analytics_off.click()
    _tab_to_and_assert_canonical_ring(page, '[data-testid="plan-detail-member-link"]')
    _tab_to_and_assert_canonical_ring(page, 'header a[href^="/studio/sprints/"]')

    page.goto(f"{django_server}{editor_path}", wait_until="domcontentloaded")
    _tab_to_and_assert_canonical_ring(page, '[data-testid="plan-editor-header"] h1 a')

    page.goto(f"{django_server}{utm_path}", wait_until="domcontentloaded")
    _tab_to_and_assert_canonical_ring(page, '[data-testid="utm-fields-help-link"]')
    context.close()
