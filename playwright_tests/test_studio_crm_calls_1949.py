"""Playwright E2E tests for CRM calls + one-hop nav + member Q&A (#1949).

Browser-level scenarios from the issue that the Django tests cannot
cover (JS menus, multi-page round trips, rendered sections):

1. One-hop nav: staff account menu (desktop + mobile) has a CRM item
   linking to /studio/crm/; a non-staff menu has none.
2. Log call: from the contact detail, staff logs a call with date/time
   + notes, lands back on the contact detail, and its Calls section
   lists the new call.
3. Calls list + edit: /studio/crm/calls/ shows the call with its source
   and a notes snippet and links to the contact; the edit round-trip
   updates the notes on the call detail.
4. Member Q&A: a member with a course question and a workshop answer
   shows both entries with context labels and public thread links;
   hidden comments are excluded; a member without comments sees the
   empty state.
5. Access control: an anonymous visitor is redirected to login from the
   calls pages.
"""

import datetime
import os
import uuid

import pytest

from scripts.browser_journey_policy import browser_journey
from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
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
from django.utils import timezone  # noqa: E402

# Local-only: DB seeding + session-cookie injection (see testing guidelines).
pytestmark = pytest.mark.local_only

STAFF_EMAIL = "crm-calls-staff@test.com"
MEMBER_EMAIL = "crm-calls-member@test.com"
QUIET_EMAIL = "crm-calls-quiet@test.com"
ASKER_EMAIL = "crm-calls-asker@test.com"

MOBILE_VIEWPORT = {"width": 390, "height": 844}


def _wipe_state():
    from accounts.models import User
    from comments.models import Comment
    from content.models import Course, Workshop, WorkshopPage
    from crm.models import CRMRecord
    from crm.models.call import Call

    Call.objects.all().delete()
    Comment.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Course.objects.all().delete()
    CRMRecord.objects.all().delete()
    User.objects.exclude(is_staff=True).delete()
    connection.close()


def _seed():
    """Seed staff, a Q&A-active member, a silent member, and thread owners.

    Returns the pk map the tests need for URL building.
    """
    from accounts.models import User
    from comments.models import Comment
    from content.models import Course, Module, Unit, Workshop, WorkshopPage
    from crm.models import CRMRecord

    _create_staff_user(STAFF_EMAIL)
    staff = User.objects.get(email=STAFF_EMAIL)
    member = _create_user(MEMBER_EMAIL, tier_slug="main", email_verified=True)
    quiet = _create_user(QUIET_EMAIL, tier_slug="free", email_verified=True)
    asker = _create_user(ASKER_EMAIL, tier_slug="free", email_verified=True)

    course = Course.objects.create(
        title="Calls E2E Course", slug="calls-e2e-course", status="published",
    )
    module = Module.objects.create(
        course=course, title="Calls Module", slug="calls-module", sort_order=1,
    )
    unit = Unit.objects.create(
        module=module, title="Calls Lesson", slug="calls-lesson",
        sort_order=1, source_content_id=uuid.uuid4(), is_preview=True,
    )
    workshop = Workshop.objects.create(
        slug="calls-e2e-ws", title="Calls E2E Workshop", status="published",
        date=datetime.date(2026, 4, 21), landing_required_level=0,
        pages_required_level=0, recording_required_level=0,
        description="ws",
    )
    ws_page = WorkshopPage.objects.create(
        workshop=workshop, slug="calls-intro", title="Calls Intro",
        sort_order=1, body="intro", content_id=uuid.uuid4(),
    )

    # The member asked a course question...
    question = Comment.objects.create(
        content_id=unit.source_content_id, user=member,
        body="How do I evaluate the RAG pipeline from this lesson?",
    )
    # ...and answered someone else's workshop question.
    ws_question = Comment.objects.create(
        content_id=ws_page.content_id, user=asker,
        body="Which embedding model does the intro use?",
    )
    Comment.objects.create(
        content_id=ws_page.content_id, user=member, parent=ws_question,
        body="It uses bge-small by default; swap it in the config.",
    )
    # A moderated comment is excluded from the section entirely.
    Comment.objects.create(
        content_id=unit.source_content_id, user=member,
        body="Hidden note for moderation state check",
        hidden_at=timezone.now() - datetime.timedelta(hours=2),
    )
    # Silence the ordering flake: guarantee the question is not newest.
    Comment.objects.filter(pk=question.pk).update(
        created_at=timezone.now() - datetime.timedelta(days=3),
    )

    member_record = CRMRecord.objects.create(user=member, created_by=staff)
    quiet_record = CRMRecord.objects.create(user=quiet, created_by=staff)

    pks = {
        "member_record": member_record.pk,
        "quiet_record": quiet_record.pk,
    }
    connection.close()
    return pks


def _mobile_auth_context(browser, email):
    """An authenticated context with a phone-sized viewport."""
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=MOBILE_VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


