"""Core browser coverage and visual artifacts for Worker triage (#1290)."""

import datetime
import os
import uuid
from pathlib import Path

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_staff_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]

OUT = Path(".tmp/screenshots/issue-1290")
STAFF_EMAIL = "worker-triage-1290@test.com"
BANNER_FUNC = "integrations.services.banner_generator.tasks.render_banner_for_content"
SYNC_FUNC = "integrations.services.github.sync_content_source"


def _reset_state():
    from django_q.models import OrmQ, Task

    from accounts.models import User
    from content.models import Article, Course, Download, Project, Workshop
    from email_app.models import EmailCampaign
    from events.models import Event, EventSeries
    from integrations.models import ContentSource

    OrmQ.objects.all().delete()
    Task.objects.all().delete()
    Article.objects.filter(slug__startswith="worker-1290-").delete()
    Course.objects.filter(slug__startswith="worker-1290-").delete()
    Project.objects.filter(slug__startswith="worker-1290-").delete()
    Download.objects.filter(slug__startswith="worker-1290-").delete()
    Workshop.objects.filter(slug__startswith="worker-1290-").delete()
    Event.objects.filter(slug__startswith="worker-1290-").delete()
    EventSeries.objects.filter(slug__startswith="worker-1290-").delete()
    EmailCampaign.objects.filter(subject__startswith="Worker 1290").delete()
    ContentSource.objects.filter(repo_name__contains="worker-1290").delete()
    User.objects.exclude(email=STAFF_EMAIL).delete()
    connection.close()


def _task(*, name, func="jobs.worker_1290", success=True, args=(), kwargs=None, started=None):
    from django_q.models import Task

    started = started or timezone.now()
    return Task.objects.create(
        id=uuid.uuid4().hex,
        name=name,
        func=func,
        args=args,
        kwargs=kwargs or {},
        started=started,
        stopped=started + datetime.timedelta(seconds=2),
        success=success,
        result=None if success else "RuntimeError: worker triage fixture",
    )


def _seed_history():
    from content.models import Article
    from integrations.models import ContentSource

    article = Article.objects.create(
        title="Worker entity with a deliberately long <escaped> title for responsive inspection",
        slug="worker-1290-available",
        date=timezone.localdate(),
    )
    base_started = timezone.now()
    featured_started = base_started + datetime.timedelta(minutes=1)
    available = _task(
        name="incident-banner-available",
        func=BANNER_FUNC,
        args=("article", article.pk),
        success=False,
        started=featured_started,
    )
    missing = _task(
        name="incident-banner-missing",
        func=BANNER_FUNC,
        args=("article", article.pk + 100000),
        success=False,
        started=featured_started,
    )
    _task(
        name="incident-unsupported",
        func=BANNER_FUNC + ".near-match",
        success=False,
        started=featured_started,
    )
    for index in range(52):
        _task(name=f"incident-history-{index:02d}", success=False, started=base_started)

    source = ContentSource.objects.create(repo_name="AI-Shipping-Labs/worker-1290-content")
    source_task = _task(name="content-sync", func=SYNC_FUNC, args=(source,))
    connection.close()
    return article.pk, available.id, missing.id, source.id, source_task.id


def _capture(page, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=OUT / f"{name}.png", full_page=True)


def _auth_context(browser):
    context = auth_context(browser, STAFF_EMAIL)
    context.add_cookies(
        [
            {
                "name": "aslab_analytics_consent",
                "value": "denied",
                "domain": "127.0.0.1",
                "path": "/",
            }
        ]
    )
    return context


def _set_theme(page, theme):
    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
    page.reload(wait_until="domcontentloaded")


def _tab_to(page, locator, max_tabs=160):
    page.evaluate("document.activeElement && document.activeElement.blur()")
    for _index in range(max_tabs):
        page.keyboard.press("Tab")
        if locator.evaluate("element => element === document.activeElement"):
            assert locator.evaluate("element => element.matches(':focus-visible')")
            assert locator.evaluate("element => getComputedStyle(element).boxShadow") != "none"
            return
    raise AssertionError("Keyboard focus never reached the requested affected-entity link")


def _activate_entity_link(page, locator, expected_path, *, task_id=None):
    """Activate a nested entity link and prove row navigation did not win."""
    assert locator.is_visible()
    locator.click()
    page.wait_for_url(f"**{expected_path}")
    if task_id is not None:
        assert f"/studio/worker/task/{task_id}" not in page.url


