"""Playwright coverage for Studio/API Call profiles (#1404)."""

import os

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = pytest.mark.local_only

BOOKING_URL = 'https://calendar.app.google/jordan-profile'


def _complete_onboarding(email):
    from django.db import connection

    from accounts.models import User
    from questionnaires.models import Questionnaire, Response
    from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG

    user = User.objects.get(email=email)
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug=GENERIC_ONBOARDING_SLUG,
        defaults={'title': 'Onboarding', 'purpose': 'onboarding'},
    )
    Response.objects.get_or_create(
        questionnaire=questionnaire,
        respondent=user,
        defaults={'status': 'submitted'},
    )
    connection.close()


def _create_profile(**overrides):
    from django.db import connection

    from community.models import CallHost

    values = {
        'name': 'Jordan Lee',
        'slug': 'jordan',
        'role_label': 'AI product coach',
        'booking_url': BOOKING_URL,
        'is_active': True,
        'order': 5,
    }
    values.update(overrides)
    profile = CallHost.objects.create(**values)
    profile_id = profile.pk
    connection.close()
    return profile_id


def _fill_profile_form(page, *, name='Jordan Lee', booking_url=BOOKING_URL):
    page.get_by_label('Name').fill(name)
    page.get_by_label('Slug').fill('jordan')
    page.get_by_label('Role label').fill('AI product coach')
    page.get_by_label('Booking URL').fill(booking_url)
    page.get_by_label('Display order').fill('5')


