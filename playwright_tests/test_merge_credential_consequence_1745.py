"""What a revoked credential costs, on the merge plan and in Django admin (#1745).

The merge plan used to report ``Member API keys revoked 1`` and stop there. The
operator running a merge is rarely the person who knows a key was in use, so
these journeys cover the four missing facts as a real browser sees them: the
credential dies on confirm, it does not transfer, it cannot be restored, and a
replacement has to be issued. The warning is gated on a nonzero counter, so the
all-zero merge here is the one that keeps the warning meaningful.

The last journey is the other operator-reachable deactivation path: the
Django-admin ``Active`` checkbox, where a superuser reads the help text and
then proves it true by saving.

Usage:
    uv run pytest playwright_tests/test_merge_credential_consequence_1745.py -v
"""

import os

import pytest
from playwright.sync_api import expect

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
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: local-only fixtures (DB seeding, session-cookie injection).
pytestmark = pytest.mark.local_only

CONSEQUENCE = '[data-testid="merge-plan-credentials-consequence"]'
REVOKED_NOTE = '[data-testid="merge-plan-credentials-revoked-note"]'
DELETED_NOTE = '[data-testid="merge-plan-credentials-deleted-note"]'


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _create_member_api_key(email, name="member key"):
    from accounts.models import MemberAPIKey, User

    _, plaintext = MemberAPIKey.create_for_user(
        user=User.objects.get(email=email), name=name
    )
    connection.close()
    return plaintext


def _create_package_api_key(email, name="package key"):
    from community_base.api.models import APIKey

    from accounts.models import User

    _, plaintext = APIKey.create_for_user(
        user=User.objects.get(email=email),
        name=name,
        scopes=["users.read"],
        kind=APIKey.Kind.MEMBER,
    )
    connection.close()
    return plaintext


def _create_operator_token(email, name="operator token"):
    """Issue a token the way a since-demoted operator got theirs.

    ``Token.clean()`` refuses to mint one for a non-staff user, so the account
    is briefly staff, exactly as it was when the token was issued.
    """
    from accounts.models import Token, User

    user = User.objects.get(email=email)
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    _, plaintext = Token.create_for_user(user=user, name=name)
    user.is_staff = False
    user.save(update_fields=["is_staff"])
    connection.close()
    return plaintext


def _member_key_authenticates(plaintext):
    from accounts.models import MemberAPIKey

    authenticated = MemberAPIKey.authenticate(plaintext) is not None
    connection.close()
    return authenticated


def _package_key_authenticates(plaintext):
    from community_base.api.models import APIKey

    authenticated = APIKey.authenticate(plaintext) is not None
    connection.close()
    return authenticated


def _operator_token_authenticates(plaintext):
    from accounts.models import Token

    authenticated = Token.authenticate(plaintext) is not None
    connection.close()
    return authenticated


def _operator_token_rows(user_pk):
    """Count by pk: a merged-in secondary's email is scrubbed by the merge."""
    from accounts.models import Token

    count = Token.objects.filter(user_id=user_pk).count()
    connection.close()
    return count


def _counter(page, testid):
    return page.locator(f'[data-testid="{testid}"] dd').inner_text().strip()


def _preview(page, django_server, canonical_email, secondary_email):
    page.goto(f"{django_server}/studio/users/merge/", wait_until="domcontentloaded")
    page.locator('[data-testid="merge-canonical-input"]').fill(canonical_email)
    page.locator('[data-testid="merge-secondary-input"]').fill(secondary_email)
    page.locator('[data-testid="merge-preview-submit"]').click()
    page.wait_for_load_state("domcontentloaded")


def _confirm(page):
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-testid="merge-confirm-submit"]').click()
    page.wait_for_load_state("domcontentloaded")


def _staff_page(browser, staff_email):
    _ensure_tiers()
    _create_staff_user(staff_email)
    _clear_users_except_staff(staff_email)
    return _auth_context(browser, staff_email)


