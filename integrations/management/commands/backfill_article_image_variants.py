"""Backfill responsive variants for repository-controlled Article images."""

import os
from contextlib import ExitStack

from community_base.content_sync.checkout import ImmutableCheckout
from community_base.content_sync.github import checkout_repository
from django.core.management.base import BaseCommand, CommandError

from content.models import Article
from content.sync_parsers.checkout_view import (
    CheckoutView,
    activate_view,
    checkout_is_file,
)
from content.sync_parsers.parsing import _parse_markdown_file
from integrations.models import ContentSource
from integrations.services.article_images import build_article_image_manifest


class Command(BaseCommand):
    help = "Generate/reuse deterministic Article image variants without deleting media"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--source",
            action="append",
            default=[],
            help="Limit to a configured repo name or short name (repeatable).",
        )
        parser.add_argument(
            "--article",
            action="append",
            default=[],
            help="Limit to an Article slug (repeatable).",
        )
        parser.add_argument(
            "--repo-dir",
            default="",
            help="Use this controlled local checkout (single source only).",
        )

    def handle(self, *args, **options):
        sources = ContentSource.objects.all().order_by("repo_name")
        selectors = set(options["source"])
        if selectors:
            sources = [source for source in sources if source.repo_name in selectors or source.short_name in selectors]
        else:
            sources = list(sources)
        if not sources:
            raise CommandError("No configured content source matched --source.")
        if options["repo_dir"] and len(sources) != 1:
            raise CommandError("--repo-dir requires exactly one selected source.")

        totals = {
            "scanned": 0,
            "generated": 0,
            "reused": 0,
            "skipped": 0,
            "failed": 0,
        }
        for source in sources:
            self._process_source(source, options, totals)

        mode = "DRY RUN" if options["dry_run"] else "COMPLETE"
        self.stdout.write(f"{mode}: " + " ".join(f"{key}={value}" for key, value in totals.items()))
        if totals["failed"]:
            self.stderr.write(self.style.WARNING(f"{totals['failed']} image(s) failed; other articles continued."))

    def _process_source(self, source, options, totals):
        # The repository arrives through the package checkout boundary:
        # an ImmutableCheckout snapshot of --repo-dir, or a package
        # GitHubClient download for the configured source. All reads go
        # through the checkout view helpers.
        with ExitStack() as stack:
            if options["repo_dir"]:
                checkout = stack.enter_context(
                    ImmutableCheckout(os.path.abspath(options["repo_dir"]))
                )
            else:
                checkout = stack.enter_context(checkout_repository(source))
            view = CheckoutView(checkout)
            repo_dir = view.root
            with activate_view(view):
                articles = Article.objects.filter(source_repo=source.repo_name)
                if options["article"]:
                    articles = articles.filter(slug__in=options["article"])
                for article in articles.order_by("slug"):
                    totals["scanned"] += 1
                    source_file = os.path.join(repo_dir, article.source_path)
                    if not checkout_is_file(source_file):
                        totals["skipped"] += 1
                        self.stdout.write(
                            f"SKIP {article.slug}: controlled source file missing"
                        )
                        continue
                    try:
                        metadata, body = _parse_markdown_file(source_file)
                        cover = metadata.get("cover_image", "") or metadata.get(
                            "cover_image_url", ""
                        )
                        manifest, stats = build_article_image_manifest(
                            source=source,
                            repo_dir=repo_dir,
                            rel_path=article.source_path,
                            body=body,
                            cover_image=cover,
                            dry_run=options["dry_run"],
                        )
                        totals["generated"] += stats.generated
                        totals["reused"] += stats.reused
                        totals["skipped"] += stats.skipped
                        totals["failed"] += stats.failed
                        for error in stats.errors:
                            self.stderr.write(
                                f"WARN {article.slug} {error.get('image', '')}: "
                                f"{error.get('error', 'unknown error')}"
                            )
                        if not options["dry_run"] and (
                            manifest != article.image_manifest
                            or stats.complete != article.image_manifest_complete
                        ):
                            Article.objects.filter(pk=article.pk).update(
                                image_manifest=manifest,
                                image_manifest_complete=stats.complete,
                            )
                    except (OSError, ValueError) as exc:
                        totals["failed"] += 1
                        self.stderr.write(f"WARN {article.slug}: {exc}")
