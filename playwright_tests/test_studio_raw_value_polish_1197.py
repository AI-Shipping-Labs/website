"""Playwright coverage for Studio raw-value polish (#1197)."""

import os
import re
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only

_NO_CLUSTERS = "studio.worker_health.Stat.get_all"


def _reset_state():
    from django_q.models import OrmQ, Task

    from accounts.models import User
    from analytics.models import UserAttribution
    from email_app.models import EmailTemplateOverride
    from notifications.models import Notification
    from plans.models import Plan, Sprint
    from triggers.models import TriggerSubscription

    OrmQ.objects.all().delete()
    Task.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    EmailTemplateOverride.objects.all().delete()
    TriggerSubscription.objects.all().delete()
    Notification.objects.all().delete()
    UserAttribution.objects.all().delete()
    User.objects.exclude(email="staff-1197@test.com").delete()
    connection.close()


def _make_ormq(*, lock, name):
    from django_q.models import OrmQ
    from django_q.signing import SignedPackage

    payload = {
        "id": uuid.uuid4().hex,
        "name": name,
        "func": "integrations.services.github.sync_content_source",
        "args": (),
        "kwargs": {},
    }
    return OrmQ.objects.create(
        key="default",
        payload=SignedPackage.dumps(payload),
        lock=lock,
    )


def _seed_plan():
    from accounts.models import User
    from plans.models import Plan, Sprint

    member = User.objects.create_user(email="plan-1197@test.com", password="pw")
    sprint = Sprint.objects.create(
        name="Sprint 1197",
        slug="sprint-1197",
        start_date=timezone.localdate(),
    )
    plan = Plan.objects.create(
        member=member,
        sprint=sprint,
        title="**Ship** `RAG` with [docs](https://example.com)",
    )
    connection.close()
    return plan.pk


def _seed_email_template():
    from email_app.models import EmailTemplateOverride

    subject = (
        "{% if user_name %}Hi {{ user_name }}"
        "{% else %}Welcome builder{% endif %}"
    )
    EmailTemplateOverride.objects.create(
        template_name="welcome",
        subject=subject,
        body_markdown="Body",
    )
    connection.close()
    return subject


def _seed_trigger_subscriptions():
    from triggers.models import TriggerSubscription

    TriggerSubscription.objects.create(
        event_type="custom",
        property_filter={},
        target_url="https://handler.example.com/all",
        secret="secret",
    )
    TriggerSubscription.objects.create(
        event_type="custom",
        property_filter={"name": "experiment_demo"},
        target_url="https://handler.example.com/filtered",
        secret="secret",
    )
    connection.close()


def _seed_signup():
    from accounts.models import User
    from analytics.models import UserAttribution

    UserAttribution.objects.all().delete()
    user = User.objects.create_user(email="signup-1197@test.com", password="pw")
    attr, _ = UserAttribution.objects.get_or_create(user=user)
    UserAttribution.objects.filter(pk=attr.pk).update(
        created_at=timezone.now() - timedelta(days=6)
    )
    connection.close()


