"""
Management command to seed the three default GitHub content sources.

Issue #310: with one ``ContentSource`` per repo, three rows cover the
canonical AI-Shipping-Labs repos. Idempotent — re-running does not create
duplicates.
"""

from django.core.management.base import BaseCommand

from integrations.models import ContentSource

DEFAULT_SOURCES = [
    {'repo_name': 'AI-Shipping-Labs/content', 'is_private': True},
    {'repo_name': 'AI-Shipping-Labs/python-course', 'is_private': True},
    {'repo_name': 'AI-Shipping-Labs/workshops-content', 'is_private': True},
]


class Command(BaseCommand):
    help = 'Seed the database with default GitHub content sources'

    def handle(self, *args, **options):
        count = 0
        for source_data in DEFAULT_SOURCES:
            _, created = ContentSource.objects.get_or_create(
                repo_name=source_data['repo_name'],
                defaults={'is_private': source_data['is_private']},
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
