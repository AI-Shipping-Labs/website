"""Convert field-guide interview question banks into content-repo markdown.

One-way converter between a local checkout of the AI Engineering Field
Guide and the content repo's ``interview-questions/*.md`` files. The guide
keeps evolving (new questions, new footnote sources, monthly additions)
while the site's hand-converted copies drift stale; this command refreshes
them into the exact shape the existing ``InterviewQuestionsParser`` sync
already consumes. It only reads and writes plain files: it never touches
the database and never runs git.

The conversion logic lives in ``content/services/field_guide_interview.py``.

Refresh loop:

1. On a clean content-repo checkout, run with ``--write``::

       uv run python manage.py sync_field_guide_interview \\
           --from-disk ~/git/ai-engineering-field-guide --write

2. Review the diff in the content repo, commit and push.
3. Let the webhook sync run, or trigger it via the existing authenticated
   sync API (the content repo's ``scripts/sync_production.py`` does this).

Without ``--write`` the command is a dry run: it prints the per-file
summary and writes nothing.
"""

import os
from argparse import RawDescriptionHelpFormatter

import yaml
from django.core.management.base import BaseCommand, CommandError

from content.services import field_guide_interview as converter


class Command(BaseCommand):
    help = __doc__

    def create_parser(self, *args, **kwargs):
        parser = super().create_parser(*args, **kwargs)
        parser.formatter_class = RawDescriptionHelpFormatter
        return parser

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-disk',
            required=True,
            help='Path to a local checkout of the field guide repo '
                 '(ai-engineering-field-guide).',
        )
        parser.add_argument(
            '--content-repo',
            default=converter.DEFAULT_CONTENT_REPO,
            help='Path to the content repo checkout to write into '
                 f'(default: {converter.DEFAULT_CONTENT_REPO}).',
        )
        parser.add_argument(
            '--write',
            action='store_true',
            help='Write the converted files. Without this flag the '
                 'command is a dry run and prints the summary only.',
        )

    def handle(self, *args, **options):
        guide_root = os.path.abspath(os.path.expanduser(options['from_disk']))
        content_repo = os.path.abspath(
            os.path.expanduser(options['content_repo'])
        )
        write = options['write']

        try:
            converter.ensure_guide_checkout(guide_root)
        except NotADirectoryError as exc:
            raise CommandError(str(exc)) from exc
        if not os.path.isdir(content_repo):
            raise CommandError(
                f'Content repo checkout not found: {content_repo}. '
                'Pass --content-repo pointing at a content repo checkout.'
            )

        try:
            summaries = converter.refresh(
                guide_root, content_repo, write=write,
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise CommandError(str(exc)) from exc

        self._print_skipped(guide_root)
        for summary in summaries:
            self.stdout.write(self._summary_line(summary))
        if write:
            self.stdout.write(self.style.SUCCESS(
                'Wrote the files above. Review the diff, commit and push '
                'the content repo, then run the content sync.'
            ))
        else:
            self.stdout.write(self.style.MIGRATE_HEADING(
                'Dry run: no files written. Re-run with --write to update '
                'the content repo.'
            ))

    def _summary_line(self, summary):
        target_rel = os.path.join(
            converter.TARGET_SUBDIR, f'{summary.slug}.md',
        )
        source_name = os.path.basename(summary.source_rel)
        if not summary.existed:
            status_note = (
                f'defaulted to {converter.NEW_FILE_STATUS} (new file)'
            )
        elif summary.status:
            status_note = f'kept "{summary.status}"'
        else:
            status_note = 'kept (none)'
        return (
            f'{source_name} -> {target_rel}: '
            f'{summary.section_count} sections, '
            f'{summary.question_count} questions ({status_note})'
        )

    def _print_skipped(self, guide_root):
        skipped = []
        for name in converter.unmapped_files(guide_root):
            reason = (
                'aggregate file, not a category bank'
                if name == converter.AGGREGATE_GUIDE_FILE
                else 'not a known guide question file'
            )
            skipped.append(f'{name} ({reason})')
        if skipped:
            self.stdout.write('Skipped: ' + '; '.join(skipped))
