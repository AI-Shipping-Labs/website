"""Find likely Playwright/QA fixture rows in local development databases."""

from dataclasses import dataclass

from django.db.models import Q


@dataclass(frozen=True)
class CleanupCandidate:
    model_label: str
    pk: int
    title: str
    slug: str


FIXTURE_TITLE_TERMS = (
    'qa ',
    ' qa',
    'test',
    'demo',
    'walkthrough',
    'walk-through',
    'gated course',
)

FIXTURE_SLUG_TERMS = (
    'qa-',
    '-qa',
    'test',
    'demo',
    'walkthrough',
    'walk-through',
    'gated-course',
)


def fixture_signature_q(model):
    """Build title/slug signature filters for obvious test fixture rows."""
    query = Q()
    field_names = {field.name for field in model._meta.fields}
    if 'title' in field_names:
        for term in FIXTURE_TITLE_TERMS:
            query |= Q(title__icontains=term)
    if 'slug' in field_names:
        for term in FIXTURE_SLUG_TERMS:
            query |= Q(slug__icontains=term)
    return query


def unsynced_q(model):
    """Build a protection filter that excludes synced/source-owned rows."""
    query = Q()
    field_names = {field.name for field in model._meta.fields}
    if 'source_repo' in field_names:
        query &= Q(source_repo__isnull=True) | Q(source_repo='')
    if 'content_id' in field_names:
        query &= Q(content_id__isnull=True)
    return query


def cleanup_models():
    """Return content models considered by the QA fixture cleanup command."""
    from content.models import (
        Article,
        Course,
        Download,
        Project,
        Tutorial,
        Workshop,
        WorkshopPage,
    )

    return (Article, Course, Download, Project, Tutorial, Workshop, WorkshopPage)


def find_cleanup_candidates():
    """Return likely local QA/test fixture rows, never synced content."""
    candidates = []
    for model in cleanup_models():
        signature = fixture_signature_q(model)
        if not signature:
            continue
        queryset = model.objects.filter(unsynced_q(model)).filter(signature).order_by('pk')
        for obj in queryset:
            candidates.append(
                CleanupCandidate(
                    model_label=model._meta.label,
                    pk=obj.pk,
                    title=getattr(obj, 'title', ''),
                    slug=getattr(obj, 'slug', ''),
                )
            )
    return candidates


def delete_cleanup_candidates(candidates):
    """Delete candidates by primary key and return the number deleted."""
    deleted_count = 0
    models_by_label = {model._meta.label: model for model in cleanup_models()}
    for candidate in candidates:
        model = models_by_label[candidate.model_label]
        deleted, _details = model.objects.filter(pk=candidate.pk).delete()
        deleted_count += deleted
    return deleted_count