def _boxes_overlap(first, second):
    return not (
        first["x"] + first["width"] <= second["x"]
        or second["x"] + second["width"] <= first["x"]
        or first["y"] + first["height"] <= second["y"]
        or second["y"] + second["height"] <= first["y"]
    )


def _seed_exact_entity_journeys():
    from content.models import Article, Course, Download, Project, Workshop
    from email_app.models import EmailCampaign
    from events.models import Event, EventSeries

    today = timezone.localdate()
    now = timezone.now()
    entities = {
        "article": Article.objects.create(
            title="Worker 1290 article",
            slug="worker-1290-article",
            date=today,
        ),
        "course": Course.objects.create(
            title="Worker 1290 course",
            slug="worker-1290-course",
        ),
        "project": Project.objects.create(
            title="Worker 1290 project",
            slug="worker-1290-project",
            date=today,
        ),
        "download": Download.objects.create(
            title="Worker 1290 download",
            slug="worker-1290-download",
            file_url="https://example.com/worker-1290.pdf",
        ),
        "workshop": Workshop.objects.create(
            title="Worker 1290 workshop",
            slug="worker-1290-workshop",
            date=today,
        ),
        "event": Event.objects.create(
            title="Worker 1290 event",
            slug="worker-1290-event",
            start_datetime=now,
        ),
        "event_series": EventSeries.objects.create(
            name="Worker 1290 series",
            slug="worker-1290-series",
            start_time=datetime.time(10, 0),
        ),
    }
    route_paths = {
        "article": f"/studio/articles/{entities['article'].pk}/edit",
        "course": f"/studio/courses/{entities['course'].pk}/edit",
        "project": f"/studio/projects/{entities['project'].pk}/review",
        "download": f"/studio/downloads/{entities['download'].pk}/edit",
        "workshop": f"/studio/workshops/{entities['workshop'].pk}/",
        "event": f"/studio/events/{entities['event'].pk}/edit",
        "event_series": f"/studio/event-series/{entities['event_series'].pk}/",
    }
    tasks = {}
    for kind, entity in entities.items():
        tasks[kind] = _task(
            name=f"entity-banner-{kind}",
            func=BANNER_FUNC,
            args=(kind, entity.pk),
            success=False,
            started=now,
        )
    notification_funcs = (
        "events.tasks.notify_reschedule.send_reschedule_notice_fanout",
        "events.tasks.notify_reschedule.send_reschedule_notice_one",
        "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        "events.tasks.notify_cancellation.send_cancellation_notice_one",
        "events.tasks.notify_series_invite.send_series_update",
        "events.tasks.notify_series_invite.send_series_cancellation",
        "events.tasks.send_post_event_followup.send_post_event_followup_fanout",
        "events.tasks.send_post_event_followup.send_post_event_followup_one",
    )
    for index, func in enumerate(notification_funcs):
        tasks[f"notification-{index}"] = _task(
            name=f"entity-notification-{index}",
            func=func,
            args=(entities["event"].pk, 99),
            started=now,
        )
    campaign = EmailCampaign.objects.create(subject="Worker 1290 campaign", body="Body")
    for index, func in enumerate(
        (
            "email_app.tasks.send_campaign.send_campaign",
            "email_app.tasks.send_campaign.send_campaign_batch",
        )
    ):
        tasks[f"campaign-{index}"] = _task(
            name=f"entity-campaign-{index}",
            func=func,
            args=(campaign.pk, [99]) if index else (),
            kwargs={} if index else {"campaign_id": campaign.pk},
            started=now,
        )
    tasks["missing"] = _task(
        name="entity-missing",
        func=BANNER_FUNC,
        args=("article", entities["article"].pk + 100000),
        started=now,
    )
    tasks["unsupported"] = _task(
        name="entity-unsupported",
        func="jobs.worker_1290.unsupported",
        started=now,
    )
    connection.close()
    return entities, route_paths, campaign.pk, tasks


