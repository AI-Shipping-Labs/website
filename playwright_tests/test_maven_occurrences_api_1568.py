"""Browser journeys for the Maven occurrence operator API (issue #1568)."""

import os
from datetime import timedelta
from unittest.mock import patch

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection
from django.utils import timezone

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import MAX_STEP_ATTEMPTS, STEP_NAMES

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.creates_data,
    pytest.mark.core,
]


def _seed_staff_token(email, name="maven-browser-support"):
    staff = create_staff_user(email)
    token = Token.objects.create(user=staff, name=name)
    key = token.key
    connection.close()
    return staff, key


def _seed_member(email):
    member = create_user(email)
    connection.close()
    return member


def _seed_occurrence(key, *, user=None, **fields):
    defaults = {
        "dedupe_key": key,
        "identity_hash": key,
        "user": user,
        "email": user.email if user else f"{key}@example.com",
        "course": "Reliable AI Systems",
        "cohort": "Autumn 2026",
        "course_key": "reliable-ai",
        "cohort_key": "autumn-2026",
        "event_type": "user_cohort.enrolled",
        "lifecycle": MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
        "outcome": MavenEnrollmentEvent.OUTCOME_ONBOARDED,
        "override_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
        "enrollment_status": MavenEnrollmentEvent.STEP_SKIPPED,
        "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
        "slack_status": MavenEnrollmentEvent.STEP_SKIPPED,
        "welcome_status": MavenEnrollmentEvent.STEP_SKIPPED,
        "removal_status": MavenEnrollmentEvent.STEP_SKIPPED,
    }
    defaults.update(fields)
    occurrence = MavenEnrollmentEvent.objects.create(**defaults)
    connection.close()
    return occurrence


def _headers(key):
    return {"Authorization": f"Token {key}"}


def _step(payload, name):
    """Return one serialized step by name.

    Deliberately not positional: the ledger gained ``tagging`` at index 0 in
    issue #1732, and an eighth step must not break these journeys again. The
    canonical ORDER is still asserted once, against ``STEP_NAMES``.
    """
    return next(step for step in payload["steps"] if step["name"] == name)


@browser_journey
def test_staff_finds_alias_linked_failure_and_reads_the_step_ledger(
    django_server, browser
):
    staff_email = "maven-browser-diagnose-staff@example.com"
    member = _seed_member("maven-browser-canonical@example.com")
    EmailAlias.objects.create(user=member, email="maven-browser-former@example.com")
    occurrence = _seed_occurrence(
        "browser-diagnose",
        user=member,
        email="",
        payload_redacted_at=timezone.now(),
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=MAX_STEP_ATTEMPTS,
        welcome_attempted_at=timezone.now() - timedelta(minutes=20),
        welcome_completed_at=timezone.now() - timedelta(minutes=20),
        welcome_error="RuntimeError",
    )
    _staff, key = _seed_staff_token(staff_email)

    context = auth_context(browser, staff_email)
    page = context.new_page()
    docs_response = page.goto(f"{django_server}/api/docs", wait_until="domcontentloaded")
    assert docs_response is not None
    assert docs_response.status == 200
    assert page.locator("#swagger-ui").count() == 1

    list_response = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences"
        "?email=maven-browser-former%40example.com&status=failed",
        headers=_headers(key),
    )
    assert list_response.status == 200
    rows = list_response.json()["occurrences"]
    assert [row["id"] for row in rows] == [occurrence.pk]
    assert rows[0]["failed_steps"] == ["welcome"]
    assert rows[0]["occurrence_email"] == ""

    detail_response = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences/{occurrence.pk}",
        headers=_headers(key),
    )
    assert detail_response.status == 200
    detail = detail_response.json()
    assert [step["name"] for step in detail["steps"]] == list(STEP_NAMES)
    welcome = _step(detail, "welcome")
    assert welcome["needs_attention"] is True
    assert welcome["last_error"] == "RuntimeError"
    context.close()


