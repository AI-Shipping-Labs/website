"""Complete a project (CMP's COMPLETED): results and feedback become visible."""

from django.core.management.base import BaseCommand

from content.management.commands.set_project_state import move_project


class Command(BaseCommand):
    help = 'Close peer review for a project attempt (move it to Completed).'

    def add_arguments(self, parser):
        parser.add_argument('course_slug')
        parser.add_argument('project_slug')

    def handle(self, *args, **options):
        project, previous = move_project(
            options['course_slug'], options['project_slug'], 'completed',
        )
        self.stdout.write(self.style.SUCCESS(
            f'{project.slug}: peer review closed ({previous} -> {project.state})'
        ))
