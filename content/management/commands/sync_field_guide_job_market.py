"""Convert field-guide job-market scrapes into a content-repo data article.

One-way converter between a local checkout of the AI Engineering Field
Guide and the content repo's ``blog/ai-engineering-job-market/`` article.
The guide collects monthly cross-sections of builtin.com AI-engineering
postings under ``job-market/data_structured/<date>/*.yaml``; this command
computes the aggregates (skill demand, skill trends, role mix, top
companies, top locations, provenance) and writes the exact file shape the
existing ``ArticlesParser`` sync already consumes: the article markdown
with a ``data:`` frontmatter payload plus the two ``widgets/`` templates
its ``<!-- include:widgets/... -->`` markers reference. It only reads and
writes plain files: it never touches the database and never runs git.

Refresh loop:

1. On a clean content-repo checkout, run with ``--write``::

       uv run python manage.py sync_field_guide_job_market \\
           --from-disk ~/git/ai-engineering-field-guide --write

2. Review the diff in the content repo, commit and push.
3. Let the webhook sync run, or trigger it via the existing authenticated
   sync API (the content repo's ``scripts/sync_production.py`` does this).

Without ``--write`` the command is a dry run: it prints the per-scrape
and per-section summary and writes nothing.
"""

import os
from argparse import RawDescriptionHelpFormatter

import yaml
from django.core.management.base import BaseCommand, CommandError

from content.services import field_guide_job_market as converter


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
            help='Write the generated article and widget files. Without '
                 'this flag the command is a dry run and prints the '
                 'summary only.',
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
            summary = converter.refresh(
                guide_root, content_repo, write=write,
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise CommandError(str(exc)) from exc

        for scrape in summary.per_scrape_counts():
            self.stdout.write(
                f'Scrape {scrape.date}: {scrape.postings} postings')
        provenance_note = (
            f'Total: {summary.total_postings:,} postings across '
            f'{len(summary.months)} scrapes; data through '
            f'{summary.data_through}.'
        )
        self.stdout.write(provenance_note)
        if summary.article_existed:
            id_note = f'kept content_id {summary.content_id}'
        else:
            id_note = 'new file, content_id minted on write'
        self.stdout.write(
            f'{summary.article_rel}: '
            f'{summary.skill_demand_categories} skill-demand categories, '
            f'{summary.trend_skill_count} trend skills, '
            f'{summary.role_type_count} role types, '
            f'{summary.company_count} companies, '
            f'{summary.location_count} locations ({id_note})'
        )
        self.stdout.write(
            f'{converter.SKILLS_WIDGET_TARGET}: '
            'CSS-width skill demand bars'
        )
        self.stdout.write(
            f'{converter.TRENDS_WIDGET_TARGET}: '
            f'month-over-month tables for {len(summary.months)} scrapes'
        )
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
