"""Playwright E2E tests for the CRM onboarding section (issue #871).

The Studio CRM detail page (`/studio/crm/<id>/`) surfaces a member's
onboarding questionnaire answers above the Member notes section, with
submitted / draft / never-onboarded states. Staff-only; never reachable by
the member.

Scenarios (from the issue):
1. Staff reads a member's goals before a call (submitted answers + date,
   above Member notes).
2. Onboarding answers and a manual note appear together; the existing
   "add note" flow still works.
3. A never-onboarded member shows the empty state, other sections intact.
4. A mid-onboarding member shows the draft note + partial answers.
5. A non-staff member cannot reach the staff CRM page.
"""

import datetime
import os

import pytest

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

# Local-only: this module seeds DB rows and injects session cookies, so it
# cannot run against the deployed dev environment.
pytestmark = pytest.mark.local_only

STAFF_EMAIL = "crm-onb-staff@test.com"
ALICE_EMAIL = "crm-onb-alice@test.com"
BOB_EMAIL = "crm-onb-bob@test.com"
NEWBIE_EMAIL = "crm-onb-newbie@test.com"
HALFWAY_EMAIL = "crm-onb-halfway@test.com"

GOALS_PROMPT = "What are your goals?"
GOALS_ANSWER = "Build an AI portfolio and switch careers"
ROLE_PROMPT = "What is your current role?"
ROLE_ANSWER = "Backend engineer"
EXTRA_PROMPT = "Anything else we should know?"


def _wipe_state():
    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote, Plan, Sprint
    from questionnaires.models import Response

    Response.objects.filter(questionnaire__purpose="onboarding").delete()
    CRMRecord.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    User.objects.exclude(is_staff=True).delete()
    connection.close()


def _add_question(response, *, qtype, prompt, order):
    from questionnaires.models import ResponseQuestion

    return ResponseQuestion.objects.create(
        response=response, question_type=qtype, prompt=prompt, order=order,
    )


def _onboarding_questionnaire():
    from questionnaires.models import Questionnaire

    # The generic onboarding questionnaire is normally seeded by the
    # questionnaires data migration, but the shared Playwright DB may have
    # had it cleared by another module, so get-or-create defensively.
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug="onboarding-general",
        defaults={"title": "Onboarding", "purpose": "onboarding"},
    )
    return questionnaire


def _seed():
    """Seed staff + four members with the onboarding states under test."""
    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote
    from questionnaires.models import Answer, Response

    _create_staff_user(STAFF_EMAIL)
    staff = User.objects.get(email=STAFF_EMAIL)
    questionnaire = _onboarding_questionnaire()

    alice = _create_user(ALICE_EMAIL, tier_slug="main", email_verified=True)
    bob = _create_user(BOB_EMAIL, tier_slug="main", email_verified=True)
    newbie = _create_user(NEWBIE_EMAIL, tier_slug="free", email_verified=True)
    halfway = _create_user(HALFWAY_EMAIL, tier_slug="free", email_verified=True)

    # Alice: submitted onboarding with a goals answer.
    alice_resp = Response.objects.create(
        questionnaire=questionnaire, respondent=alice, status="submitted",
        submitted_at=datetime.datetime(2026, 5, 19, tzinfo=datetime.UTC),
    )
    rq = _add_question(
        alice_resp, qtype="long_text", prompt=GOALS_PROMPT, order=0,
    )
    Answer.objects.create(
        response=alice_resp, question=rq, text_value=GOALS_ANSWER,
    )

    # Bob: submitted onboarding + one internal note pasted from Slack.
    bob_resp = Response.objects.create(
        questionnaire=questionnaire, respondent=bob, status="submitted",
        submitted_at=datetime.datetime(2026, 5, 20, tzinfo=datetime.UTC),
    )
    rq_bob = _add_question(
        bob_resp, qtype="long_text", prompt=GOALS_PROMPT, order=0,
    )
    Answer.objects.create(
        response=bob_resp, question=rq_bob, text_value="Ship a SaaS side project",
    )
    InterviewNote.objects.create(
        member=bob, visibility="internal", kind="intake",
        body="Pasted Slack: Bob wants weekly accountability",
        created_by=staff,
    )

    # halfway: draft with one answered + one blank question.
    half_resp = Response.objects.create(
        questionnaire=questionnaire, respondent=halfway, status="draft",
    )
    rq_role = _add_question(
        half_resp, qtype="text", prompt=ROLE_PROMPT, order=0,
    )
    Answer.objects.create(
        response=half_resp, question=rq_role, text_value=ROLE_ANSWER,
    )
    _add_question(half_resp, qtype="long_text", prompt=EXTRA_PROMPT, order=1)

    # Track everyone (incl. never-onboarded newbie) in the CRM.
    records = {}
    for user in (alice, bob, newbie, halfway):
        records[user.email] = CRMRecord.objects.create(
            user=user, created_by=staff,
        )

    pks = {
        "alice_record": records[ALICE_EMAIL].pk,
        "bob_record": records[BOB_EMAIL].pk,
        "newbie_record": records[NEWBIE_EMAIL].pk,
        "halfway_record": records[HALFWAY_EMAIL].pk,
    }
    connection.close()
    return pks