def test_worker_history_filters_nested_links_and_visual_states(django_server, browser):
    ensure_tiers()
    _reset_state()
    create_staff_user(STAFF_EMAIL)
    article_id, available_id, missing_id, _source_id, _source_task_id = _seed_history()

    context = _auth_context(browser)
    page = context.new_page()
    today = timezone.localdate().isoformat()
    page.goto(
        f"{django_server}/studio/worker/?q=incident&status=failed"
        f"&date_from={today}&date_to={today}",
        wait_until="domcontentloaded",
    )
    assert page.locator(".recent-task-row").count() == 50
    assert "55 matching tasks" in page.locator("main").inner_text()
    assert page.get_by_text("Article", exact=False).count() >= 1
    assert page.get_by_text("not found", exact=False).count() >= 1
    assert page.locator('[title="No recognized affected entity"]').count() >= 1

    available_row = page.locator(
        f'.recent-task-row[data-task-id="{available_id}"]'
    )
    entity_link = available_row.locator('td[data-label="Affected entity"] a')
    assert entity_link.get_attribute("href") == f"/studio/articles/{article_id}/edit"
    entity_link.click()
    page.wait_for_url(f"**/studio/articles/{article_id}/edit")
    assert f"/studio/worker/task/{available_id}" not in page.url

    page.go_back(wait_until="domcontentloaded")
    next_link = page.get_by_role("link", name="Next", exact=True)
    next_link.click()
    page.wait_for_load_state("domcontentloaded")
    assert "task_page=2" in page.url
    assert "q=incident" in page.url and "status=failed" in page.url
    assert page.locator(".recent-task-row").count() == 5

    page.goto(
        f"{django_server}/studio/worker/?q=incident&status=failed",
        wait_until="domcontentloaded",
    )
    _capture(page, "history-desktop-light")
    _set_theme(page, "dark")
    _capture(page, "history-desktop-dark")

    page.set_viewport_size({"width": 393, "height": 852})
    _capture(page, "history-mobile-dark")
    _set_theme(page, "light")
    _capture(page, "history-mobile-light")
    _set_theme(page, "dark")
    widths = page.evaluate(
        "() => ({client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})"
    )
    assert widths["scroll"] <= widths["client"] + 2

    page.set_viewport_size({"width": 1280, "height": 720})
    _set_theme(page, "light")
    page.goto(f"{django_server}/studio/worker/task/{available_id}/", wait_until="domcontentloaded")
    assert page.locator(f'a[href="/studio/articles/{article_id}/edit"]').is_visible()
    _capture(page, "detail-available-desktop-light")
    _set_theme(page, "dark")
    _capture(page, "detail-available-desktop-dark")
    page.set_viewport_size({"width": 393, "height": 852})
    _capture(page, "detail-available-mobile-dark")
    page.goto(f"{django_server}/studio/worker/task/{missing_id}/", wait_until="domcontentloaded")
    assert "not found" in page.locator("main").inner_text()
    _capture(page, "detail-missing-mobile-dark")

    page.goto(f"{django_server}/studio/worker/?q=no-match", wait_until="domcontentloaded")
    assert "No tasks match these filters" in page.locator("main").inner_text()
    assert page.get_by_test_id("studio-empty-state-filter").is_visible()
    _capture(page, "history-no-match-mobile-dark")
    page.goto(
        f"{django_server}/studio/worker/?date_from=2026-07-20&date_to=2026-07-18",
        wait_until="domcontentloaded",
    )
    assert "From date must be on or before To date" in page.locator("main").inner_text()
    assert "No tasks match these filters" not in page.locator("main").inner_text()
    _capture(page, "history-date-error-mobile-dark")
    context.close()


def test_content_sync_entity_lands_on_stable_source_card(django_server, browser):
    ensure_tiers()
    _reset_state()
    create_staff_user(STAFF_EMAIL)
    _article_id, _available_id, _missing_id, source_id, source_task_id = _seed_history()
    context = _auth_context(browser)
    page = context.new_page()
    page.goto(f"{django_server}/studio/worker/task/{source_task_id}/", wait_until="domcontentloaded")
    link = page.locator(f'a[href="/studio/sync/#content-source-{source_id}"]')
    assert link.is_visible()
    link.click()
    page.wait_for_url(f"**/studio/sync/#content-source-{source_id}")
    assert page.locator(f"#content-source-{source_id}").count() == 1
    assert page.locator(f"#sync-source-{source_id}").is_visible()
    context.close()


