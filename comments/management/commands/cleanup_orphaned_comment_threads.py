"""Report or delete old comment threads that have no registered owner."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comments.threads import (
    delete_thread,
    orphaned_thread_queryset,
)
from notifications.models import Notification


def _plural(count, singular):
    return singular if count == 1 else f'{singular}s'


class Command(BaseCommand):
    help = (
        'Report comment threads with no registered owner; pass --apply to '
        'delete candidates old enough to be safe.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Delete the reported orphaned threads.',
        )
        parser.add_argument(
            '--min-age-days',
            type=int,
            default=7,
            help='Minimum age of the newest comment in a thread (default: 7).',
        )

    def handle(self, *args, **options):
        del args
        min_age_days = options['min_age_days']
        if min_age_days < 1:
            raise CommandError('--min-age-days has a minimum value of 1.')

        cutoff = timezone.now() - timedelta(days=min_age_days)
        rows = list(orphaned_thread_queryset(cutoff=cutoff))
        eligible_content_ids = [row['content_id'] for row in rows]
        total_comments = sum(row['comment_count'] for row in rows)
        total_notifications = 0
        if not options['apply']:
            total_notifications = Notification.objects.filter(
                notification_type='content_comment',
                thread_content_id__in=eligible_content_ids,
            ).count()

        for row in rows:
            self.stdout.write(
                f"{row['content_id']}: {row['comment_count']} "
                f"{_plural(row['comment_count'], 'comment')}, newest "
                f"{row['newest_comment_at'].isoformat()}"
            )

        if options['apply']:
            for row in rows:
                deleted = delete_thread(None, row['content_id'])
                total_notifications += deleted['notifications']

        thread_count = len(rows)
        self.stdout.write(
            f'{thread_count} {_plural(thread_count, "orphaned thread")}, '
            f'{total_comments} {_plural(total_comments, "comment")}, '
            f'{total_notifications} '
            f'{_plural(total_notifications, "metadata-bearing notification")}'
        )
        self.stdout.write(
            'Legacy content-comment notifications without thread metadata '
            'are preserved because they cannot be safely mapped.'
        )
        if options['apply']:
            self.stdout.write(self.style.SUCCESS('Cleanup applied.'))
        else:
            self.stdout.write(
                'Dry run: nothing was deleted. Re-run with --apply to delete '
                'these orphaned threads.'
            )
