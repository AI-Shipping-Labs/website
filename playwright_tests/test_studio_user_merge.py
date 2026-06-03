"""End-to-end coverage for the Studio account-merge UI (issue #842).

Exercises the irreversible preview -> confirm flow from a real browser: a clean
merge with a moved event registration, the dry-run-is-a-no-op guarantee, the
self-merge stop, the unknown-email message, the non-staff 403 gate, and the
pre-fill-from-user-detail entry point.

Usage:
    uv run pytest playwright_tests/test_studio_user_merge.py -v
"""

import os

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
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

# Issue #656: local-only fixtures (DB seeding, session-cookie injection).
pytestmark = pytest.mark.local_only


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _register_for_event(email, event_slug):
    """Give ``email`` a registration for a (newly created) event."""
    from accounts.models import User
    from events.models import Event, EventRegistration

    event, _ = Event.objects.get_or_create(
        slug=event_slug,
        defaults={"title": event_slug, "start_datetime": timezone.now()},
    )
    EventRegistration.objects.get_or_create(
        event=event, user=User.objects.get(email=email)
    )
    connection.close()


def _secondary_state(email):
    """Return ``(is_active, has_alias_on_canonical)`` for assertions."""
    from accounts.models import EmailAlias, User

    user = User.objects.filter(email=email).first()
    is_active = bool(user and user.is_active)
    has_alias = EmailAlias.objects.filter(email=email).exists()
    connection.close()
    return is_active, has_alias


def _canonical_event_count(email):
    from accounts.models import User
    from events.models import EventRegistration

    user = User.objects.get(email=email)
    n = EventRegistration.objects.filter(user=user).count()
    connection.close()
    return n


@pytest.mark.django_db(transaction=True)
class TestPreviewThenConfirm:
    """Staff previews a merge, reviews the plan, and commits it."""

    @pytest.mark.core
    def test_full_preview_confirm_flow(self, django_server, browser):
        _ensure_tiers()
        staff_email = "merge-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        _register_for_event("dupe@test.com", "merge-ev")
        canonical_pk = _user_id_for("keep@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="merge-canonical-input"]').fill("keep@test.com")
        page.locator('[data-testid="merge-secondary-input"]').fill("dupe@test.com")
        page.locator('[data-testid="merge-preview-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Plan shows the moved event registration row + deactivation notice.
        assert page.locator('[data-testid="merge-preview"]').count() == 1
        plan = page.locator('[data-testid="merge-plan"]')
        assert "events.EventRegistration" in plan.inner_text()
        assert page.locator(
            '[data-testid="merge-plan-deactivate-notice"]'
        ).count() == 1
        assert page.locator('[data-testid="merge-confirm-submit"]').count() == 1

        # Confirm.
        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="merge-confirm-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success headline + link to canonical detail.
        headline = page.locator('[data-testid="merge-result-headline"]')
        assert headline.count() == 1
        assert "dupe@test.com merged into keep@test.com" in headline.inner_text()
        link = page.locator('[data-testid="merge-result-canonical-link"]')
        assert link.get_attribute("href").endswith(
            f"/studio/users/{canonical_pk}/"
        )

        # Canonical now owns the registration.
        assert _canonical_event_count("keep@test.com") == 1
        is_active, has_alias = _secondary_state("dupe@test.com")
        assert is_active is False
        assert has_alias is True

        # Following the link loads the canonical detail page.
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert f"/studio/users/{canonical_pk}/" in page.url

        context.close()


@pytest.mark.django_db(transaction=True)
class TestPreviewIsDryRun:
    """Preview persists nothing."""

    def test_preview_changes_nothing(self, django_server, browser):
        _ensure_tiers()
        staff_email = "dry-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        _register_for_event("dupe@test.com", "dry-ev")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="merge-canonical-input"]').fill("keep@test.com")
        page.locator('[data-testid="merge-secondary-input"]').fill("dupe@test.com")
        page.locator('[data-testid="merge-preview-submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="merge-preview"]').count() == 1

        # Without confirming, secondary is untouched.
        is_active, has_alias = _secondary_state("dupe@test.com")
        assert is_active is True
        assert has_alias is False
        assert _canonical_event_count("keep@test.com") == 0
        assert _canonical_event_count("dupe@test.com") == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestSelfMergeStopped:
    """An account cannot be merged into itself."""

    def test_self_merge_blocked(self, django_server, browser):
        _ensure_tiers()
        staff_email = "self-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("solo@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="merge-canonical-input"]').fill("solo@test.com")
        page.locator('[data-testid="merge-secondary-input"]').fill("solo@test.com")
        page.locator('[data-testid="merge-preview-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        err = page.locator('[data-testid="merge-error-self-merge"]')
        assert err.count() == 1
        assert "into itself" in err.inner_text().lower()
        assert page.locator('[data-testid="merge-confirm-submit"]').count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestUnknownEmail:
    """An email with no account shows a clear field message."""

    def test_unknown_secondary(self, django_server, browser):
        _ensure_tiers()
        staff_email = "ghost-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="merge-canonical-input"]').fill("keep@test.com")
        page.locator('[data-testid="merge-secondary-input"]').fill("ghost@test.com")
        page.locator('[data-testid="merge-preview-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        err = page.locator('[data-testid="merge-error-secondary"]')
        assert err.count() == 1
        assert "No account found for ghost@test.com" in err.inner_text()
        assert page.locator('[data-testid="merge-preview"]').count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffBlocked:
    """A non-staff member cannot reach the merge screen."""

    def test_member_gets_403(self, django_server, browser):
        _ensure_tiers()
        staff_email = "gate-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("main@test.com", tier_slug="free")

        context = _auth_context(browser, "main@test.com")
        response = context.request.get(
            f"{django_server}/studio/users/merge/", max_redirects=0
        )
        assert response.status == 403
        context.close()


@pytest.mark.django_db(transaction=True)
class TestPrefilledFromUserDetail:
    """The user detail "Merge accounts" action pre-fills canonical."""

    def test_prefill_canonical(self, django_server, browser):
        _ensure_tiers()
        staff_email = "prefill-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        member_pk = _user_id_for("keep@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        action = page.locator('[data-testid="user-detail-merge"]')
        assert action.count() == 1
        action.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/studio/users/merge/" in page.url
        canonical_input = page.locator('[data-testid="merge-canonical-input"]')
        assert canonical_input.input_value() == "keep@test.com"

        context.close()