@pytest.mark.django_db(transaction=True)
class TestPreviewWarnsBeforeConfirming:
    """The operator learns an integration key dies the moment they confirm."""

    @browser_journey
    def test_preview_explains_the_cost_of_the_revoked_keys(
        self, django_server, browser
    ):
        context = _staff_page(browser, "warn-admin@test.com")
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        _create_member_api_key("dupe@test.com")
        _create_package_api_key("dupe@test.com")

        page = context.new_page()
        _preview(page, django_server, "keep@test.com", "dupe@test.com")

        assert _counter(page, "merge-plan-member-api-keys-revoked") == "1"
        assert _counter(page, "merge-plan-package-api-keys-revoked") == "1"

        warning = page.locator(CONSEQUENCE)
        expect(warning).to_be_visible()
        expect(warning).to_contain_text(
            "These credentials stop working the moment you confirm."
        )
        expect(warning).to_contain_text("They do not transfer to keep@test.com")
        expect(warning).to_contain_text("cannot be restored")
        expect(warning).to_contain_text("they need a replacement key")
        expect(warning).to_contain_text("check before you confirm")
        expect(page.locator(REVOKED_NOTE)).to_have_text(
            "Revoked keys stay on the merged-in account as security history."
        )
        # No operator token was deleted, so nothing claims one was.
        expect(page.locator(DELETED_NOTE)).to_have_count(0)
        assert "deleted outright" not in page.locator("body").inner_text()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestResultTellsOperatorToReplaceTheKey:
    """After the merge the page says what to do about the dead key."""

    @browser_journey
    def test_confirm_flips_the_warning_to_the_past_tense(
        self, django_server, browser
    ):
        context = _staff_page(browser, "result-admin@test.com")
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        survivor = _create_package_api_key("keep@test.com", "survivor key")
        doomed = _create_package_api_key("dupe@test.com", "dupe key")
        _create_member_api_key("dupe@test.com")

        page = context.new_page()
        _preview(page, django_server, "keep@test.com", "dupe@test.com")
        _confirm(page)

        expect(page.locator('[data-testid="merge-result-headline"]')).to_contain_text(
            "dupe@test.com merged into keep@test.com"
        )
        assert _counter(page, "merge-plan-package-api-keys-revoked") == "1"
        assert _counter(page, "merge-plan-member-api-keys-revoked") == "1"

        warning = page.locator(CONSEQUENCE)
        expect(warning).to_be_visible()
        expect(warning).to_contain_text(
            "These credentials stopped working when the merge ran."
        )
        expect(warning).to_contain_text("They did not transfer to keep@test.com")
        expect(warning).to_contain_text("cannot be restored")
        expect(warning).to_contain_text("a replacement key must be issued now")
        assert "check before you confirm" not in warning.inner_text()

        # The warning is true: the merged-in key is dead, the survivor is not.
        assert _package_key_authenticates(doomed) is False
        assert _package_key_authenticates(survivor) is True

        context.close()


