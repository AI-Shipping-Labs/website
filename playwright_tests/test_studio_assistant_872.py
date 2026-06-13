"""Playwright E2E for the Studio AI assistant (issue #872, Phase 1).

The Django dev server runs in the SAME process as the test (a background
thread), so the LLM boundary is mocked in-process with
``unittest.mock.patch('studio.services.assistant.llm.complete', ...)`` —
CI never makes a live call. The LLM service is enabled by writing the
``LLM_API_KEY`` config to the DB (read via ``get_config``).

These assert the propose -> confirm -> execute wiring, the cancel path,
the decline / unknown-member refusals, staff-only gating, and the
not-configured state. Screenshots land in
``.tmp/aisl-issue-872-screenshots`` for tester review.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = (
    Path(__file__).parent.parent / ".tmp" / "aisl-issue-872-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@contextmanager
def _llm_enabled(enabled=True):
    """Enable/disable the LLM service via DB config for the server thread."""
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    keys = ["LLM_API_KEY", "LLM_PROVIDER"]
    if enabled:
        IntegrationSetting.objects.update_or_create(
            key="LLM_API_KEY", defaults={"value": "sk-test-fake"},
        )
        IntegrationSetting.objects.update_or_create(
            key="LLM_PROVIDER", defaults={"value": "anthropic"},
        )
    else:
        IntegrationSetting.objects.filter(key__in=keys).delete()
    clear_config_cache()
    connection.close()
    try:
        yield
    finally:
        IntegrationSetting.objects.filter(key__in=keys).delete()
        clear_config_cache()
        connection.close()


def _result(tool_name=None, tool_input=None, text=""):
    from integrations.services.llm import LLMResult

    return LLMResult(text=text, tool_input=tool_input, tool_name=tool_name)


def _note_result(email, body):
    return _result(
        tool_name="add_member_note",
        tool_input={"member_email": email, "body": body},
    )


def _profile_result(email, **fields):
    return _result(
        tool_name="update_member_profile",
        tool_input={"member_email": email, **fields},
    )


def _seed_member(email="jane@example.com", with_crm=False):
    from crm.models import CRMRecord

    user = _create_user(email=email)
    if with_crm:
        CRMRecord.objects.get_or_create(user=user)
    connection.close()
    return user


def _reset():
    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote
    from studio.models import AssistantActionLog

    InterviewNote.objects.all().delete()
    AssistantActionLog.objects.all().delete()
    CRMRecord.objects.all().delete()
    User.objects.filter(email="jane@example.com").delete()
    connection.close()


def _note_count(email):
    from accounts.models import User
    from plans.models import InterviewNote

    user = User.objects.filter(email=email).first()
    count = (
        InterviewNote.objects.filter(member=user).count() if user else 0
    )
    connection.close()
    return count


def _crm_status(email):
    from accounts.models import User
    from crm.models import CRMRecord

    user = User.objects.filter(email=email).first()
    record = CRMRecord.objects.filter(user=user).first() if user else None
    status = record.status if record else None
    connection.close()
    return status


def _login_admin(browser, django_server):
    _create_staff_user(email="admin@test.com")
    connection.close()
    context = _auth_context(browser, "admin@test.com")
    return context.new_page()


@pytest.mark.django_db(transaction=True)
class TestProposeConfirm:
    @pytest.mark.core
    def test_note_proposal_then_confirm_persists(
        self, django_server, browser,
    ):
        _reset()
        _seed_member("jane@example.com")
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_note_result(
                "jane@example.com", "wants the Premium teardown",
            ),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(
                "Add a note to jane@example.com: wants the Premium teardown",
            )
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            proposal = page.locator('[data-testid="assistant-proposal"]')
            proposal.wait_for(state="visible", timeout=10000)
            assert "jane@example.com" in proposal.inner_text()
            assert "Premium teardown" in proposal.inner_text()
            _shot(page, "note_proposal")
            # Nothing saved yet.
            assert _note_count("jane@example.com") == 0

            page.locator('[data-testid="assistant-confirm"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.locator('[data-testid="assistant-result"]').wait_for(
                state="visible", timeout=10000,
            )
            _shot(page, "note_confirmed")

        assert _note_count("jane@example.com") == 1

    @pytest.mark.core
    def test_cancel_writes_nothing(self, django_server, browser):
        _reset()
        _seed_member("jane@example.com")
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_note_result("jane@example.com", "some note"),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(
                "Add a note to jane@example.com: some note",
            )
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.locator('[data-testid="assistant-cancel"]').click()
            page.wait_for_load_state("domcontentloaded")
            # Proposal cleared; no confirm form.
            assert page.locator('[data-testid="assistant-proposal"]').count() == 0
            _shot(page, "cancelled")

        assert _note_count("jane@example.com") == 0

    @pytest.mark.core
    def test_profile_update_field(self, django_server, browser):
        _reset()
        _seed_member("jane@example.com", with_crm=True)
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_profile_result("jane@example.com", status="archived"),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(
                "Archive jane@example.com in the CRM",
            )
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            changes = page.locator('[data-testid="assistant-proposal-changes"]')
            changes.wait_for(state="visible", timeout=10000)
            assert "archived" in changes.inner_text()
            _shot(page, "profile_proposal")

            page.locator('[data-testid="assistant-confirm"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.locator('[data-testid="assistant-result"]').wait_for(
                state="visible", timeout=10000,
            )
            _shot(page, "profile_confirmed")

        assert _crm_status("jane@example.com") == "archived"


@pytest.mark.django_db(transaction=True)
class TestRefusals:
    @pytest.mark.core
    def test_decline_out_of_scope(self, django_server, browser):
        _reset()
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_result(
                text="I can only add member notes or update member profiles.",
            ),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(
                "Delete all events from last year",
            )
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            declined = page.locator('[data-testid="assistant-declined"]')
            declined.wait_for(state="visible", timeout=10000)
            assert "member notes" in declined.inner_text().lower()
            assert page.locator('[data-testid="assistant-proposal"]').count() == 0
            _shot(page, "declined")

    @pytest.mark.core
    def test_unknown_member_refused(self, django_server, browser):
        _reset()
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_note_result("nobody@nowhere.test", "hi"),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(
                "Add a note to nobody@nowhere.test: hi",
            )
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            error = page.locator('[data-testid="assistant-error"]')
            error.wait_for(state="visible", timeout=10000)
            assert "nobody@nowhere.test" in error.inner_text()
            assert page.locator('[data-testid="assistant-proposal"]').count() == 0
            _shot(page, "unknown_member")


@pytest.mark.django_db(transaction=True)
class TestGatingAndConfig:
    @pytest.mark.core
    def test_non_staff_forbidden(self, django_server, browser):
        _create_user(email="free@test.com", tier_slug="free")
        connection.close()
        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/assistant/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403
        _shot(page, "non_staff_403")

    @pytest.mark.core
    def test_not_configured_state(self, django_server, browser):
        page = _login_admin(browser, django_server)
        # ``is_enabled`` is patched to False directly because this dev
        # environment may carry an ``LLM_API_KEY`` env var, which the DB
        # config falls back to — deleting the DB row alone would not
        # disable the service. The patch is in the same (in-process)
        # server thread, so the rendered page sees the disabled state.
        with patch(
            "studio.views.assistant.llm.is_enabled", return_value=False,
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            not_configured = page.locator(
                '[data-testid="assistant-not-configured"]',
            )
            not_configured.wait_for(state="visible", timeout=10000)
            _shot(page, "not_configured")
            # The not-configured panel renders instead of the input form.
            # (The "submitting never 500s" guarantee is asserted in the
            # Django suite's test_post_does_not_500; here we assert the
            # graceful disabled surface renders for the operator.)
            assert page.locator(
                '[data-testid="assistant-form"]',
            ).count() == 0


@pytest.mark.django_db(transaction=True)
class TestAssistantPolish942:
    """Polish from acceptance reviews (issue #942): consistent nav/H1 label
    and the request textarea clearing only after a successful execute."""

    @pytest.mark.core
    def test_nav_and_heading_label_match(self, django_server, browser):
        page = _login_admin(browser, django_server)
        page.goto(
            f"{django_server}/studio/assistant/",
            wait_until="domcontentloaded",
        )
        # The assistant nav entry lives inside the collapsible "people"
        # section, which is not expanded on the assistant page, so the link
        # is present in the DOM but not necessarily visible. The label text
        # is what this scenario asserts, so read it without a visibility
        # wait — it still fails if the span says "Assistant".
        nav = page.locator('[data-testid="studio-nav-assistant"]')
        nav.wait_for(state="attached", timeout=10000)
        heading = page.get_by_role("heading", name="AI Assistant", exact=True)
        # Both the nav entry and the page H1 read "AI Assistant" — no
        # "Assistant" vs "AI Assistant" mismatch.
        assert nav.text_content().strip() == "AI Assistant"
        assert heading.count() == 1
        _shot(page, "label_consistency")

    @pytest.mark.core
    def test_textarea_cleared_after_successful_confirm(
        self, django_server, browser,
    ):
        _reset()
        _seed_member("jane@example.com")
        request = (
            "Add a note to jane@example.com: wants the Premium teardown"
        )
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_note_result(
                "jane@example.com", "wants the Premium teardown",
            ),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(request)
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.locator('[data-testid="assistant-proposal"]').wait_for(
                state="visible", timeout=10000,
            )
            page.locator('[data-testid="assistant-confirm"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.locator('[data-testid="assistant-result"]').wait_for(
                state="visible", timeout=10000,
            )
            # After a successful execute the request box is empty, so a
            # second submit cannot resubmit the old request.
            assert (
                page.locator('[data-testid="assistant-input"]').input_value()
                == ""
            )
            _shot(page, "textarea_cleared_after_success")

    @pytest.mark.core
    def test_textarea_preserved_on_unknown_member_error(
        self, django_server, browser,
    ):
        _reset()
        request = "Add a note to nobody@nowhere.test: hi"
        page = _login_admin(browser, django_server)
        with _llm_enabled(), patch(
            "studio.services.assistant.llm.complete",
            return_value=_note_result("nobody@nowhere.test", "hi"),
        ):
            page.goto(
                f"{django_server}/studio/assistant/",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="assistant-input"]').fill(request)
            page.locator('[data-testid="assistant-submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            error = page.locator('[data-testid="assistant-error"]')
            error.wait_for(state="visible", timeout=10000)
            assert "nobody@nowhere.test" in error.inner_text()
            # The error path keeps the request so the admin can fix the
            # email and retry.
            assert (
                page.locator('[data-testid="assistant-input"]').input_value()
                == request
            )
            _shot(page, "textarea_preserved_on_error")
