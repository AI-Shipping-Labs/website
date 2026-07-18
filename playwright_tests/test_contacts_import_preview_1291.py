"""Operator journeys for contact-import outcome preview (#1291)."""

import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.contrib.sessions.backends.db import SessionStore
from django.db import connection

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _start_import(page, django_server, csv_path, content):
    csv_path.write_text(content)
    page.goto(f'{django_server}/studio/users/import/', wait_until='domcontentloaded')
    consent = page.get_by_test_id('analytics-consent-deny')
    if consent.is_visible():
        with page.expect_navigation(wait_until='domcontentloaded'):
            consent.click()
    page.get_by_test_id('import-file-input').set_input_files(str(csv_path))
    page.get_by_test_id('import-upload-submit').click()
    page.wait_for_load_state('domcontentloaded')
    assert page.get_by_role('heading', name='Confirm import').is_visible()


def _staff_page(browser, suffix):
    email = f'import-staff-{suffix}@example.com'
    create_staff_user(email)
    context = auth_context(browser, email)
    return email, context, context.new_page()


def _capture_matrix(page, tmp_path, label):
    for width, height, viewport in (
        (1280, 900, 'desktop'),
        (393, 852, '393'),
    ):
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            page.evaluate(
                "theme => { localStorage.setItem('theme', theme); "
                "document.documentElement.classList.toggle('dark', theme === 'dark'); }",
                theme,
            )
            assert page.evaluate(
                'document.documentElement.scrollWidth <= '
                'document.documentElement.clientWidth + 2'
            )
            page.screenshot(
                path=str(tmp_path / f'issue-1291-{label}-{viewport}-{theme}.png'),
                full_page=True,
            )


