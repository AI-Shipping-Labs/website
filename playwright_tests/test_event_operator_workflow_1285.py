"""Browser acceptance for the event-operator workflow shipped in #1285."""

import os
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1285")
MOBILE = {"width": 393, "height": 852}
DESKTOP = {"width": 1280, "height": 900}


def _staff_page(browser, email, viewport=MOBILE):
    create_staff_user(email)
    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size(viewport)
    return context, page


def _event(slug, **overrides):
    from events.models import Event

    values = {
        "title": "Operator source event",
        "slug": slug,
        "description": "Reusable operator description",
        "start_datetime": timezone.now() + timedelta(days=7),
        "end_datetime": timezone.now() + timedelta(days=7, hours=2),
        "timezone": "Europe/Berlin",
        "status": "draft",
        "origin": "studio",
    }
    values.update(overrides)
    return Event.objects.create(**values)


def _assert_no_horizontal_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "window.innerWidth"
    )


def _expect_focus_visible(locator):
    locator.focus()
    expect(locator).to_be_focused()
    style = locator.evaluate(
        """element => {
          const computed = getComputedStyle(element);
          return {outline: computed.outlineStyle, shadow: computed.boxShadow};
        }"""
    )
    assert style["outline"] != "none" or style["shadow"] != "none"


def _submit_forged_post(page, url):
    token = page.locator('input[name="csrfmiddlewaretoken"]').first.input_value()
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.evaluate(
            """({target, csrf}) => {
              const form = document.createElement('form');
              form.method = 'post';
              form.action = target;
              const input = document.createElement('input');
              input.type = 'hidden';
              input.name = 'csrfmiddlewaretoken';
              input.value = csrf;
              form.appendChild(input);
              document.body.appendChild(form);
              form.submit();
            }""",
            {"target": url, "csrf": token},
        )


def _dismiss_analytics_prompt(page):
    button = page.get_by_role("button", name="Keep analytics off")
    if button.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            button.click()


def test_mobile_duplicate_campaign_shortcut_and_guarded_delete(
    django_server, browser,
):
    from events.models import Event, EventRegistration

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    source = _event("operator-browser-source")
    campaign_event = _event(
        "operator-browser-campaign", title="Same title operator session",
    )
    member = create_user("operator-1285-member@test.com")
    EventRegistration.objects.create(event=campaign_event, user=member)
    source_id = source.pk
    campaign_id = campaign_event.pk
    connection.close()

    context, page = _staff_page(browser, "operator-1285-staff@test.com")

    page.goto(
        f"{django_server}/studio/campaigns/new?event={campaign_id}",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    expect(page.locator('[data-testid="campaign-target-event"]')).to_have_value(
        str(campaign_id)
    )
    expect(page.locator('input[name="subject"]')).to_have_value("")
    expect(page.locator('textarea[name="body"]')).to_have_value("")
    expect(page.locator('[data-testid="recipient-count-helper"]')).to_contain_text(
        "1"
    )

    page.goto(
        f"{django_server}/studio/events/{source_id}/edit",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="studio-header-overflow"] summary').click()
    expect(page.locator('[data-testid="studio-event-duplicate"]')).to_be_visible()
    expect(page.locator('[data-testid="studio-event-delete-submit"]')).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.screenshot(
        path=SCREENSHOT_DIR / "event-actions-mobile.png", full_page=False,
    )

    page.locator('[data-testid="studio-event-duplicate"]').click()
    expect(page.locator('[data-testid="event-duplicate-context"]')).to_contain_text(
        "Operator source event"
    )
    expect(page.locator('input[name="title"]')).to_have_value(
        "Operator source event (copy)"
    )
    expect(page.locator('input[name="slug"]')).to_have_value("")
    expect(page.locator('input[name="event_date"]')).to_have_value("")
    expect(page.locator('input[name="event_time"]')).to_have_value("")
    _assert_no_horizontal_overflow(page)
    page.screenshot(
        path=SCREENSHOT_DIR / "duplicate-event-mobile.png", full_page=False,
    )

    page.goto(
        f"{django_server}/studio/events/{source_id}/edit",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="studio-header-overflow"] summary').click()
    dialogs = []
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.locator('[data-testid="studio-event-delete-submit"]').click()
    expect(page).to_have_url(f"{django_server}/studio/events/{source_id}/edit")
    assert dialogs == [
        "Delete “Operator source event”? This cannot be undone."
    ]

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-testid="studio-event-delete-submit"]').click()
    expect(page).to_have_url(f"{django_server}/studio/events/")
    expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
        "Event “Operator source event” deleted."
    )
    assert not Event.objects.filter(pk=source_id).exists()
    context.close()


