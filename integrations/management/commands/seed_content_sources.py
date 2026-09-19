"""Seed package content sources for the canonical AISL repos (A2.3).

Creates or updates package-owned ``ContentSource`` rows. Declarations carry
no secrets: the webhook secret is resolved per source, database-to-database
only (issue #1766):

1. the row's existing nonblank value (never overwritten);
2. the legacy ``integrations.ContentSource`` mirror copy (existing behavior);
3. inheritance from sibling package rows that hold a configured secret,
   only when every configured sibling holds the same value. On
   disagreement nothing is inherited, a warning naming the affected repos
   is printed (never any secret material), and the row stays disabled;
4. blank: the row is left disabled with an actionable message.

Sources without a resolved secret are created (or left) disabled.
Idempotent; never prints secret material; never reads secrets from env
vars, settings, or code.
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


def configured_sibling_secrets(repo_name):
    """Stripped nonblank secrets held by sibling package rows.

    Returns a list of ``(repo_name, secret)`` tuples for every other
    package ``ContentSource`` row that holds a configured webhook secret.
    Database-to-database only: no env vars, settings, or code literals.
    """
    siblings = []
    rows = PackageContentSource.objects.exclude(
        repo_name=repo_name,
    ).values_list('repo_name', 'webhook_secret')
    for sibling_repo, sibling_secret in rows:
        stripped = (sibling_secret or '').strip()
        if stripped:
            siblings.append((sibling_repo, stripped))
    return siblings


class Command(BaseCommand):
    help = 'Seed package content sources (secrets stay in the database)'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        disabled = []
        disagreement_repos = None

        for declaration in DEFAULT_SOURCES:
            repo_name = declaration['repo_name']
            source = PackageContentSource.objects.filter(repo_name=repo_name).first()
            created = source is None
            source = source or PackageContentSource(repo_name=repo_name)

            # Secret resolution order (issue #1766): the row's own value,
            # then the legacy mirror copy, then configured siblings (all
            # must agree), else blank.
            existing_secret = (source.webhook_secret or '').strip()
            secret = existing_secret
            if not secret:
                legacy = LegacyContentSource.objects.filter(
                    repo_name=repo_name,
                ).first()
                legacy_secret = (legacy.webhook_secret or '').strip() if legacy else ''
                if legacy_secret:
                    secret = legacy_secret
                else:
                    siblings = configured_sibling_secrets(repo_name)
                    values = {s for _, s in siblings}
                    if len(values) > 1:
                        # Fail closed: configured siblings disagree, so
                        # inherit nothing; a warning is emitted after the
                        # loop (never any secret material).
                        if disagreement_repos is None:
                            disagreement_repos = sorted(r for r, _ in siblings)
                    elif values:
                        secret = next(iter(values))

            if secret and not existing_secret:
                # Only fill blanks; never overwrite an operator-set value.
                source.webhook_secret = secret

            is_enabled = bool(secret)
            source.slug = declaration['slug']
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

        if disagreement_repos:
            self.stdout.write(self.style.WARNING(
                '  WARNING: content sources disagree on the webhook secret '
                f'({", ".join(disagreement_repos)}); blank sources were not '
                'configured. Make the values match in Studio > Content sync, '
                'then re-run seed_content_sources.'
            ))

        for repo_name in disabled:
            self.stdout.write(
                f'  DISABLED: {repo_name} has no webhook secret. '
                f'Set one (Studio > Content sync > Edit), configure any '
                f'other content source so this seed can inherit it, or set '
                f'integrations.ContentSource.webhook_secret, then re-run '
                f'seed_content_sources to enable it.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {created_count} created, {updated_count} updated, '
                f'{len(disabled)} left disabled.'
            )
        )