@pytest.mark.django_db(transaction=True)
class TestContactImportOutcomePreview:
    def test_garbage_default_mapping_is_blocked_without_writes(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        staff_email, context, page = _staff_page(browser, suffix)
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'garbage.csv',
            'Name,Source\nAda,event\nGrace,newsletter\n',
        )

        assert page.get_by_test_id('import-email-warning').inner_text() == (
            'Only 0/2 values in this column look like email addresses.'
        )
        assert page.get_by_test_id('import-block-reason').inner_text() == (
            'Choose another email column to continue.'
        )
        assert page.get_by_test_id('import-confirm-submit').is_disabled()
        _capture_matrix(page, tmp_path, 'zero-valid')

        from accounts.models import TierOverride, User

        assert list(User.objects.values_list('email', flat=True)) == [staff_email]
        assert TierOverride.objects.count() == 0
        context.close()

    def test_mixed_batch_preview_matches_actual_result(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        existing_email = f'existing-{suffix}@example.com'
        create_user(existing_email)
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'mixed.csv',
            'Name,Email\n'
            f'Existing,{existing_email}\n'
            f'One,one-{suffix}@example.com\n'
            f'Two,two-{suffix}@example.com\n'
            'Bad,not-an-email\n',
        )

        assert page.get_by_test_id('import-outcome-summary').inner_text() == (
            '2 new users will be created, 1 existing user will be updated, '
            '1 row will be skipped (1 invalid email, 0 duplicates).'
        )
        assert page.get_by_test_id('import-confirm-submit').is_enabled()
        _capture_matrix(page, tmp_path, 'mixed-valid')
        page.get_by_test_id('import-tag-input').fill('previewed-batch')
        page.get_by_test_id('import-confirm-submit').click()
        page.wait_for_load_state('domcontentloaded')

        assert page.get_by_test_id('import-result-created').inner_text() == '2'
        assert page.get_by_test_id('import-result-updated').inner_text() == '1'
        assert page.get_by_test_id('import-result-malformed').inner_text() == '1'
        from accounts.models import User

        assert User.objects.get(email=existing_email).tags == ['previewed-batch']
        context.close()

    def test_mapping_recomputes_without_losing_tag_or_tier(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'remap.csv',
            'Name,Contact email\n'
            f'Ada,ada-{suffix}@example.com\n'
            f'Grace,grace-{suffix}@example.com\n',
        )
        tag = page.get_by_test_id('import-tag-input')
        tier = page.get_by_test_id('import-tier-select')
        tag.fill('keep-this-tag')
        tier.select_option(label='Main')

        with page.expect_response(lambda response: response.url.endswith('/preview')):
            page.get_by_test_id('import-email-column').select_option('Contact email')
        assert page.get_by_test_id('import-confirm-submit').is_enabled()
        assert page.get_by_test_id('import-outcome-summary').inner_text().startswith(
            '2 new users will be created'
        )
        assert tag.input_value() == 'keep-this-tag'
        assert tier.locator('option:checked').inner_text() == 'Main'

        with page.expect_response(lambda response: response.url.endswith('/preview')):
            page.get_by_test_id('import-email-column').select_option('Name')
        assert page.get_by_test_id('import-confirm-submit').is_disabled()
        assert page.get_by_test_id('import-block-reason').is_visible()
        assert tag.input_value() == 'keep-this-tag'
        assert tier.locator('option:checked').inner_text() == 'Main'
        context.close()

    def test_duplicate_and_plus_address_counts_match_apply(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        base = f'person-{suffix}@example.com'
        plus = f'person-{suffix}+offer@example.com'
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'duplicates.csv',
            f'Email\n{base}\n{base.upper()}\n{plus}\ninvalid\n',
        )

        assert page.get_by_test_id('import-outcome-summary').inner_text() == (
            '2 new users will be created, 0 existing users will be updated, '
            '2 rows will be skipped (1 invalid email, 1 duplicate).'
        )
        page.get_by_test_id('import-confirm-submit').click()
        page.wait_for_load_state('domcontentloaded')
        assert page.get_by_test_id('import-result-created').inner_text() == '2'
        assert page.get_by_test_id('import-result-skipped').inner_text() == '1'
        assert page.get_by_test_id('import-result-malformed').inner_text() == '1'

        from accounts.models import User

        assert User.objects.filter(email__in=[base, plus]).count() == 2
        context.close()

    def test_database_change_forces_review_before_one_successful_apply(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        email = f'race-{suffix}@example.com'
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'race.csv',
            f'Email\n{email}\n',
        )
        from accounts.models import User

        User.objects.create_user(email=email, password=None)
        connection.close()
        page.get_by_test_id('import-tag-input').fill('after-review')
        page.get_by_test_id('import-confirm-submit').click()
        page.wait_for_load_state('domcontentloaded')
        assert page.get_by_test_id('import-error').inner_text() == (
            'Import outcome changed. Review the updated counts and confirm again.'
        )
        assert User.objects.get(email=email).tags == []

        page.get_by_test_id('import-confirm-submit').click()
        page.wait_for_load_state('domcontentloaded')
        assert page.get_by_test_id('import-result-updated').inner_text() == '1'
        assert User.objects.get(email=email).tags == ['after-review']
        context.close()

    def test_keyboard_mapping_feedback_keeps_focus_and_exposes_loading(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'keyboard.csv',
            f'Name,Contact email\nAda,ada-{suffix}@example.com\n',
        )
        select = page.get_by_test_id('import-email-column')
        select.focus()
        with page.expect_response(lambda response: response.url.endswith('/preview')):
            select.evaluate(
                "element => { element.value = 'Contact email'; "
                "element.dispatchEvent(new Event('change', {bubbles: true})); }"
            )
            assert page.get_by_test_id('import-preview-loading').is_visible()
            assert page.get_by_test_id('import-confirm-submit').is_disabled()
        assert select.evaluate('element => document.activeElement === element')
        assert page.get_by_test_id('import-confirm-submit').is_enabled()
        assert page.get_by_test_id('import-outcome-card').get_attribute('aria-live') == 'polite'
        context.close()

    def test_expired_upload_recovers_without_import_or_raw_json(
        self, django_server, browser, tmp_path,
    ):
        suffix = os.urandom(4).hex()
        _staff_email, context, page = _staff_page(browser, suffix)
        email = f'expired-{suffix}@example.com'
        connection.close()
        _start_import(
            page,
            django_server,
            tmp_path / 'expired.csv',
            f'Email\n{email}\n',
        )
        session_cookie = next(
            cookie for cookie in context.cookies() if cookie['name'] == 'sessionid'
        )
        session = SessionStore(session_key=session_cookie['value'])
        session.pop('studio_user_import_payload', None)
        session.save()
        connection.close()

        response = page.request.post(
            f'{django_server}/studio/users/import/preview',
            form={'email_column': 'Email'},
            headers={'X-CSRFToken': page.locator('[name="csrfmiddlewaretoken"]').input_value()},
        )
        assert response.status == 400
        body = response.json()
        assert body['code'] == 'upload_session_expired'
        assert email not in str(body)

        page.get_by_test_id('import-confirm-submit').click()
        page.wait_for_load_state('domcontentloaded')
        assert '/studio/users/import/' in page.url
        from accounts.models import User

        assert not User.objects.filter(email=email).exists()
        context.close()

    def test_studio_and_api_import_remain_operator_only(
        self, django_server, browser,
    ):
        suffix = os.urandom(4).hex()
        anonymous = browser.new_context()
        page = anonymous.new_page()
        page.goto(f'{django_server}/studio/users/import/', wait_until='domcontentloaded')
        assert '/accounts/login/' in page.url
        anonymous.close()

        member_email = f'member-{suffix}@example.com'
        create_user(member_email)
        member = auth_context(browser, member_email)
        page = member.new_page()
        page.goto(f'{django_server}/studio/users/import/', wait_until='domcontentloaded')
        assert page.locator('body').inner_text() == 'Staff access required'
        response = page.request.post(
            f'{django_server}/api/contacts/import',
            data={'contacts': [], 'dry_run': True},
        )
        assert response.status == 401
        member.close()
