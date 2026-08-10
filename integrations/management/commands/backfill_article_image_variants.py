"""Backfill responsive variants for repository-controlled Article images."""

import os
import shutil
import tempfile

from django.core.management.base import BaseCommand, CommandError

from content.models import Article
from integrations.models import ContentSource
from integrations.services.article_images import build_article_image_manifest
from integrations.services.github_sync.parsing import _parse_markdown_file
from integrations.services.github_sync.repo import clone_or_pull_repo


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
        temp_dir = None
        repo_dir = options["repo_dir"]
        try:
            if repo_dir:
                repo_dir = os.path.realpath(repo_dir)
                if not os.path.isdir(repo_dir):
                    raise CommandError(f"--repo-dir does not exist: {repo_dir}")
            else:
                temp_dir = tempfile.mkdtemp(prefix="article-image-backfill-")
                clone_or_pull_repo(source.repo_name, temp_dir, source.is_private)
                repo_dir = temp_dir

            articles = Article.objects.filter(source_repo=source.repo_name)
            if options["article"]:
                articles = articles.filter(slug__in=options["article"])
            for article in articles.order_by("slug"):
                totals["scanned"] += 1
                source_file = os.path.realpath(os.path.join(repo_dir, article.source_path))
                if os.path.commonpath((repo_dir, source_file)) != repo_dir or not os.path.isfile(source_file):
                    totals["skipped"] += 1
                    self.stdout.write(f"SKIP {article.slug}: controlled source file missing")
                    continue
                try:
                    metadata, body = _parse_markdown_file(source_file)
                    cover = metadata.get("cover_image", "") or metadata.get("cover_image_url", "")
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
                            f"WARN {article.slug} {error.get('image', '')}: {error.get('error', 'unknown error')}"
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
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