@pytest.mark.django_db(transaction=True)
class TestCRMOneHopNav:
    @pytest.mark.core
    @browser_journey
    def test_desktop_account_menu_links_to_crm(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="account-menu-trigger"]').click()
        crm_link = page.locator('[data-testid="header-account-crm-link"]')
        crm_link.wait_for(state="visible")
        crm_link.click()
        page.wait_for_url("**/studio/crm/**")
        assert page.locator(
            '[data-testid="crm-list-table"], '
            '[data-testid="studio-empty-state-fresh"]',
        ).first.is_visible()
        context.close()

    @pytest.mark.core
    @browser_journey
    def test_mobile_menu_links_to_crm(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        _seed()

        context = _mobile_auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator("#mobile-menu-btn").click()
        mobile_link = page.locator('[data-testid="mobile-crm-link"]')
        mobile_link.wait_for(state="visible")
        mobile_link.click()
        page.wait_for_url("**/studio/crm/**")
        context.close()

    @pytest.mark.core
    @browser_journey
    def test_non_staff_menu_has_no_crm_item(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        _create_user(MEMBER_EMAIL, tier_slug="free", email_verified=True)
        connection.close()

        context = _auth_context(browser, MEMBER_EMAIL)
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="account-menu-trigger"]').click()
        page.locator('[data-testid="account-menu-dropdown"]').wait_for(
            state="visible",
        )
        assert page.locator(
            '[data-testid="header-account-crm-link"]',
        ).count() == 0
        assert page.locator('[data-testid="mobile-crm-link"]').count() == 0
        context.close()


@pytest.mark.django_db(transaction=True)
class TestLogCallFlow:
    @pytest.mark.core
    @browser_journey
    def test_log_call_from_contact_detail_round_trip(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['member_record']}/",
            wait_until="domcontentloaded",
        )

        # The Calls section starts empty.
        assert page.locator('[data-testid="crm-calls-empty"]').is_visible()

        # Log a call: date/time + what was discussed.
        page.locator('[data-testid="crm-calls-log-link"]').click()
        page.wait_for_url("**/calls/new/")
        page.locator('[data-testid="crm-call-occurred-at-input"]').fill(
            "2026-06-01T14:30",
        )
        page.locator(
            '[data-testid="crm-call-summary-input"]',
        ).fill("Discussed the RAG eval plan; agreed on a homework review.")
        page.locator('[data-testid="crm-call-form-submit"]').click()

        # AC7: back on the contact detail, the Calls section lists the
        # new call with its summary.
        page.wait_for_url(f"**/studio/crm/{pks['member_record']}/")
        calls_section = page.locator('[data-testid="crm-calls-section"]')
        assert calls_section.locator(
            '[data-testid="crm-call-item"]',
        ).count() == 1
        assert calls_section.get_by_text(
            "Discussed the RAG eval plan",
        ).is_visible()
        context.close()


@pytest.mark.django_db(transaction=True)
class TestCallsListAndEdit:
    @pytest.mark.core
    @browser_journey
    def test_list_shows_row_and_edit_round_trip(
        self, django_server, browser,
    ):
        from accounts.models import User
        from crm.models import CRMRecord
        from crm.models.call import Call

        _ensure_tiers()
        _wipe_state()
        pks = _seed()
        record = CRMRecord.objects.get(pk=pks["member_record"])
        staff = User.objects.get(email=STAFF_EMAIL)
        call = Call.objects.create(
            contact=record,
            email=record.user.email,
            occurred_at=timezone.now() - datetime.timedelta(days=1),
            summary="First pass notes",
            source="manual",
            created_by=staff,
        )
        connection.close()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        # The list row shows the snippet and links to the contact record.
        page.goto(
            f"{django_server}/studio/crm/calls/",
            wait_until="domcontentloaded",
        )
        row = page.locator(f'[data-testid="crm-calls-row-{call.pk}"]')
        assert row.is_visible()
        assert row.get_by_text("First pass notes").is_visible()
        assert row.locator(
            '[data-testid="crm-calls-contact-link"]',
        ).is_visible()

        # Edit round-trip: change the notes, detail reflects it.
        page.goto(
            f"{django_server}/studio/crm/calls/{call.pk}/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="crm-call-detail-edit"]').click()
        page.wait_for_url("**/edit/")
        notes_box = page.locator('[data-testid="crm-call-summary-input"]')
        notes_box.fill("Updated after the second conversation.")
        page.locator('[data-testid="crm-call-form-submit"]').click()
        page.wait_for_url("**/studio/crm/calls/**")
        assert page.locator(
            '[data-testid="crm-call-detail-summary"]',
        ).get_by_text("Updated after the second conversation.").is_visible()
        context.close()


@pytest.mark.django_db(transaction=True)
class TestRecentQASection:
    @pytest.mark.core
    @browser_journey
    def test_qa_entries_with_links_and_empty_state(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['member_record']}/",
            wait_until="domcontentloaded",
        )

        qa_section = page.locator('[data-testid="crm-qna-section"]')
        items = qa_section.locator('[data-testid="crm-qna-item"]')
        # The course question + the workshop answer; the hidden comment
        # is excluded entirely.
        assert items.count() == 2

        # Question and answer are both flagged.
        assert qa_section.get_by_text("Question", exact=True).count() >= 1
        assert qa_section.get_by_text("Answer", exact=True).count() >= 1
        assert qa_section.get_by_text(
            "Hidden note for moderation state check",
        ).count() == 0

        # Links point at the public threads.
        unit_link = qa_section.locator("a[href*='calls-lesson']").first
        assert unit_link.is_visible()
        ws_link = qa_section.locator("a[href*='calls-e2e-ws']").first
        assert ws_link.is_visible()

        # A member with no comments sees the empty state.
        page.goto(
            f"{django_server}/studio/crm/{pks['quiet_record']}/",
            wait_until="domcontentloaded",
        )
        assert page.locator('[data-testid="crm-qna-empty"]').is_visible()
        context.close()


@pytest.mark.django_db(transaction=True)
class TestCallsAccessControl:
    @browser_journey
    def test_anonymous_redirects_to_login(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        _seed()

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/calls/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in page.url
        context.close()
