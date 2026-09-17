"""Seed package content sources for the canonical AISL repos (A2.3).

Creates or updates package-owned ``ContentSource`` rows. Declarations carry
no secrets: the webhook secret is preserved from the legacy
``integrations.ContentSource`` row (database-to-database) when one exists.
Sources without a secret are created disabled with an actionable message.
Idempotent; never prints secret material.
"""

from community_base.content_sync.models import ContentSource as PackageContentSource
from django.core.management.base import BaseCommand

from integrations.models import ContentSource as LegacyContentSource

DEFAULT_SOURCES = [
    {
        'slug': 'content',
        'repo_name': 'AI-Shipping-Labs/content',
        'is_private': True,
        # The package snapshot limit counts every repository file, not just
        # content files; the legacy engine limited content files only.
        'max_files': 5000,
    },
    {
        'slug': 'python-course',
        'repo_name': 'AI-Shipping-Labs/python-course',
        'is_private': True,
        'max_files': 5000,
    },
    {
        'slug': 'workshops-content',
        'repo_name': 'AI-Shipping-Labs/workshops-content',
        'is_private': True,
        'max_files': 5000,
    },
    {
        'slug': 'ai-buildcamp-course',
        'repo_name': 'AI-Shipping-Labs/ai-buildcamp-course',
        'is_private': True,
        'max_files': 5000,
    },
    {
        # Issue #1688: the private member wiki. Topic pages from `_wiki/`
        # render under /topics/; the record directories are not synced.
        'slug': 'wiki',
        'repo_name': 'AI-Shipping-Labs/wiki',
        'is_private': True,
        'max_files': 5000,
    },
]


def derive_slug(repo_name):
    """Deterministic source slug from the repository short name."""
    short = repo_name.rsplit('/', 1)[-1]
    slug = ''.join(
        ch if ch.isalnum() else '-' for ch in short.lower()
    ).strip('-')
    return slug or 'source'


class Command(BaseCommand):
    help = 'Seed package content sources (secrets stay in the database)'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        disabled = []

        for declaration in DEFAULT_SOURCES:
            repo_name = declaration['repo_name']
            legacy = LegacyContentSource.objects.filter(repo_name=repo_name).first()
            secret = (legacy.webhook_secret or '') if legacy else ''
            is_enabled = bool(secret)

            source = PackageContentSource.objects.filter(repo_name=repo_name).first()
            created = source is None
            source = source or PackageContentSource(repo_name=repo_name)
            source.slug = declaration['slug']
            source.webhook_secret = secret
            source.is_private = declaration['is_private']
            # Migration rule: a source without a usable webhook secret stays
            # disabled; an existing operator-enabled row is not re-disabled.
            if created or not source.is_enabled:
                source.is_enabled = is_enabled
            source.max_files = declaration['max_files']
            source.save()

            if created:
                created_count += 1
                self.stdout.write(f'  Created content source: {repo_name}')
            else:
                updated_count += 1
                self.stdout.write(f'  Updated content source: {repo_name}')
            if not is_enabled:
                disabled.append(repo_name)

        for repo_name in disabled:
            self.stdout.write(
                f'  DISABLED: {repo_name} has no webhook secret. '
                f'Set one (Studio > Content sync > Edit, or '
                f'integrations.ContentSource.webhook_secret) and re-run '
                f'seed_content_sources to enable it.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {created_count} created, {updated_count} updated, '
                f'{len(disabled)} left disabled.'
            )
        )