@pytest.mark.django_db(transaction=True)
class TestOrdinaryDuplicatesAreNotWarned:
    """A warning that fires on every merge stops being read."""

    @browser_journey
    def test_all_zero_counters_render_no_warning(self, django_server, browser):
        context = _staff_page(browser, "plain-admin@test.com")
        _create_user("plain-keep@test.com", tier_slug="free")
        _create_user("plain-dupe@test.com", tier_slug="free")

        page = context.new_page()
        _preview(page, django_server, "plain-keep@test.com", "plain-dupe@test.com")

        assert _counter(page, "merge-plan-member-api-keys-revoked") == "0"
        assert _counter(page, "merge-plan-operator-tokens-deleted") == "0"
        assert _counter(page, "merge-plan-package-api-keys-revoked") == "0"

        expect(page.locator(CONSEQUENCE)).to_have_count(0)
        expect(page.locator(REVOKED_NOTE)).to_have_count(0)
        expect(page.locator(DELETED_NOTE)).to_have_count(0)
        assert "cannot be restored" not in page.locator("body").inner_text()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestDeletedOperatorTokenLeavesNoTrace:
    """A demoted ex-operator's token is deleted, not revoked."""

    @browser_journey
    def test_deleted_note_replaces_the_security_history_note(
        self, django_server, browser
    ):
        context = _staff_page(browser, "token-admin@test.com")
        _create_user("ops-keep@test.com", tier_slug="free")
        _create_user("ex-op@test.com", tier_slug="free")
        token_plaintext = _create_operator_token("ex-op@test.com")
        ex_op_pk = _user_id_for("ex-op@test.com")

        page = context.new_page()
        _preview(page, django_server, "ops-keep@test.com", "ex-op@test.com")

        assert _counter(page, "merge-plan-operator-tokens-deleted") == "1"
        assert _counter(page, "merge-plan-member-api-keys-revoked") == "0"
        assert _counter(page, "merge-plan-package-api-keys-revoked") == "0"

        warning = page.locator(CONSEQUENCE)
        expect(warning).to_contain_text(
            "These credentials stop working the moment you confirm."
        )
        expect(warning).to_contain_text("They do not transfer to ops-keep@test.com")
        expect(warning).to_contain_text("cannot be restored")
        expect(page.locator(DELETED_NOTE)).to_have_text(
            "Operator tokens have no revoked state, so they are deleted "
            "outright and leave no record behind."
        )
        # Nothing was revoked, so the security-history sentence stays away.
        expect(page.locator(REVOKED_NOTE)).to_have_count(0)
        assert "security history" not in page.locator("body").inner_text()

        _confirm(page)
        assert _operator_token_authenticates(token_plaintext) is False
        assert _operator_token_rows(ex_op_pk) == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestAllThreeFamiliesShareOneWarning:
    """Three dead credential families, one warning -- not three."""

    @browser_journey
    def test_one_consequence_block_before_and_after_the_merge(
        self, django_server, browser
    ):
        context = _staff_page(browser, "all-admin@test.com")
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        member_plaintext = _create_member_api_key("dupe@test.com")
        package_plaintext = _create_package_api_key("dupe@test.com")
        token_plaintext = _create_operator_token("dupe@test.com")

        page = context.new_page()
        _preview(page, django_server, "keep@test.com", "dupe@test.com")

        for testid in (
            "merge-plan-member-api-keys-revoked",
            "merge-plan-operator-tokens-deleted",
            "merge-plan-package-api-keys-revoked",
        ):
            assert _counter(page, testid) == "1"

        expect(page.locator(CONSEQUENCE)).to_have_count(1)
        expect(page.locator(REVOKED_NOTE)).to_have_count(1)
        expect(page.locator(DELETED_NOTE)).to_have_count(1)
        expect(page.locator(CONSEQUENCE)).to_contain_text(
            "These credentials stop working the moment you confirm."
        )

        _confirm(page)

        expect(page.locator(CONSEQUENCE)).to_have_count(1)
        expect(page.locator(REVOKED_NOTE)).to_have_count(1)
        expect(page.locator(DELETED_NOTE)).to_have_count(1)
        expect(page.locator(CONSEQUENCE)).to_contain_text(
            "These credentials stopped working when the merge ran."
        )

        assert _member_key_authenticates(member_plaintext) is False
        assert _package_key_authenticates(package_plaintext) is False
        assert _operator_token_authenticates(token_plaintext) is False

        context.close()


@pytest.mark.django_db(transaction=True)
class TestAdminActiveCheckboxWarning:
    """The other deactivation path: the Django-admin ``Active`` checkbox."""

    @browser_journey
    def test_superuser_reads_the_help_text_then_proves_it_true(
        self, django_server, browser
    ):
        context = _staff_page(browser, "root-admin@test.com")
        _create_user("admin-target@test.com", tier_slug="free")
        member_plaintext = _create_member_api_key("admin-target@test.com")
        package_plaintext = _create_package_api_key("admin-target@test.com")
        token_plaintext = _create_operator_token("admin-target@test.com")
        target_pk = _user_id_for("admin-target@test.com")

        page = context.new_page()
        page.goto(
            f"{django_server}/admin/accounts/user/{target_pk}/change/",
            wait_until="domcontentloaded",
        )

        active_row = page.locator(".field-is_active")
        expect(active_row).to_contain_text(
            "revokes every API credential this account owns"
        )
        expect(active_row).to_contain_text("operator tokens are deleted")
        expect(active_row).to_contain_text("They stop working immediately")
        expect(active_row).to_contain_text(
            "re-checking this box does not restore them"
        )

        checkbox = page.locator("#id_is_active")
        expect(checkbox).to_be_checked()
        checkbox.uncheck()
        page.locator('input[name="_save"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert _member_key_authenticates(member_plaintext) is False
        assert _package_key_authenticates(package_plaintext) is False
        assert _operator_token_authenticates(token_plaintext) is False
        assert _operator_token_rows(target_pk) == 0

        # Re-checking the box is not an undo, exactly as the help text says.
        page.goto(
            f"{django_server}/admin/accounts/user/{target_pk}/change/",
            wait_until="domcontentloaded",
        )
        page.locator("#id_is_active").check()
        page.locator('input[name="_save"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert _member_key_authenticates(member_plaintext) is False
        assert _package_key_authenticates(package_plaintext) is False
        assert _operator_token_authenticates(token_plaintext) is False

        context.close()