@browser_journey
def test_staff_retries_exhausted_welcome_and_attention_list_clears(
    django_server, browser
):
    staff_email = "maven-browser-retry-staff@example.com"
    member = _seed_member("maven-browser-retry-member@example.com")
    occurrence = _seed_occurrence(
        "browser-retry",
        user=member,
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=MAX_STEP_ATTEMPTS,
        welcome_attempted_at=timezone.now() - timedelta(minutes=20),
        welcome_completed_at=timezone.now() - timedelta(minutes=20),
    )
    _staff, key = _seed_staff_token(staff_email)
    context = auth_context(browser, staff_email)
    page = context.new_page()
    docs_response = page.goto(f"{django_server}/api/docs", wait_until="domcontentloaded")
    assert docs_response is not None
    assert docs_response.status == 200

    before = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences/{occurrence.pk}",
        headers=_headers(key),
    ).json()
    assert before["needs_attention_steps"] == ["welcome"]

    with patch("integrations.services.maven._send_welcome") as send:
        response = context.request.post(
            f"{django_server}/api/integrations/maven/occurrences/"
            f"{occurrence.pk}/steps/welcome/retry",
            headers=_headers(key),
        )
    assert response.status == 200
    payload = response.json()
    assert payload["retry"] == {
        "step": "welcome",
        "outcome": "succeeded",
        "attempted": True,
    }
    assert _step(payload["occurrence"], "welcome")["attempts"] == MAX_STEP_ATTEMPTS + 1
    assert send.call_count == 1

    remaining = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences?status=needs_attention",
        headers=_headers(key),
    ).json()
    assert occurrence.pk not in {row["id"] for row in remaining["occurrences"]}
    context.close()


@browser_journey
def test_staff_gets_conflict_instead_of_duplicate_fresh_running_attempt(
    django_server, browser
):
    staff_email = "maven-browser-running-staff@example.com"
    member = _seed_member("maven-browser-running-member@example.com")
    occurrence = _seed_occurrence(
        "browser-running",
        user=member,
        welcome_status=MavenEnrollmentEvent.STEP_RUNNING,
        welcome_attempts=2,
        welcome_attempted_at=timezone.now(),
    )
    _staff, key = _seed_staff_token(staff_email)
    context = auth_context(browser, staff_email)
    page = context.new_page()
    docs_response = page.goto(f"{django_server}/api/docs", wait_until="domcontentloaded")
    assert docs_response is not None
    assert docs_response.status == 200

    with patch("integrations.services.maven._send_welcome") as send:
        response = context.request.post(
            f"{django_server}/api/integrations/maven/occurrences/"
            f"{occurrence.pk}/steps/welcome/retry",
            headers=_headers(key),
        )
    assert response.status == 409
    assert response.json()["code"] == "maven_step_in_progress"
    assert response.json()["details"] == {
        "step": "welcome",
        "status": "running",
        "attempts": 2,
    }
    send.assert_not_called()

    current = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences/{occurrence.pk}",
        headers=_headers(key),
    ).json()
    current_welcome = _step(current, "welcome")
    assert current_welcome["attempts"] == 2
    assert current_welcome["status"] == "running"
    context.close()


@browser_journey
def test_non_staff_session_and_token_cannot_read_or_retry(
    django_server, browser
):
    member = _seed_member("maven-browser-denied-member@example.com")
    occurrence = _seed_occurrence(
        "browser-denied",
        user=member,
        welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        welcome_attempts=MAX_STEP_ATTEMPTS,
    )
    token = Token(
        key="maven-browser-non-staff-token",
        user=member,
        name="legacy-member-token",
    )
    Token.objects.bulk_create([token])
    key = token.key
    connection.close()

    context = auth_context(browser, member.email)
    page = context.new_page()
    docs_response = page.goto(f"{django_server}/api/docs", wait_until="domcontentloaded")
    assert docs_response is not None
    assert docs_response.status == 403

    unknown = _headers("unknown-maven-browser-token")
    legacy = _headers(key)
    known_response = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences/{occurrence.pk}",
        headers=legacy,
    )
    unknown_response = context.request.get(
        f"{django_server}/api/integrations/maven/occurrences/{occurrence.pk}",
        headers=unknown,
    )
    retry_response = context.request.post(
        f"{django_server}/api/integrations/maven/occurrences/"
        f"{occurrence.pk}/steps/welcome/retry",
        headers=legacy,
    )
    assert known_response.status == 401
    assert retry_response.status == 401
    assert known_response.json() == unknown_response.json()

    occurrence.refresh_from_db()
    assert occurrence.welcome_attempts == MAX_STEP_ATTEMPTS
    assert not CommunityAuditLog.objects.filter(action="maven_step_retry").exists()
    context.close()
