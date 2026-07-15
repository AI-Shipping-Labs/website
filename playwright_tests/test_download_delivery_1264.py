"""Browser journeys for privacy-safe tiered download delivery (#1264)."""

import os
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
pytestmark = pytest.mark.local_only


def _seed_download(*, slug='browser-guide', required_level=0):
    from django.db import connection

    from content.models import Download

    Download.objects.all().delete()
    download = Download.objects.create(
        title='Browser AI field guide',
        slug=slug,
        description='A practical PDF for shipping reliable AI systems.',
        file_url='',
        storage_key=f'downloads/{slug}.pdf',
        file_type='pdf',
        asset_mime_type='application/pdf',
        file_size_bytes=2048,
        required_level=required_level,
        tags=['agents', 'shipping'],
        published=True,
    )
    connection.close()
    return download


@pytest.mark.django_db(transaction=True)
class TestDownloadDelivery1264:
    @pytest.mark.core
    def test_real_mailbox_verify_presign_count_and_activity_journey(
        self,
        django_server,
        django_db_blocker,
        page,
        monkeypatch,
    ):
        mailbox = {}
        with django_db_blocker.unblock():
            download = _seed_download(slug='real-mailbox-guide')

        def capture_mail(_service, user, template_name, context, **_kwargs):
            mailbox.update({
                'recipient': user.email,
                'template': template_name,
                **context,
            })

        monkeypatch.setattr(
            'content.services.download_requests.EmailService.send',
            capture_mail,
        )
        monkeypatch.setattr(
            'content.services.download_requests.site_base_url',
            lambda: django_server,
        )
        monkeypatch.setattr(
            'content.services.download_delivery.verify_download_object_exists',
            lambda _key: None,
        )
        monkeypatch.setattr(
            'content.services.download_delivery.build_download_presigned_url',
            lambda item: f'{django_server}{item.get_absolute_url()}?delivered=1',
        )

        page.goto(f'{django_server}{download.get_absolute_url()}')
        form = page.get_by_test_id('download-request-form')
        form.locator('input[name="email"]').fill('real-mailbox@example.com')
        form.get_by_test_id('download-request-submit').click()
        expect(page.get_by_test_id('download-request-success')).to_be_visible()
        assert mailbox['recipient'] == 'real-mailbox@example.com'
        assert mailbox['template'] == 'download_delivery'
        assert mailbox['verification_required'] is True

        page.goto(mailbox['delivery_url'], wait_until='domcontentloaded')
        expect(page).to_have_url(
            f'{django_server}{download.get_absolute_url()}?delivered=1',
        )
        with django_db_blocker.unblock():
            from analytics.models import UserActivity
            from content.models import Download, DownloadDeliveryGrant

            delivered = Download.objects.get(pk=download.pk)
            grant = DownloadDeliveryGrant.objects.get(download=delivered)
            assert delivered.download_count == 1
            assert grant.redeemed_at is not None
            assert grant.user.email_verified is True
            assert UserActivity.objects.filter(
                user=grant.user,
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
                object_type='download',
                object_id=download.slug,
            ).exists()

    @pytest.mark.core
    def test_existing_address_receives_direct_one_time_delivery(
        self,
        django_server,
        django_db_blocker,
        page,
        monkeypatch,
    ):
        mailbox = {}
        with django_db_blocker.unblock():
            download = _seed_download(slug='existing-address-guide')
            user = create_user(
                'existing-download@example.com',
                tier_slug='free',
                password=DEFAULT_PASSWORD,
            )
            user.email_verified = True
            user.save(update_fields=['email_verified'])

        def capture_mail(_service, recipient, template_name, context, **_kwargs):
            mailbox.update({
                'recipient': recipient.email,
                'template': template_name,
                **context,
            })

        monkeypatch.setattr(
            'content.services.download_requests.EmailService.send',
            capture_mail,
        )
        monkeypatch.setattr(
            'content.services.download_requests.site_base_url',
            lambda: django_server,
        )
        monkeypatch.setattr(
            'content.services.download_delivery.verify_download_object_exists',
            lambda _key: None,
        )
        monkeypatch.setattr(
            'content.services.download_delivery.build_download_presigned_url',
            lambda item: f'{django_server}{item.get_absolute_url()}?existing=1',
        )

        page.goto(f'{django_server}{download.get_absolute_url()}')
        form = page.get_by_test_id('download-request-form')
        form.locator('input[name="email"]').fill(user.email)
        form.get_by_test_id('download-request-submit').click()
        expect(page.get_by_test_id('download-request-success')).to_be_visible()
        assert mailbox['verification_required'] is False
        page.goto(mailbox['delivery_url'], wait_until='domcontentloaded')
        expect(page).to_have_url(
            f'{django_server}{download.get_absolute_url()}?existing=1',
        )
        with django_db_blocker.unblock():
            from content.models import Download

            assert Download.objects.get(pk=download.pk).download_count == 1

    @pytest.mark.core
    def test_operator_backfill_then_synced_row_is_read_only(
        self,
        django_server,
        django_db_blocker,
        browser,
        monkeypatch,
    ):
        with django_db_blocker.unblock():
            from io import StringIO

            from django.core.management import call_command

            from content.models import Download

            Download.objects.all().delete()
            download = Download.objects.create(
                title='Backfilled synced guide',
                slug='backfilled-synced-guide',
                file_url=(
                    'https://private.s3.eu-central-1.amazonaws.com/'
                    'downloads/backfilled-synced-guide.pdf'
                ),
                storage_key='',
                file_type='pdf',
                file_size_bytes=2048,
                required_level=10,
                published=True,
            )
            monkeypatch.setattr(
                'content.management.commands.backfill_download_storage_keys.get_downloads_s3_config',
                lambda: {'bucket': 'private', 'region': 'eu-central-1'},
            )
            call_command(
                'backfill_download_storage_keys',
                '--apply',
                stdout=StringIO(),
            )
            download.refresh_from_db()
            assert download.storage_key == 'downloads/backfilled-synced-guide.pdf'
            assert download.delivery_ready
            download.source_repo = 'AI-Shipping-Labs/resources'
            download.source_path = 'downloads/backfilled-synced-guide.yaml'
            download.source_commit = 'abc123def456'
            download.save(update_fields=[
                'source_repo',
                'source_path',
                'source_commit',
            ])
            staff = create_staff_user('download-operator@example.com')

        context = auth_context(browser, staff.email)
        try:
            page = context.new_page()
            page.goto(f'{django_server}/studio/downloads/{download.pk}/edit')
            expect(page.get_by_test_id('origin-panel')).to_be_visible()
            expect(page.get_by_test_id('origin-panel')).to_contain_text(
                'Synced from GitHub',
            )
            expect(page.locator('input[name="storage_key"]')).to_be_disabled()
            expect(page.get_by_test_id('download-count')).to_contain_text(
                'Successful downloads: 0',
            )
        finally:
            context.close()

    @pytest.mark.core
    def test_anonymous_catalog_hands_off_to_clean_detail_form(
        self, django_server, django_db_blocker, page,
    ):
        with django_db_blocker.unblock():
            download = _seed_download()

        page.goto(f'{django_server}/downloads', wait_until='domcontentloaded')
        card = page.get_by_test_id('download-card')
        expect(card).to_contain_text(download.title)
        expect(card.locator('input[type="email"]')).to_have_count(0)
        card.get_by_test_id('download-card-body-link').click()
        expect(page).to_have_url(
            f'{django_server}/downloads/{download.slug}?surface=catalog',
        )
        form = page.get_by_test_id('download-request-form')
        expect(form).to_be_visible()
        expect(form.locator('input[name="newsletter_opt_in"]')).not_to_be_checked()
        expect(page.locator('body')).not_to_contain_text(download.storage_key)

        page.route(
            f'**/api/downloads/{download.slug}/request',
            lambda route: route.fulfill(
                status=202,
                content_type='application/json',
                body='{"status":"accepted","message":"Check your email."}',
            ),
        )
        form.locator('input[name="email"]').fill('browser-lead@example.com')
        card_height = page.get_by_test_id('download-access-card').bounding_box()['height']
        form.get_by_test_id('download-request-submit').click()
        expect(page.get_by_test_id('download-request-success')).to_be_visible()
        expect(page.get_by_test_id('download-request-success')).to_contain_text(
            'Check your email',
        )
        success_height = page.get_by_test_id('download-access-card').bounding_box()['height']
        assert abs(success_height - card_height) <= 1

    @pytest.mark.core
    @pytest.mark.parametrize(
        ('status', 'message'),
        [(429, 'Too many requests.'), (503, 'We could not send the email.')],
        ids=['rate-limit', 'delivery-failure'],
    )
    def test_request_errors_preserve_layout_focus_and_retry(
        self, django_server, django_db_blocker, page, status, message,
    ):
        with django_db_blocker.unblock():
            download = _seed_download(slug=f'error-{status}')
        page.goto(f'{django_server}{download.get_absolute_url()}')
        form = page.get_by_test_id('download-request-form')
        form.locator('input[name="email"]').fill('retry@example.com')
        initial_height = page.get_by_test_id('download-access-card').bounding_box()['height']
        attempts = {'count': 0}

        def fulfill(route):
            attempts['count'] += 1
            if attempts['count'] == 1:
                route.fulfill(
                    status=status,
                    content_type='application/json',
                    body=f'{{"error":"{message}"}}',
                )
            else:
                route.fulfill(
                    status=202,
                    content_type='application/json',
                    body='{"status":"accepted"}',
                )

        page.route(f'**/api/downloads/{download.slug}/request?*', fulfill)
        form.get_by_test_id('download-request-submit').click()
        error = page.locator('#download-request-error')
        expect(error).to_be_visible()
        expect(error).to_be_focused()
        expect(error).to_contain_text(message)
        assert abs(
            page.get_by_test_id('download-access-card').bounding_box()['height']
            - initial_height
        ) <= 1
        expect(form.get_by_test_id('download-request-submit')).to_be_enabled()

        form.get_by_test_id('download-request-submit').click()
        expect(page.get_by_test_id('download-request-success')).to_be_visible()

    @pytest.mark.core
    def test_anonymous_paid_download_shows_tier_gate_not_capture(
        self, django_server, django_db_blocker, page,
    ):
        with django_db_blocker.unblock():
            download = _seed_download(slug='premium-guide', required_level=30)
        page.goto(download_url := f'{django_server}{download.get_absolute_url()}')
        expect(page).to_have_url(download_url)
        expect(page.get_by_test_id('download-tier-gate')).to_be_visible()
        expect(page.get_by_test_id('download-tier-gate')).to_contain_text(
            'Premium required',
        )
        expect(page.get_by_test_id('download-request-form')).to_have_count(0)
        expect(page.locator('body')).not_to_contain_text(download.storage_key)

    @pytest.mark.core
    def test_eligible_member_gets_direct_download_action(
        self, django_server, django_db_blocker, browser, monkeypatch,
    ):
        with django_db_blocker.unblock():
            download = _seed_download(slug='basic-guide', required_level=10)
            user = create_user(
                'download-basic@example.com',
                tier_slug='basic',
                password=DEFAULT_PASSWORD,
            )
            user.email_verified = True
            user.account_activated = True
            user.save(update_fields=['email_verified', 'account_activated'])

        monkeypatch.setattr(
            'content.services.download_delivery.verify_download_object_exists',
            lambda _key: None,
        )
        monkeypatch.setattr(
            'content.services.download_delivery.build_download_presigned_url',
            lambda item: f'{django_server}{item.get_absolute_url()}?member=1',
        )

        context = auth_context(browser, user.email)
        try:
            page = context.new_page()
            page.goto(f'{django_server}{download.get_absolute_url()}')
            expect(page.get_by_test_id('download-file-cta')).to_be_visible()
            expect(page.get_by_test_id('download-request-form')).to_have_count(0)
            expect(page.get_by_test_id('download-tier-gate')).to_have_count(0)
            page.get_by_test_id('download-file-cta').click()
            expect(page).to_have_url(
                f'{django_server}{download.get_absolute_url()}?member=1',
            )
            with django_db_blocker.unblock():
                from analytics.models import UserActivity
                from content.models import Download

                assert Download.objects.get(pk=download.pk).download_count == 1
                assert UserActivity.objects.filter(
                    user=user,
                    event_type=UserActivity.EVENT_RESOURCE_VIEW,
                    object_type='download',
                    object_id=download.slug,
                ).exists()
        finally:
            context.close()

    @pytest.mark.core
    def test_under_tier_delivery_link_hands_off_to_safe_recovery(
        self, django_server, django_db_blocker, page,
    ):
        with django_db_blocker.unblock():
            from content.services.download_delivery import create_delivery_grant

            download = _seed_download(slug='premium-recovery', required_level=30)
            user = create_user(
                'basic-download-requester@example.com',
                tier_slug='basic',
                password=DEFAULT_PASSWORD,
            )
            user.email_verified = True
            user.save(update_fields=['email_verified'])
            grant = create_delivery_grant(user, download)

        page.goto(
            f'{django_server}/api/downloads/{download.slug}/file?grant={quote(grant)}',
            wait_until='domcontentloaded',
        )
        expect(page).to_have_url(
            f'{django_server}{download.get_absolute_url()}?delivery=access-required',
        )
        expect(
            page.get_by_test_id('download-access-required-notice'),
        ).to_be_visible()
        expect(page.get_by_test_id('download-tier-gate')).to_be_visible()
        expect(page.get_by_test_id('download-pricing-cta')).to_be_visible()
        expect(page.locator('body')).not_to_contain_text(download.storage_key)

    @pytest.mark.core
    @pytest.mark.parametrize(
        'viewport',
        [
            {'width': 1440, 'height': 1000},
            {'width': 390, 'height': 844},
        ],
        ids=['desktop', 'mobile'],
    )
    def test_free_detail_is_responsive_without_overflow(
        self, django_server, django_db_blocker, browser, viewport,
    ):
        with django_db_blocker.unblock():
            download = _seed_download(slug=f'responsive-{viewport["width"]}')
        context = browser.new_context(viewport=viewport)
        try:
            page = context.new_page()
            page.goto(f'{django_server}{download.get_absolute_url()}')
            expect(page.get_by_test_id('download-request-form')).to_be_visible()
            overflow = page.evaluate(
                'document.documentElement.scrollWidth - document.documentElement.clientWidth',
            )
            assert overflow <= 1
        finally:
            context.close()