def _seed_notifications():
    from accounts.models import User
    from notifications.models import Notification

    target = User.objects.create_user(email="notify-1197@test.com", password="pw")
    for index, url in enumerate(
        [
            "/events/example?x=1#join",
            "http://localhost:8000/blog/example?x=1",
            "https://aishippinglabs.com/blog/example?x=1#frag",
            "https://external.example.com/blog/example?x=1",
        ]
    ):
        Notification.objects.create(
            user=target,
            title=f"Batch {index}",
            url=url,
            notification_type="new_content",
        )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStudioRawValuePolish1197:
    @pytest.fixture(autouse=True)
    def _setup(self):
        _create_staff_user("staff-1197@test.com")
        _reset_state()
        yield
        _reset_state()

    @pytest.mark.core
    def test_worker_pending_lock_text_is_humanized_on_page_and_fragment(
        self, django_server, browser,
    ):
        now = timezone.now()
        _make_ormq(lock=now + timedelta(seconds=45), name="future-lock")
        _make_ormq(lock=now - timedelta(days=15), name="expired-lock")
        _make_ormq(lock=None, name="unlocked-task")
        connection.close()

        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()
        with (
            mock.patch("studio.views.worker.timezone.now", return_value=now),
            mock.patch(_NO_CLUSTERS, return_value=[]),
        ):
            page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")
            expect(page.get_by_text("in 45s")).to_be_visible()
            expect(page.get_by_text("expired 15d ago")).to_be_visible()
            assert not re.search(r"\b\d{4,}s\b", page.locator("body").inner_text())

            page.goto(
                f"{django_server}/studio/worker/?fragment=pending",
                wait_until="domcontentloaded",
            )
            expect(page.get_by_text("in 45s")).to_be_visible()
            expect(page.get_by_text("expired 15d ago")).to_be_visible()
            assert not re.search(r"\b\d{4,}s\b", page.locator("body").inner_text())
        context.close()

    def test_plan_list_strips_markdown_but_detail_preserves_title(
        self, django_server, browser,
    ):
        plan_pk = _seed_plan()
        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/plans/", wait_until="domcontentloaded")
        body = page.locator("body").inner_text()
        assert "Ship RAG with docs" in body
        assert "**" not in body
        assert "`RAG`" not in body
        assert "[docs](https://example.com)" not in body

        page.goto(
            f"{django_server}/studio/plans/{plan_pk}/",
            wait_until="domcontentloaded",
        )
        assert "**Ship** `RAG` with [docs]" in page.locator("body").inner_text()
        context.close()

    def test_email_template_subject_preview_hides_control_flow(
        self, django_server, browser,
    ):
        subject = _seed_email_template()
        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/email-templates/",
            wait_until="domcontentloaded",
        )
        body = page.locator("body").inner_text()
        assert "Hi user_name Welcome builder" in body
        assert "{% if" not in body
        assert "{% else" not in body
        assert "{% endif" not in body

        page.goto(
            f"{django_server}/studio/email-templates/welcome/edit/",
            wait_until="domcontentloaded",
        )
        assert page.locator("#tpl-subject").input_value() == subject
        context.close()

    def test_trigger_filters_and_import_schedules_are_readable(
        self, django_server, browser,
    ):
        _seed_trigger_subscriptions()
        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/triggers/subscriptions/",
            wait_until="domcontentloaded",
        )
        body = page.locator("body").inner_text()
        assert "All events" in body
        assert "name = experiment_demo" in body
        assert "{'name': 'experiment_demo'}" not in body

        page.goto(f"{django_server}/studio/imports/", wait_until="domcontentloaded")
        body = page.locator("body").inner_text()
        assert "daily 03:00 UTC" in body
        assert "daily 03:30 UTC" in body
        context.close()

    def test_recent_signup_uses_operator_datetime_after_filters(
        self, django_server, browser,
    ):
        _seed_signup()
        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/signup-analytics/",
            wait_until="domcontentloaded",
        )
        recent = page.locator('[data-testid="signup-analytics-recent-table"]')
        expect(recent).to_be_visible()
        assert "ago" not in recent.inner_text()

        page.select_option("select[name='range']", "30d")
        page.wait_for_url("**/signup-analytics/?*range=30d*")
        recent = page.locator('[data-testid="signup-analytics-recent-table"]')
        expect(recent).to_be_visible()
        assert "ago" not in recent.inner_text()
        context.close()

    def test_notification_targets_are_normalized_and_clickable(
        self, django_server, browser,
    ):
        _seed_notifications()
        context = _auth_context(browser, "staff-1197@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/notifications/",
            wait_until="domcontentloaded",
        )
        body = page.locator("body").inner_text()
        assert "/events/example?x=1#join" in body
        assert "/blog/example?x=1" in body
        assert "/blog/example?x=1#frag" in body
        assert "https://external.example.com/blog/example?x=1" in body
        assert "http://localhost:8000" not in body
        assert "https://aishippinglabs.com/blog/example" not in body

        page.locator('a[href="/blog/example?x=1"]').click()
        page.wait_for_url("**/blog/example?x=1", wait_until="domcontentloaded")
        assert page.url == f"{django_server}/blog/example?x=1"
        context.close()