def test_exact_entity_mouse_keyboard_and_malformed_date_journeys(django_server, browser):
    ensure_tiers()
    _reset_state()
    create_staff_user(STAFF_EMAIL)
    entities, route_paths, campaign_id, tasks = _seed_exact_entity_journeys()
    context = _auth_context(browser)
    page = context.new_page()
    worker_url = f"{django_server}/studio/worker/"

    page.goto(worker_url, wait_until="domcontentloaded")
    for kind, expected_path in route_paths.items():
        row = page.locator(f'.recent-task-row[data-task-id="{tasks[kind].id}"]')
        link = row.locator('td[data-label="Affected entity"] a')
        assert link.get_attribute("href") == expected_path
        _activate_entity_link(page, link, expected_path, task_id=tasks[kind].id)
        page.goto(worker_url, wait_until="domcontentloaded")

    event_path = route_paths["event"]
    for index in range(8):
        key = f"notification-{index}"
        link = page.locator(
            f'.recent-task-row[data-task-id="{tasks[key].id}"] '
            'td[data-label="Affected entity"] a'
        )
        assert link.get_attribute("href") == event_path
        _activate_entity_link(page, link, event_path, task_id=tasks[key].id)
        page.goto(worker_url, wait_until="domcontentloaded")
    campaign_path = f"/studio/campaigns/{campaign_id}/"
    for key in ("campaign-0", "campaign-1"):
        link = page.locator(
            f'.recent-task-row[data-task-id="{tasks[key].id}"] '
            'td[data-label="Affected entity"] a'
        )
        assert link.get_attribute("href") == campaign_path
        _activate_entity_link(page, link, campaign_path, task_id=tasks[key].id)
        page.goto(worker_url, wait_until="domcontentloaded")

    missing_row = page.locator(f'.recent-task-row[data-task-id="{tasks["missing"].id}"]')
    assert "not found" in missing_row.inner_text()
    assert missing_row.locator('td[data-label="Affected entity"] a').count() == 0
    unsupported_row = page.locator(
        f'.recent-task-row[data-task-id="{tasks["unsupported"].id}"]'
    )
    assert unsupported_row.locator('[title="No recognized affected entity"]').count() == 1

    course_link = page.locator(
        f'.recent-task-row[data-task-id="{tasks["course"].id}"] '
        'td[data-label="Affected entity"] a'
    )
    _tab_to(page, course_link)
    page.keyboard.press("Enter")
    page.wait_for_url(f"**{route_paths['course']}")

    # Every banner kind is also activated from Failed Tasks. The disclosure
    # state must remain untouched by the nested entity link.
    for kind, expected_path in route_paths.items():
        page.goto(worker_url, wait_until="domcontentloaded")
        failed_row = page.locator(
            f'.failed-task-row[data-failed-task-id="{tasks[kind].id}"]'
        )
        toggle = failed_row.locator('[data-action="toggle-failed-trace"]')
        failed_link = failed_row.locator(f'a[href="{expected_path}"]')
        assert toggle.get_attribute("aria-expanded") == "false"
        assert failed_row.locator(".failed-task-trace").is_hidden()
        _activate_entity_link(page, failed_link, expected_path)

    page.goto(worker_url, wait_until="domcontentloaded")
    failed_row = page.locator(f'.failed-task-row[data-failed-task-id="{tasks["article"].id}"]')
    toggle = failed_row.locator('[data-action="toggle-failed-trace"]')
    failed_link = failed_row.locator(f'a[href="{route_paths["article"]}"]')
    assert toggle.get_attribute("aria-expanded") == "false"
    assert failed_row.locator(".failed-task-trace").is_hidden()
    _tab_to(page, failed_link)
    assert toggle.get_attribute("aria-expanded") == "false"
    _capture(page, "failed-entity-keyboard-focus")
    page.keyboard.press("Enter")
    page.wait_for_url(f"**{route_paths['article']}")

    page.go_back(wait_until="domcontentloaded")
    failed_row = page.locator(f'.failed-task-row[data-failed-task-id="{tasks["article"].id}"]')
    toggle = failed_row.locator('[data-action="toggle-failed-trace"]')
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert failed_row.locator(".failed-task-trace").is_visible()

    # Every banner kind is activated again from completed-task detail.
    for kind, expected_path in route_paths.items():
        page.goto(
            f"{django_server}/studio/worker/task/{tasks[kind].id}/",
            wait_until="domcontentloaded",
        )
        detail_link = page.locator(f'a[href="{expected_path}"]')
        _activate_entity_link(page, detail_link, expected_path)

    # Parent notification/campaign links are real journeys on task detail too,
    # not merely href assertions on the history table.
    for index in range(8):
        key = f"notification-{index}"
        page.goto(
            f"{django_server}/studio/worker/task/{tasks[key].id}/",
            wait_until="domcontentloaded",
        )
        _activate_entity_link(page, page.locator(f'a[href="{event_path}"]'), event_path)
    for key in ("campaign-0", "campaign-1"):
        page.goto(
            f"{django_server}/studio/worker/task/{tasks[key].id}/",
            wait_until="domcontentloaded",
        )
        _activate_entity_link(page, page.locator(f'a[href="{campaign_path}"]'), campaign_path)

    page.goto(f"{django_server}/studio/worker/task/{tasks['article'].id}/", wait_until="domcontentloaded")
    detail_link = page.locator(f'a[href="{route_paths["article"]}"]')
    _tab_to(page, detail_link)
    _capture(page, "detail-entity-keyboard-focus")
    page.keyboard.press("Enter")
    page.wait_for_url(f"**{route_paths['article']}")

    # At 393px the failed-task identity and entity/actions occupy separate
    # regions, and nested controls remain operable without overlap.
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(worker_url, wait_until="domcontentloaded")
    failed_row = page.locator(f'.failed-task-row[data-failed-task-id="{tasks["article"].id}"]')
    toggle = failed_row.locator('[data-action="toggle-failed-trace"]')
    controls = failed_row.locator('[data-testid="failed-task-controls"]')
    toggle_box = toggle.bounding_box()
    controls_box = controls.bounding_box()
    assert toggle_box and controls_box
    assert not _boxes_overlap(toggle_box, controls_box)
    assert controls_box["x"] >= 0
    assert controls_box["x"] + controls_box["width"] <= 393
    assert failed_row.locator('[data-action="retry-failed"]').is_visible()
    assert failed_row.locator('[data-action="delete-failed"]').is_visible()
    _capture(page, "failed-mobile-no-overlap")
    mobile_failed_link = failed_row.locator(f'a[href="{route_paths["article"]}"]')
    _activate_entity_link(page, mobile_failed_link, route_paths["article"])

    page.goto(worker_url, wait_until="domcontentloaded")
    mobile_toggle = page.locator(
        f'.failed-task-row[data-failed-task-id="{tasks["article"].id}"] '
        '[data-action="toggle-failed-trace"]'
    )
    mobile_toggle.click()
    assert mobile_toggle.get_attribute("aria-expanded") == "true"
    page.goto(
        f"{django_server}/studio/worker/task/{tasks['article'].id}/",
        wait_until="domcontentloaded",
    )
    mobile_detail_link = page.locator(f'a[href="{route_paths["article"]}"]')
    _activate_entity_link(page, mobile_detail_link, route_paths["article"])
    page.goto(worker_url, wait_until="domcontentloaded")
    mobile_history_link = page.locator(
        f'.recent-task-row[data-task-id="{tasks["course"].id}"] '
        'td[data-label="Affected entity"] a'
    )
    _activate_entity_link(
        page,
        mobile_history_link,
        route_paths["course"],
        task_id=tasks["course"].id,
    )

    page.goto(
        f"{django_server}/studio/worker/task/{tasks['missing'].id}/",
        wait_until="domcontentloaded",
    )
    assert "not found" in page.locator("main").inner_text()
    assert page.locator('main a[title*="not found"]').count() == 0
    page.goto(
        f"{django_server}/studio/worker/task/{tasks['unsupported'].id}/",
        wait_until="domcontentloaded",
    )
    assert page.locator('[title="No recognized affected entity"]').count() == 1

    page.goto(
        f"{django_server}/studio/worker/?date_from=not-a-date&date_to=2026-07-18",
        wait_until="domcontentloaded",
    )
    malformed = page.locator('input[name="date_from"]')
    assert malformed.get_attribute("type") == "text"
    assert malformed.input_value() == "not-a-date"
    assert malformed.get_attribute("aria-invalid") == "true"
    assert page.get_by_role("alert").get_by_text("Enter dates in YYYY-MM-DD format.").is_visible()
    _capture(page, "history-malformed-date-visible")
    malformed.fill("2026-07-17")
    page.get_by_role("button", name="Apply filters").click()
    page.wait_for_load_state("domcontentloaded")
    corrected = page.locator('input[name="date_from"]')
    assert corrected.get_attribute("type") == "date"
    assert corrected.input_value() == "2026-07-17"
    context.close()
