import datetime
import os
from datetime import time, timedelta

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_seed_data():
    from accounts.models import ImportBatch, User
    from content.models import Article
    from email_app.models import EmailLog
    from email_app.models.ses_event import SesEvent
    from events.models import Event, EventSeries
    from payments.models import PaymentAccountMismatch
    from plans.models import Sprint
    from questionnaires.models import Persona, Questionnaire

    PaymentAccountMismatch.objects.all().delete()
    SesEvent.objects.all().delete()
    EmailLog.objects.all().delete()
    ImportBatch.objects.all().delete()
    Persona.objects.all().delete()
    Questionnaire.objects.all().delete()
    Sprint.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    Article.objects.all().delete()
    User.objects.exclude(email="admin@test.com").delete()
    connection.close()


def _seed_studio_lists():
    from accounts.models import IMPORT_SOURCE_SLACK, ImportBatch, User
    from content.models import Article
    from email_app.models import EmailLog
    from email_app.models.ses_event import SesEvent
    from events.models import Event, EventSeries
    from payments.models import PaymentAccountMismatch
    from plans.models import Sprint
    from questionnaires.models import Persona, Questionnaire

    staff = User.objects.get(email="admin@test.com")
    today = datetime.date(2026, 7, 10)
    for index in range(30):
        Article.objects.create(
            title=f"Agent Browser Article {index:02d}",
            slug=f"agent-browser-article-{index:02d}",
            date=today,
        )

    start = timezone.now() + timedelta(days=2)
    for index in range(26):
        Event.objects.create(
            title=f"Operator Browser Event {index:02d}",
            slug=f"operator-browser-event-{index:02d}",
            start_datetime=start + timedelta(days=index),
            status="upcoming",
        )

    Sprint.objects.create(
        name="Browser Agent Sprint",
        slug="browser-agent-sprint",
        start_date=today,
        duration_weeks=6,
        status="active",
    )
    EventSeries.objects.create(
        name="Browser Agent Series",
        slug="browser-agent-series",
        cadence="weekly",
        day_of_week=4,
        start_time=time(16, 0),
        timezone="UTC",
    )
    questionnaire = Questionnaire.objects.create(
        title="Browser Onboarding Questionnaire",
        slug="browser-onboarding-questionnaire",
        purpose="onboarding",
    )
    for index in range(26):
        Persona.objects.create(
            name=f"Browser Priya Persona {index:02d}",
            archetype="Builder",
            slug=f"browser-priya-persona-{index:02d}",
            default_questionnaire=questionnaire,
            order=index,
        )

    paid = User.objects.create_user(
        email="browser-paid@test.com",
        password="testpass123",
    )
    candidate = User.objects.create_user(
        email="browser-buyer@test.com",
        password="testpass123",
    )
    for index in range(26):
        PaymentAccountMismatch.objects.create(
            stripe_session_id=f"cs_browser_{index:02d}",
            stripe_customer_id=f"cus_browser_{index:02d}",
            stripe_subscription_id=f"sub_browser_{index:02d}",
            stripe_email=f"browser-buyer{index:02d}@example.com",
            paid_user=paid,
            candidate_user=candidate,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
        )

    email_log = EmailLog.objects.create(
        user=staff,
        email_type="campaign",
        ses_message_id="browser-ses-message",
    )
    SesEvent.objects.create(
        event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
        message_id="browser-sns-message",
        raw_payload={"Message": "payload"},
        recipient_email="browser-bounce@example.com",
        user=staff,
        email_log=email_log,
        bounce_type="Permanent",
        bounce_subtype="NoEmail",
        action_taken="unsubscribed and tagged bounced",
    )

    ImportBatch.objects.create(
        source=IMPORT_SOURCE_SLACK,
        actor=staff,
        dry_run=True,
        status=ImportBatch.STATUS_COMPLETED,
        finished_at=timezone.now(),
        users_created=1,
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStudioListBaseline1193:
    @pytest.mark.core
    def test_staff_browses_consistent_studio_lists(self, django_server, browser):
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _clear_seed_data()
        _seed_studio_lists()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/articles/?q=Agent", wait_until="domcontentloaded")
        page.locator('[data-testid="article-list-pager"]').wait_for(state="visible")
        assert "q=Agent&page=2" in page.locator(
            '[data-testid="article-list-pager-next"]'
        ).get_attribute("href")

        page.goto(f"{django_server}/studio/events/?q=Operator", wait_until="domcontentloaded")
        page.locator('[data-testid="event-upcoming-list-pager"]').wait_for(state="visible")
        event_date = page.locator('[data-testid="event-row-date"]').first
        assert "whitespace-nowrap" in event_date.get_attribute("class")
        assert "," not in event_date.inner_text()

        page.goto(f"{django_server}/studio/sprints/?q=Browser Agent", wait_until="domcontentloaded")
        page.locator('[data-component="studio-list-filter"]').wait_for(state="visible")
        assert page.locator("text=Browser Agent Sprint").count() == 1
        assert page.locator("text=Edit").first.is_visible()

        page.goto(f"{django_server}/studio/event-series/?q=Browser Agent", wait_until="domcontentloaded")
        assert page.locator("text=Browser Agent Series").count() == 1
        assert page.locator("text=No occurrences scheduled").is_visible()
        assert page.locator("text=Manage").first.is_visible()

        page.goto(
            f"{django_server}/studio/questionnaires/?q=browser-onboarding",
            wait_until="domcontentloaded",
        )
        page.locator('[data-component="studio-list-filter"]').wait_for(state="visible")
        assert page.locator("text=Browser Onboarding Questionnaire").count() == 1

        page.goto(f"{django_server}/studio/personas/?q=Priya", wait_until="domcontentloaded")
        page.locator('[data-testid="persona-list-pager"]').wait_for(state="visible")
        assert "q=Priya&page=2" in page.locator(
            '[data-testid="persona-list-pager-next"]'
        ).get_attribute("href")

        page.goto(
            f"{django_server}/studio/users/payment-mismatches/?status=open&q=browser-buyer",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="payment-mismatch-list-pager"]').wait_for(state="visible")
        assert "status=open&q=browser-buyer&page=2" in page.locator(
            '[data-testid="payment-mismatch-list-pager-next"]'
        ).get_attribute("href")

        page.goto(
            f"{django_server}/studio/ses-events/?type=bounce_permanent&q=browser-bounce",
            wait_until="domcontentloaded",
        )
        page.locator("text=browser-bounce@example.com").wait_for(state="visible")
        assert page.locator("text=Permanent").first.is_visible()
        assert page.locator("text=NoEmail").first.is_visible()
        assert "whitespace-nowrap" in page.locator('[data-label="Received"]').first.get_attribute("class")

        page.goto(f"{django_server}/studio/imports/", wait_until="domcontentloaded")
        page.locator("text=User imports").wait_for(state="visible")
        page.locator('[data-label="Started"]').first.wait_for(state="visible")
        assert "whitespace-nowrap" in page.locator('[data-label="Started"]').first.get_attribute("class")

        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")
        page.locator("text=Worker Status").wait_for(state="visible")
        page.locator("#pending-tasks-wrapper").wait_for(state="attached")

        context.close()