@pytest.mark.django_db(transaction=True)
class TestStudioCallProfileMemberJourney:
    @pytest.mark.core
    def test_staff_adds_profile_and_member_uses_booking_link(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-call-profile-1404@test.com')
            create_user('member-call-profile-1404@test.com', tier_slug='free')
            _complete_onboarding('member-call-profile-1404@test.com')
            from django.db import connection

            from community.models import BookedCall, CallHost

            BookedCall.objects.all().delete()
            CallHost.objects.all().delete()
            connection.close()

        staff_context = auth_context(browser, 'staff-call-profile-1404@test.com')
        try:
            page = staff_context.new_page()
            page.goto(f'{django_server}/studio/call-hosts/', wait_until='domcontentloaded')
            empty_state_cta = page.get_by_test_id('studio-empty-state-fresh').get_by_role(
                'link', name='New call profile', exact=True,
            )
            assert empty_state_cta.get_attribute('href') == '/studio/call-hosts/new'
            header_cta = page.get_by_test_id('studio-header-actions').get_by_role(
                'link', name='New call profile', exact=True,
            )
            assert header_cta.get_attribute('href') == '/studio/call-hosts/new'
            header_cta.click()
            _fill_profile_form(page)
            page.get_by_label('Show on Request a call').check()
            page.get_by_test_id('sticky-save-action').click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            assert 'Call profile “Jordan Lee” created.' in page.locator('body').inner_text()
            row = page.locator('[data-host-slug="jordan"]')
            assert row.get_by_test_id('call-profile-visibility').inner_text() == 'Shown'
            assert row.get_by_test_id('call-profile-booking-link').get_attribute('href') == BOOKING_URL
            for forbidden in ('Capacity', 'Current load', 'Availability'):
                assert forbidden not in page.locator('main').inner_text()
        finally:
            staff_context.close()

        member_context = auth_context(browser, 'member-call-profile-1404@test.com')
        try:
            page = member_context.new_page()
            page.goto(f'{django_server}/request-a-call', wait_until='domcontentloaded')
            card = page.locator('[data-host-slug="jordan"]')
            card.wait_for(state='visible')
            link = card.get_by_role('link', name='Book a call')
            assert link.get_attribute('href') == BOOKING_URL
            assert link.get_attribute('target') == '_blank'
            assert link.get_attribute('rel') == 'noopener noreferrer'
            assert 'capacity' not in card.inner_text().lower()
            assert 'availability' not in card.inner_text().lower()
        finally:
            member_context.close()

    def test_hidden_profile_can_be_prepared_then_published(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-hidden-1404@test.com')
            create_user('member-hidden-1404@test.com', tier_slug='free')
            _complete_onboarding('member-hidden-1404@test.com')

        staff_context = auth_context(browser, 'staff-hidden-1404@test.com')
        try:
            page = staff_context.new_page()
            page.goto(f'{django_server}/studio/call-hosts/new', wait_until='domcontentloaded')
            _fill_profile_form(page, booking_url='')
            page.get_by_label('Show on Request a call').uncheck()
            page.get_by_test_id('sticky-save-action').click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            row = page.locator('[data-host-slug="jordan"]')
            assert row.get_by_test_id('call-profile-visibility').inner_text() == 'Hidden'
            row.get_by_role('link', name='Edit').click()
            page.get_by_label('Booking URL').fill(BOOKING_URL)
            page.get_by_label('Show on Request a call').check()
            page.get_by_test_id('sticky-save-action').click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            assert page.locator('[data-host-slug="jordan"]').get_by_test_id(
                'call-profile-visibility',
            ).inner_text() == 'Shown'
        finally:
            staff_context.close()

        member_context = auth_context(browser, 'member-hidden-1404@test.com')
        try:
            page = member_context.new_page()
            page.goto(f'{django_server}/request-a-call', wait_until='domcontentloaded')
            assert page.locator('[data-host-slug="jordan"]').count() == 1
        finally:
            member_context.close()

    def test_active_linkless_edit_preserves_values_and_stays_hidden(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-invalid-1404@test.com')
            profile_id = _create_profile(booking_url='', is_active=False)

        context = auth_context(browser, 'staff-invalid-1404@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/studio/call-hosts/{profile_id}/edit',
                wait_until='domcontentloaded',
            )
            page.get_by_label('Name').fill('Jordan Preserved')
            page.get_by_label('Show on Request a call').check()
            page.get_by_test_id('sticky-save-action').click()
            page.get_by_test_id('error-booking-url').wait_for(state='visible')
            assert 'Booking URL is required' in page.get_by_test_id('error-booking-url').inner_text()
            assert page.get_by_label('Name').input_value() == 'Jordan Preserved'
            with django_db_blocker.unblock():
                from community.models import CallHost

                profile = CallHost.objects.get(pk=profile_id)
                assert profile.name == 'Jordan Lee'
                assert profile.is_active is False
        finally:
            context.close()

    def test_staff_edits_and_reorders_profiles_without_capacity_controls(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-reorder-1404@test.com')
            create_user('member-reorder-1404@test.com', tier_slug='free')
            _complete_onboarding('member-reorder-1404@test.com')
            _create_profile(name='First profile', slug='first-profile', order=20)
            second_id = _create_profile(
                name='Second profile',
                slug='second-profile',
                order=30,
                booking_url='https://calendar.app.google/second-old',
            )

        staff_context = auth_context(browser, 'staff-reorder-1404@test.com')
        try:
            page = staff_context.new_page()
            page.goto(
                f'{django_server}/studio/call-hosts/{second_id}/edit',
                wait_until='domcontentloaded',
            )
            page.get_by_label('Name').fill('Second profile updated')
            page.get_by_label('Role label').fill('Updated call profile role')
            page.get_by_label('Booking URL').fill(BOOKING_URL)
            page.get_by_label('Display order').fill('10')
            for forbidden in ('Capacity', 'Current load', 'Open spots', 'Availability'):
                assert forbidden not in page.locator('main').inner_text()
            page.get_by_test_id('sticky-save-action').click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            assert 'Call profile “Second profile updated” updated.' in page.locator('body').inner_text()
            studio_order = page.locator('tbody tr').evaluate_all(
                'rows => rows.map(row => row.dataset.hostSlug)',
            )
            assert studio_order.index('second-profile') < studio_order.index('first-profile')
        finally:
            staff_context.close()

        member_context = auth_context(browser, 'member-reorder-1404@test.com')
        try:
            page = member_context.new_page()
            page.goto(f'{django_server}/request-a-call', wait_until='domcontentloaded')
            member_order = page.locator('[data-testid="call-host-card"]').evaluate_all(
                'cards => cards.map(card => card.dataset.hostSlug)',
            )
            assert member_order.index('second-profile') < member_order.index('first-profile')
            updated_card = page.locator('[data-host-slug="second-profile"]')
            assert 'Second profile updated' in updated_card.inner_text()
            assert 'Updated call profile role' in updated_card.inner_text()
            assert updated_card.get_by_role('link', name='Book a call').get_attribute('href') == BOOKING_URL
        finally:
            member_context.close()

    def test_member_sees_truthful_empty_state_for_hidden_or_linkless_profiles(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user('member-empty-1404@test.com', tier_slug='free')
            _complete_onboarding('member-empty-1404@test.com')
            from community.models import CallHost

            CallHost.objects.update(is_active=False)
            _create_profile(slug='hidden-profile', is_active=False)
            _create_profile(slug='linkless-profile', booking_url='', is_active=True)

        context = auth_context(browser, 'member-empty-1404@test.com')
        try:
            page = context.new_page()
            page.goto(f'{django_server}/request-a-call', wait_until='domcontentloaded')
            empty_state = page.get_by_test_id('request-call-empty')
            empty_state.wait_for(state='visible')
            assert 'No call profiles available' in empty_state.inner_text()
            assert (
                'There are no call booking links available right now. Check back soon.'
                in empty_state.inner_text()
            )
            assert page.locator('[data-testid="call-host-card"]').count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioCallProfileDeletion:
    def test_staff_starts_from_canonical_empty_state(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-empty-1404@test.com')
            from community.models import BookedCall, CallHost

            BookedCall.objects.all().delete()
            CallHost.objects.all().delete()

        context = auth_context(browser, 'staff-empty-1404@test.com')
        try:
            page = context.new_page()
            page.goto(f'{django_server}/studio/call-hosts/', wait_until='domcontentloaded')
            assert page.locator('table').count() == 0
            assert 'No call profiles yet.' in page.get_by_test_id(
                'studio-empty-state-fresh',
            ).inner_text()
            page.get_by_role('link', name='New call profile', exact=True).first.click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/new')
            assert page.get_by_label('Name').count() == 1
            assert page.get_by_label('Capacity').count() == 0
            assert page.get_by_label('Current load').count() == 0
        finally:
            context.close()

    def test_staff_deletes_unused_profile_with_named_confirmation(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-delete-1404@test.com')
            _create_profile(is_active=False)

        context = auth_context(browser, 'staff-delete-1404@test.com')
        try:
            page = context.new_page()
            page.goto(f'{django_server}/studio/call-hosts/', wait_until='domcontentloaded')
            confirmation = []

            def accept_dialog(dialog):
                confirmation.append(dialog.message)
                dialog.accept()

            page.once('dialog', accept_dialog)
            page.locator('[data-host-slug="jordan"]').get_by_role(
                'button', name='Delete', exact=True,
            ).click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            assert confirmation == [
                'Delete call profile “Jordan Lee”? This removes its booking link '
                'from member pages and cannot be undone.',
            ]
            assert 'Call profile “Jordan Lee” deleted.' in page.locator('body').inner_text()
            assert page.locator('[data-host-slug="jordan"]').count() == 0
        finally:
            context.close()

    def test_booked_call_history_blocks_delete_and_profile_can_be_hidden(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_staff_user('staff-history-1404@test.com')
            profile_id = _create_profile()
            from community.models import STATUS_CANCELED, BookedCall, CallHost

            profile = CallHost.objects.get(pk=profile_id)
            BookedCall.objects.create(
                host=profile,
                invitee_email='history@example.com',
                status=STATUS_CANCELED,
                calendly_event_uri='https://api.calendly.com/scheduled_events/pw-history',
            )

        context = auth_context(browser, 'staff-history-1404@test.com')
        try:
            page = context.new_page()
            page.goto(f'{django_server}/studio/call-hosts/', wait_until='domcontentloaded')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('[data-host-slug="jordan"]').get_by_role(
                'button', name='Delete', exact=True,
            ).click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/{profile_id}/edit')
            assert 'booked-call history' in page.locator('body').inner_text()
            page.get_by_label('Show on Request a call').uncheck()
            page.get_by_test_id('sticky-save-action').click()
            page.wait_for_url(f'{django_server}/studio/call-hosts/')
            assert page.locator('[data-host-slug="jordan"]').get_by_test_id(
                'call-profile-visibility',
            ).inner_text() == 'Hidden'
            with django_db_blocker.unblock():
                from community.models import BookedCall, CallHost

                assert CallHost.objects.filter(pk=profile_id, is_active=False).exists()
                assert BookedCall.objects.filter(host_id=profile_id).exists()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestCallProfileApiJourney:
    @pytest.mark.core
    def test_api_creates_publishes_hides_and_deletes_profile(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            staff = create_staff_user('staff-api-1404@test.com')
            create_user('member-api-1404@test.com', tier_slug='free')
            _complete_onboarding('member-api-1404@test.com')
            from accounts.models import Token

            _token, plaintext = Token.create_for_user(user=staff, name='pw-call-profile')

        context = browser.new_context(
            extra_http_headers={'Authorization': f'Token {plaintext}'},
        )
        try:
            created = context.request.post(
                f'{django_server}/api/call-profiles',
                data={
                    'name': 'API Profile',
                    'slug': 'api-profile',
                    'booking_url': '',
                    'is_active': False,
                    'order': 1,
                },
            )
            assert created.status == 201
            assert 'capacity' not in created.json()

            published = context.request.patch(
                f'{django_server}/api/call-profiles/api-profile',
                data={'booking_url': BOOKING_URL, 'is_active': True},
            )
            assert published.status == 200
            assert published.json()['is_active'] is True

            member_context = auth_context(browser, 'member-api-1404@test.com')
            try:
                page = member_context.new_page()
                page.goto(f'{django_server}/request-a-call', wait_until='domcontentloaded')
                assert page.locator('[data-host-slug="api-profile"]').count() == 1
            finally:
                member_context.close()

            hidden = context.request.patch(
                f'{django_server}/api/call-profiles/api-profile',
                data={'is_active': False},
            )
            assert hidden.status == 200
            deleted = context.request.delete(
                f'{django_server}/api/call-profiles/api-profile',
            )
            assert deleted.status == 204
            assert context.request.get(
                f'{django_server}/api/call-profiles/api-profile',
            ).status == 404
        finally:
            context.close()

    @pytest.mark.core
    def test_unauthorized_studio_and_api_mutations_have_no_side_effects(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            create_user('nonstaff-1404@test.com', tier_slug='free')
            profile_id = _create_profile(is_active=False)

        context = auth_context(browser, 'nonstaff-1404@test.com')
        try:
            page = context.new_page()
            for path in (
                '/studio/call-hosts/new',
                f'/studio/call-hosts/{profile_id}/edit',
            ):
                response = page.goto(
                    f'{django_server}{path}',
                    wait_until='domcontentloaded',
                )
                assert response.status == 403
            studio_responses = (
                context.request.post(
                    f'{django_server}/studio/call-hosts/new',
                    form={'name': 'Unauthorized create'},
                ),
                context.request.post(
                    f'{django_server}/studio/call-hosts/{profile_id}/edit',
                    form={'name': 'Unauthorized edit'},
                ),
                context.request.post(
                    f'{django_server}/studio/call-hosts/{profile_id}/delete',
                ),
            )
            assert [response.status for response in studio_responses] == [403, 403, 403]
            api_responses = (
                context.request.post(
                    f'{django_server}/api/call-profiles',
                    data={'name': 'Unauthorized create'},
                ),
                context.request.patch(
                    f'{django_server}/api/call-profiles/jordan',
                    data={'name': 'Unauthorized edit'},
                ),
                context.request.delete(
                    f'{django_server}/api/call-profiles/jordan',
                ),
            )
            assert [response.status for response in api_responses] == [401, 401, 401]
            with django_db_blocker.unblock():
                from community.models import CallHost

                assert CallHost.objects.get(pk=profile_id).name == 'Jordan Lee'
                assert not CallHost.objects.filter(name__startswith='Unauthorized').exists()
        finally:
            context.close()

    def test_api_delete_protects_history_then_allows_hiding(
        self, django_server, django_db_blocker, browser,
    ):
        with django_db_blocker.unblock():
            staff = create_staff_user('staff-api-history-1404@test.com')
            profile_id = _create_profile()
            from accounts.models import Token
            from community.models import STATUS_CANCELED, BookedCall, CallHost

            _token, plaintext = Token.create_for_user(
                user=staff,
                name='pw-call-profile-history',
            )
            BookedCall.objects.create(
                host=CallHost.objects.get(pk=profile_id),
                invitee_email='api-history@example.com',
                status=STATUS_CANCELED,
                calendly_event_uri='https://api.calendly.com/scheduled_events/api-history',
            )

        context = browser.new_context(
            extra_http_headers={'Authorization': f'Token {plaintext}'},
        )
        try:
            deleted = context.request.delete(
                f'{django_server}/api/call-profiles/jordan',
            )
            assert deleted.status == 409
            assert deleted.json() == {
                'error': (
                    "This call profile has booked-call history and can't be deleted. "
                    'Hide it with PATCH is_active=false instead.'
                ),
                'code': 'call_profile_in_use',
            }
            hidden = context.request.patch(
                f'{django_server}/api/call-profiles/jordan',
                data={'is_active': False},
            )
            assert hidden.status == 200
            with django_db_blocker.unblock():
                from community.models import BookedCall, CallHost

                assert CallHost.objects.filter(pk=profile_id, is_active=False).exists()
                assert BookedCall.objects.filter(host_id=profile_id).exists()
        finally:
            context.close()