def test_bulk_publish_confirmation_scope_retry_and_mobile_layout(
    django_server, browser,
):
    from events.models import Event, EventSeries

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    series = EventSeries.objects.create(
        name="Browser publish series",
        slug="browser-publish-series",
        start_time=timezone.localtime().time().replace(second=0, microsecond=0),
    )
    drafts = [
        _event(
            f"browser-publish-{index}",
            title=f"Browser draft {index}",
            event_series=series,
            start_datetime=timezone.now() + timedelta(days=index),
            end_datetime=timezone.now() + timedelta(days=index, hours=1),
        )
        for index in (1, 2)
    ]
    already_published = _event(
        "browser-already-published",
        title="Already published",
        status="upcoming",
        event_series=series,
    )
    unrelated = _event("browser-unrelated-draft")
    series_id = series.pk
    draft_ids = [event.pk for event in drafts]
    connection.close()

    context, page = _staff_page(browser, "publish-1285-staff@test.com")
    page.goto(
        f"{django_server}/studio/event-series/{series_id}/",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    button = page.locator('[data-testid="event-series-publish-all-submit"]')
    expect(button).to_contain_text("Publish all drafts (2)")
    csrf_token = page.locator(
        '[data-testid="event-series-publish-all-form"] '
        'input[name="csrfmiddlewaretoken"]'
    ).input_value()
    _assert_no_horizontal_overflow(page)
    page.screenshot(
        path=SCREENSHOT_DIR / "series-publish-mobile.png", full_page=False,
    )

    dialogs = []
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    button.click()
    expect(button).to_be_visible()
    assert dialogs == [
        "Publish 2 draft occurrences in “Browser publish series”?"
    ]

    page.once("dialog", lambda dialog: dialog.accept())
    button.click()
    expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
        "Published 2 draft occurrences."
    )
    expect(button).to_have_count(0)

    assert set(
        Event.objects.filter(pk__in=draft_ids).values_list("status", flat=True)
    ) == {"upcoming"}
    already_published.refresh_from_db()
    unrelated.refresh_from_db()
    assert already_published.status == "upcoming"
    assert unrelated.status == "draft"

    # A direct retry is harmless and reports that no draft work remained.
    page.request.post(
        f"{django_server}/studio/event-series/{series_id}/publish-all",
        form={"csrfmiddlewaretoken": csrf_token},
        headers={"X-CSRFToken": csrf_token},
    )
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-testid="event-series-publish-all-submit"]')).to_have_count(0)
    context.close()


