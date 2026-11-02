"""Backfill records that are missing their generated banner.

The banner pipeline is fire-and-forget by design: when the generator is
misconfigured or the Lambda fails, records keep an empty
``auto_banner_url``, the public pages fall back to the static logo, and
nothing raises. This command makes the gap visible and healable — it
lists every supported record without an effective banner and, with
``--execute``, re-enqueues a render through the same dispatcher the
sync pipeline uses.

Run it after deploy or config cutovers to detect a silently broken
pipeline, and after restoring ``BANNER_GENERATOR_*`` configuration to
regenerate the banners missed while it was down.
"""

from django.core.management.base import BaseCommand, CommandError

from integrations.services.banner_generator import dispatch
from integrations.services.banner_generator.content_models import (
    SUPPORTED_CONTENT_TYPES,
    model_for,
)
from integrations.services.banner_generator.resolve import effective_banner_url


def _records_for(model, ids):
    queryset = model.objects.all().order_by('pk')
    if ids:
        queryset = queryset.filter(pk__in=ids)
    return queryset


class Command(BaseCommand):
    help = (
        'List records without an effective banner; --execute re-enqueues '
        'a banner render for each of them.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute', action='store_true',
            help='Enqueue banner renders (default: dry run).',
        )
        parser.add_argument(
            '--content-type', action='append', default=[],
            help='Limit to a content type slug (repeatable). Default: all.',
        )
        parser.add_argument(
            '--id', action='append', type=int, default=[],
            help='Limit to specific record pks (repeatable).',
        )

    def handle(self, *args, **options):
        wanted = options['content_type'] or list(SUPPORTED_CONTENT_TYPES)
        unknown = [slug for slug in wanted if slug not in SUPPORTED_CONTENT_TYPES]
        if unknown:
            raise CommandError('Unknown content type(s): %s' % ', '.join(unknown))

        totals = {'missing': 0, 'enqueued': 0, 'skipped': 0}
        for slug in wanted:
            model = model_for(slug)
            self._scan_type(slug, model, options['id'], options, totals)

        mode = 'EXECUTE' if options['execute'] else 'DRY RUN'
        summary = '%s: missing=%d enqueued=%d skipped=%d' % (
            mode, totals['missing'], totals['enqueued'], totals['skipped'],
        )
        self.stdout.write(summary)
        if totals['skipped']:
            self.stderr.write(self.style.WARNING(
                '%d record(s) could not be enqueued; check worker task '
                'failures and the banner generator settings.' % totals['skipped'],
            ))

    def _scan_type(self, slug, model, ids, options, totals):
        for record in _records_for(model, ids):
            if effective_banner_url(record):
                continue
            totals['missing'] += 1
            label = dispatch.title_value(slug, record)
            if not options['execute']:
                self.stdout.write('MISSING %s %s %s' % (slug, record.pk, label))
                continue
            self._enqueue(slug, record, label, totals)

    def _enqueue(self, slug, record, label, totals):
        if not dispatch.is_enabled():
            raise CommandError(
                'Banner generator is not configured '
                '(BANNER_GENERATOR_FUNCTION_URL / BANNER_GENERATOR_AUTH_TOKEN); '
                'renders would be silently skipped.',
            )
        task_id = dispatch.enqueue_if_missing(slug, record.pk)
        if task_id:
            totals['enqueued'] += 1
            self.stdout.write(
                'ENQUEUED %s %s %s (task %s)' % (slug, record.pk, label, task_id),
            )
            return
        totals['skipped'] += 1
        self.stderr.write(self.style.WARNING(
            'SKIP %s %s %s: dispatcher returned no task id' % (slug, record.pk, label),
        ))
