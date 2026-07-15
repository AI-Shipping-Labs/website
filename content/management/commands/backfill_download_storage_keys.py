"""Audit and optionally backfill secure storage keys for legacy downloads."""

from django.core.management.base import BaseCommand, CommandError

from content.models import Download
from content.services.download_delivery import get_downloads_s3_config
from content.services.download_validation import (
    DownloadMetadataError,
    storage_key_from_configured_s3_url,
    validate_download_metadata,
)


class Command(BaseCommand):
    help = 'Audit legacy download URLs and optionally backfill private S3 keys.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Persist safe mappings. Without this flag the command is dry-run.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        config = get_downloads_s3_config()
        bucket = config['bucket']
        region = config['region']
        if not bucket:
            raise CommandError(
                'AWS_S3_DOWNLOADS_BUCKET is not configured; no rows were changed.',
            )

        mapped = ready = unresolved = 0
        for download in Download.objects.order_by('pk'):
            if download.delivery_ready:
                ready += 1
                continue
            try:
                key = storage_key_from_configured_s3_url(
                    download.file_url,
                    bucket,
                    region,
                )
                metadata = validate_download_metadata(
                    storage_key=key,
                    file_type=download.file_type,
                    file_size_bytes=download.file_size_bytes,
                    required_level=download.required_level,
                    asset_mime_type=download.asset_mime_type,
                )
            except DownloadMetadataError as exc:
                unresolved += 1
                self.stdout.write(f'UNRESOLVED {download.slug}: {exc}')
                continue
            mapped += 1
            self.stdout.write(f'MAPPABLE {download.slug}')
            if apply_changes:
                for field, value in metadata.items():
                    setattr(download, field, value)
                download.delivery_blocked_reason = ''
                download.save(update_fields=[
                    'storage_key',
                    'file_type',
                    'file_size_bytes',
                    'required_level',
                    'asset_mime_type',
                    'delivery_blocked_reason',
                    'updated_at',
                ])

        mode = 'APPLY' if apply_changes else 'DRY RUN'
        self.stdout.write(
            self.style.SUCCESS(
                f'{mode}: ready={ready} mappable={mapped} unresolved={unresolved}',
            ),
        )
