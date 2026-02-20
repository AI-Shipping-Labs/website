"""
Management command to seed the four default GitHub content sources.

Idempotent: running twice does not create duplicates.
"""

from django.core.management.base import BaseCommand

from integrations.models import ContentSource


DEFAULT_SOURCES = [
    {
        'repo_name': 'AI-Shipping-Labs/blog',
        'content_type': 'article',
        'is_private': False,
    },
    {
        'repo_name': 'AI-Shipping-Labs/courses',
        'content_type': 'course',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/resources',
        'content_type': 'resource',
        'is_private': False,
    },
    {
        'repo_name': 'AI-Shipping-Labs/projects',
        'content_type': 'project',
        'is_private': False,
    },
]


class Command(BaseCommand):
    help = 'Seed the database with default GitHub content sources'

    def handle(self, *args, **options):
        count = 0
        for source_data in DEFAULT_SOURCES:
            _, created = ContentSource.objects.get_or_create(
                repo_name=source_data['repo_name'],
                defaults={
                    'content_type': source_data['content_type'],
                    'is_private': source_data['is_private'],
                },
            )
            if created:
                count += 1
                self.stdout.write(
                    f'  Created content source: {source_data["repo_name"]}'
                )
            else:
                self.stdout.write(
                    f'  Already exists: {source_data["repo_name"]}'
                )

        self.stdout.write(
            self.style.SUCCESS(f'Done. {count} content sources created.')
        )