def test_same_title_campaign_options_keep_distinct_ids_and_live_counts(
    django_server, browser,
):
    from email_app.models import EmailCampaign
    from events.models import EventRegistration

    first = _event(
        "same-title-first-1285",
        title="Office Hours",
        start_datetime=timezone.now() + timedelta(days=7),
        end_datetime=timezone.now() + timedelta(days=7, hours=1),
    )
    second = _event(
        "same-title-second-1285",
        title="Office Hours",
        start_datetime=timezone.now() + timedelta(days=8),
        end_datetime=timezone.now() + timedelta(days=8, hours=1),
    )
    first_member = create_user("same-title-first-member-1285@test.com")
    second_members = [
        create_user(f"same-title-second-{index}-1285@test.com")
        for index in (1, 2)
    ]
    EventRegistration.objects.create(event=first, user=first_member)
    for member in second_members:
        EventRegistration.objects.create(event=second, user=member)
    first_id, second_id = first.pk, second.pk
    connection.close()

    context, page = _staff_page(
        browser, "same-title-operator-1285@test.com", DESKTOP,
    )
    page.goto(
        f"{django_server}/studio/campaigns/new?event={first_id}",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    select = page.locator('[data-testid="campaign-target-event"]')
    first_label = select.locator(f'option[value="{first_id}"]').inner_text()
    second_label = select.locator(f'option[value="{second_id}"]').inner_text()
    assert first_label.startswith("Office Hours — ")
    assert second_label.startswith("Office Hours — ")
    assert first_label != second_label
    expect(select).to_have_value(str(first_id))
    expect(page.locator('[data-testid="recipient-count-helper"]')).to_contain_text(
        "1 eligible recipient"
    )

    select.select_option(str(second_id))
    expect(select).to_have_value(str(second_id))
    expect(page.locator('[data-testid="recipient-count-helper"]')).to_contain_text(
        "2 eligible recipients"
    )
    assert EmailCampaign.objects.count() == 0
    context.close()


def test_duplicate_invalid_then_valid_submit_creates_one_clean_draft(
    django_server, browser,
):
    from events.models import Event, EventFeedback, EventRegistration

    source = _event(
        "duplicate-submit-source-1285",
        title="Duplicate submission source",
        description="Only safe copy",
        status="upcoming",
        platform="custom",
        zoom_join_url="https://secret.example/join",
        zoom_meeting_id="secret-provider-id",
        host_email="secret-host@example.com",
        recording_url="https://secret.example/recording",
    )
    member = create_user("duplicate-history-member-1285@test.com")
    EventRegistration.objects.create(event=source, user=member)
    EventFeedback.objects.create(event=source, user=member, rating=5)
    source_id = source.pk
    before = Event.objects.count()
    connection.close()

    context, page = _staff_page(
        browser, "duplicate-submit-operator-1285@test.com", DESKTOP,
    )
    page.goto(
        f"{django_server}/studio/events/{source_id}/duplicate",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator('input[name="event_date"]').evaluate(
        "element => element.removeAttribute('required')"
    )
    page.locator('input[name="event_time"]').evaluate(
        "element => element.removeAttribute('required')"
    )
    page.locator('[data-testid="event-create-submit"]').click()
    expect(page.locator('[data-testid="error-event-date"]')).to_be_visible()
    expect(page.locator('[data-testid="event-duplicate-context"]')).to_be_visible()
    expect(page.locator('input[name="title"]')).to_have_value(
        "Duplicate submission source (copy)"
    )
    assert Event.objects.count() == before

    future = (timezone.localdate() + timedelta(days=14)).strftime("%d/%m/%Y")
    page.locator('input[name="event_date"]').fill(future)
    page.locator('input[name="event_time"]').fill("18:30")
    page.locator('[data-testid="event-create-submit"]').click()
    expect(page).to_have_url(re.compile(r".*/studio/events/\d+/edit$"))
    expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
        "Event “Duplicate submission source (copy)” created."
    )

    assert Event.objects.count() == before + 1
    duplicate = Event.objects.exclude(pk=source_id).get()
    assert duplicate.status == "draft"
    assert duplicate.origin == "studio"
    assert duplicate.event_series_id is None
    assert duplicate.zoom_join_url == ""
    assert duplicate.zoom_meeting_id == ""
    assert duplicate.host_email == ""
    assert duplicate.recording_url == ""
    assert not EventRegistration.objects.filter(event=duplicate).exists()
    assert not EventFeedback.objects.filter(event=duplicate).exists()
    context.close()


def test_token_api_publish_is_scoped_and_idempotent(django_server, browser):
    from accounts.models import Token
    from events.models import EventSeries

    staff = create_staff_user("api-browser-operator-1285@test.com")
    token = Token.objects.create(user=staff, name="browser-publish-1285")
    series = EventSeries.objects.create(
        name="Browser API series",
        slug="browser-api-series-1285",
        start_time=timezone.localtime().time().replace(second=0, microsecond=0),
    )
    drafts = [
        _event(
            f"browser-api-draft-{index}-1285",
            event_series=series,
            series_position=index,
        )
        for index in (1, 2)
    ]
    unrelated = _event("browser-api-unrelated-1285")
    series_id = series.pk
    token_key = token.key
    draft_ids = [event.pk for event in drafts]
    connection.close()

    context = browser.new_context()
    headers = {"Authorization": f"Token {token_key}"}
    first = context.request.post(
        f"{django_server}/api/event-series/{series_id}/publish-drafts",
        headers=headers,
    )
    assert first.status == 200
    assert first.json() == {
        "series_id": series_id,
        "published_count": 2,
        "occurrence_ids": draft_ids,
    }
    replay = context.request.post(
        f"{django_server}/api/event-series/{series_id}/publish-drafts",
        headers=headers,
    )
    assert replay.status == 200
    assert replay.json() == {
        "series_id": series_id,
        "published_count": 0,
        "occurrence_ids": [],
    }
    unrelated.refresh_from_db()
    assert unrelated.status == "draft"
    context.close()


def test_future_and_past_event_create_feedback_is_exact(django_server, browser):
    from events.models import Event

    context, page = _staff_page(
        browser, "create-feedback-operator-1285@test.com", DESKTOP,
    )
    page.on("dialog", lambda dialog: dialog.accept())

    def create(title, day):
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.locator('input[name="title"]').fill(title)
        page.locator('input[name="event_date"]').fill(day.strftime("%d/%m/%Y"))
        page.locator('input[name="event_time"]').fill("12:00")
        page.locator('[data-testid="event-create-submit"]').click()
        expect(page).to_have_url(re.compile(r".*/studio/events/\d+/edit$"))

    create("Future & safe browser event", timezone.localdate() + timedelta(days=7))
    messages = page.locator('[data-testid="messages-region"]')
    expect(messages).to_contain_text(
        "Event “Future & safe browser event” created."
    )
    expect(messages).not_to_contain_text("This event's start time is in the past.")

    create("Past browser event", timezone.localdate() - timedelta(days=7))
    messages = page.locator('[data-testid="messages-region"]')
    expect(messages).to_contain_text("Event “Past browser event” created.")
    expect(messages).to_contain_text("This event's start time is in the past.")
    assert Event.objects.get(slug="past-browser-event").status == "draft"
    context.close()


def test_series_create_feedback_leads_to_deliberate_bulk_publish(
    django_server, browser,
):
    from events.models import Event, EventSeries

    context, page = _staff_page(
        browser, "series-create-operator-1285@test.com", DESKTOP,
    )
    page.goto(
        f"{django_server}/studio/event-series/new",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    start = timezone.localdate() + timedelta(days=14)
    page.locator('input[name="name"]').fill("Deliberate browser series")
    page.locator('input[name="start_date"]').fill(start.strftime("%d/%m/%Y"))
    page.locator('input[name="start_time"]').fill("18:00")
    page.locator('input[name="occurrences"]').fill("3")
    page.locator('[data-testid="sticky-save-action"]').click()
    expect(page).to_have_url(re.compile(r".*/studio/event-series/\d+/$"))
    expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
        "Event series “Deliberate browser series” created."
    )
    expect(page.locator('[data-testid="event-series-publish-all-submit"]')).to_contain_text(
        "Publish all drafts (3)"
    )
    expect(page.locator('[data-testid="event-publish-state"]')).to_have_count(3)
    assert all(
        text == "Not published"
        for text in page.locator('[data-testid="event-publish-state"]').all_inner_texts()
    )
    series = EventSeries.objects.get(slug="deliberate-browser-series")
    assert Event.objects.filter(event_series=series, status="draft").count() == 3
    context.close()


def test_protected_delete_controls_are_absent_and_forged_posts_refuse(
    django_server, browser,
):
    from email_app.models import EmailCampaign
    from events.models import Event, EventRegistration

    synced = _event(
        "protected-synced-1285",
        origin="github",
        source_repo="content",
    )
    registered = _event("protected-registered-1285")
    member = create_user("protected-delete-member-1285@test.com")
    registration = EventRegistration.objects.create(
        event=registered, user=member,
    )
    targeted = _event("protected-targeted-1285")
    campaign = EmailCampaign.objects.create(
        subject="Scoped campaign",
        body="Keep the event audience",
        target_event=targeted,
    )
    scenarios = [
        (synced.pk, "Source-managed events cannot be deleted."),
        (registered.pk, "Events with registrations cannot be deleted."),
        (targeted.pk, "Campaigns targeting this event must be retargeted first."),
    ]
    connection.close()

    context, page = _staff_page(
        browser, "protected-delete-operator-1285@test.com", MOBILE,
    )
    for event_id, message in scenarios:
        page.goto(
            f"{django_server}/studio/events/{event_id}/edit",
            wait_until="domcontentloaded",
        )
        _dismiss_analytics_prompt(page)
        page.locator('[data-testid="studio-header-overflow"] summary').click()
        expect(page.locator('[data-testid="studio-event-duplicate"]')).to_be_visible()
        expect(page.locator('[data-testid="studio-event-delete-submit"]')).to_have_count(0)
        _assert_no_horizontal_overflow(page)
        _submit_forged_post(
            page, f"{django_server}/studio/events/{event_id}/delete",
        )
        expect(page.locator('[data-testid="messages-region"]')).to_contain_text(
            message
        )
        assert Event.objects.filter(pk=event_id).exists()

    assert EventRegistration.objects.filter(pk=registration.pk).exists()
    campaign.refresh_from_db()
    assert campaign.target_event_id == targeted.pk
    context.close()


def test_long_title_actions_keep_keyboard_focus_and_notification_coexistence(
    django_server, browser,
):
    from events.models import Event

    long_title = "A deliberately long operator event title " + ("that wraps safely " * 8)
    event = _event(
        "long-keyboard-event-1285",
        title=long_title,
        status="upcoming",
        platform="custom",
        zoom_join_url="https://example.test/live",
    )
    event_id = event.pk
    connection.close()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    context, page = _staff_page(
        browser, "keyboard-actions-operator-1285@test.com", DESKTOP,
    )
    page.goto(
        f"{django_server}/studio/events/{event_id}/edit",
        wait_until="domcontentloaded",
    )
    _dismiss_analytics_prompt(page)
    _assert_no_horizontal_overflow(page)
    campaign = page.locator('[data-testid="new-campaign-to-registrants"]')
    notify = page.locator('#notify-subscribers-btn')
    slack = page.locator('#post-to-slack-btn')
    for control in (campaign, notify, slack):
        _expect_focus_visible(control)

    page.set_viewport_size(MOBILE)
    _assert_no_horizontal_overflow(page)
    summary = page.locator('[data-testid="studio-header-overflow"] summary')
    summary.focus()
    page.keyboard.press("Enter")
    duplicate = page.locator('[data-testid="studio-event-duplicate"]')
    delete = page.locator('[data-testid="studio-event-delete-submit"]')
    expect(duplicate).to_be_visible()
    expect(delete).to_be_visible()
    _expect_focus_visible(duplicate)
    _expect_focus_visible(delete)
    dialogs = []
    requests = []
    page.on(
        "request",
        lambda request: requests.append(request.url)
        if "/notify" in request.url or "/announce-slack" in request.url
        else None,
    )
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    delete.press("Enter")
    assert dialogs == [f"Delete “{long_title}”? This cannot be undone."]
    assert Event.objects.filter(pk=event_id).exists()

    notify.focus()
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.keyboard.press("Enter")
    slack.focus()
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.keyboard.press("Enter")
    assert requests == []
    assert "eligible members in app" in dialogs[-2]
    assert dialogs[-1] == (
        "Post this announcement to the configured #announcements channel? "
        "This cannot be undone."
    )
    _assert_no_horizontal_overflow(page)
    page.evaluate("window.scrollTo(0, 0)")
    if page.locator('[data-testid="studio-header-overflow"]').get_attribute("open") is None:
        summary.click()
    duplicate.focus()
    page.screenshot(
        path=SCREENSHOT_DIR / "long-title-keyboard-mobile.png",
        full_page=False,
    )
    context.close()
