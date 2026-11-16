"""Move a project attempt between CMP-style states by hand."""

from django.core.management.base import BaseCommand, CommandError

from content.models import Course, CourseProject
from content.services.project_state import (
    ProjectStateError,
    parse_state,
    set_project_state,
)


def move_project(course_slug, project_slug, state):
    try:
        course = Course.objects.get(slug=course_slug)
    except Course.DoesNotExist:
        raise CommandError(f'Course "{course_slug}" not found.')
    try:
        project = CourseProject.objects.get(course=course, slug=project_slug)
    except CourseProject.DoesNotExist:
        raise CommandError(f'Project "{project_slug}" not found in course "{course_slug}".')
    try:
        previous = set_project_state(project, parse_state(state))
    except ProjectStateError as error:
        raise CommandError(str(error))
    return project, previous


class Command(BaseCommand):
    help = 'Set a project attempt state (collecting_submissions, peer_reviewing, completed, closed).'

    def add_arguments(self, parser):
        parser.add_argument('course_slug')
        parser.add_argument('project_slug')
        parser.add_argument('state')

    def handle(self, *args, **options):
        project, previous = move_project(
            options['course_slug'], options['project_slug'], options['state'],
        )
        self.stdout.write(self.style.SUCCESS(
            f'{project.slug}: {previous} -> {project.state} ({project.get_state_display()})'
        ))