@pytest.mark.django_db(transaction=True)
class TestCRMOnboardingSection:
    @pytest.mark.core
    def test_staff_reads_goals_before_call(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['alice_record']}/",
            wait_until="domcontentloaded",
        )

        section = page.locator('[data-testid="crm-onboarding-section"]')
        assert section.is_visible()
        assert section.get_by_text(GOALS_PROMPT).is_visible()
        assert section.get_by_text(GOALS_ANSWER).is_visible()
        assert page.locator(
            '[data-testid="crm-onboarding-submitted"]'
        ).is_visible()

        # Onboarding appears above Member notes in document order.
        onboarding_box = section.bounding_box()
        notes_box = page.locator(
            '[data-testid="crm-notes-section"]'
        ).bounding_box()
        assert onboarding_box["y"] < notes_box["y"]
        context.close()

    def test_onboarding_and_manual_note_together_and_add_note_works(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['bob_record']}/",
            wait_until="domcontentloaded",
        )

        assert page.locator(
            '[data-testid="crm-onboarding-section"]'
        ).get_by_text("Ship a SaaS side project").is_visible()
        assert page.get_by_text(
            "Pasted Slack: Bob wants weekly accountability"
        ).is_visible()

        # The existing add-note flow still works alongside onboarding: the
        # CRM page links to the shared note-create form, which saves and
        # redirects to the member detail. The new note then shows back on
        # the CRM record next to the onboarding answers.
        from plans.models import InterviewNote

        page.locator('[data-testid="member-notes-add"]').first.click()
        page.wait_for_url("**/notes/new**")
        page.locator('textarea[name="body"]').first.fill(
            "New note added during the call"
        )
        page.get_by_role("button", name="Save note").click()
        # Redirects to the member detail page after save.
        page.wait_for_load_state("domcontentloaded")

        assert InterviewNote.objects.filter(
            body="New note added during the call",
        ).exists()
        connection.close()

        # Re-open the CRM record: the new note and onboarding live together.
        page.goto(
            f"{django_server}/studio/crm/{pks['bob_record']}/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("New note added during the call").is_visible()
        assert page.locator(
            '[data-testid="crm-onboarding-section"]'
        ).get_by_text("Ship a SaaS side project").is_visible()
        context.close()

    def test_never_onboarded_shows_empty_state(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['newbie_record']}/",
            wait_until="domcontentloaded",
        )

        assert page.locator(
            '[data-testid="crm-onboarding-empty"]'
        ).is_visible()
        # Other sections still render.
        assert page.locator('[data-testid="crm-snapshot-card"]').is_visible()
        assert page.locator('[data-testid="crm-plans-section"]').is_visible()
        assert page.locator('[data-testid="crm-notes-section"]').is_visible()
        context.close()

    def test_draft_shows_partial_answers(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['halfway_record']}/",
            wait_until="domcontentloaded",
        )

        section = page.locator('[data-testid="crm-onboarding-section"]')
        assert page.locator('[data-testid="crm-onboarding-draft"]').is_visible()
        assert page.locator(
            '[data-testid="crm-onboarding-submitted"]'
        ).count() == 0
        assert section.get_by_text(ROLE_PROMPT).is_visible()
        assert section.get_by_text(ROLE_ANSWER).is_visible()
        context.close()

    def test_member_cannot_access_staff_crm_page(self, django_server, browser):
        _ensure_tiers()
        _wipe_state()
        pks = _seed()

        # halfway is a non-staff member; their session must not reach the
        # staff CRM page or any onboarding/notes data.
        session_key = _create_session_for_user(HALFWAY_EMAIL)
        context = browser.new_context()
        context.add_cookies([
            {
                "name": "sessionid",
                "value": session_key,
                "domain": "127.0.0.1",
                "path": "/",
            },
        ])
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/crm/{pks['halfway_record']}/",
            wait_until="domcontentloaded",
        )

        assert response.status in (302, 403) or "/login" in page.url
        assert page.locator(
            '[data-testid="crm-onboarding-section"]'
        ).count() == 0
        assert ROLE_ANSWER not in page.content()
        context.close()
