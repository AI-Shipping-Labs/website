"""
Management command to seed the four default GitHub content sources.

Idempotent: running twice does not create duplicates.
"""

from django.core.management.base import BaseCommand

from integrations.models import ContentSource


DEFAULT_SOURCES = [
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'article',
        'content_path': 'blog',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'course',
        'content_path': 'courses',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'resource',
        'content_path': 'resources',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'project',
        'content_path': 'projects',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'interview_question',
        'content_path': 'interview-questions',
        'is_private': True,
    },
    {
        'repo_name': 'AI-Shipping-Labs/content',
        'content_type': 'event',
        'content_path': 'events',
        'is_private': True,
    },
]


class Command(BaseCommand):
    help = 'Seed the database with default GitHub content sources'

    def handle(self, *args, **options):
        count = 0
        for source_data in DEFAULT_SOURCES:
            _, created = ContentSource.objects.get_or_create(
                repo_name=source_data['repo_name'],
                content_type=source_data['content_type'],
                defaults={
                    'content_path': source_data.get('content_path', ''),
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
