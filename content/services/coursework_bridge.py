"""Bridge AISL cohorts onto the shared curriculum cohort rows (issue #1696).

``community_base.coursework`` hangs projects, submissions and enrollments off
``cb_curriculum.Cohort``. AISL still owns ``content.Cohort`` and
``content.CohortEnrollment``, so every AISL cohort gets one mirror row, linked
by ``content.Cohort.curriculum_cohort``. The mirror is derived: this module is
the only writer, and the ``post_save`` signal in ``content.signals`` keeps it
in step. The later A5.1 cutover keeps the mirror rows as the canonical cohorts,
so coursework foreign keys never need re-pointing.

Nothing here changes a learner page. Phase 6 of the plan moves the project flow
onto these rows.
"""

from __future__ import annotations

from community_base.curriculum.models import Cohort as CurriculumCohort
from community_base.curriculum.models import Enrollment as CurriculumEnrollment
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from content.models.cohort import COHORT_MODE_SELF_PACED

SELF_PACED_SLUG = 'self-paced'
# cb_curriculum.Cohort.slug is a SlugField(max_length=300).
_SLUG_MAX_LENGTH = 300


def mirror_base_slug(cohort):
    """Slug rule: the external key, else ``self-paced``, else the name.

    Falls back to ``cohort-<pk>`` when the rule produces nothing usable.
    """
    if cohort.external_key:
        slug = slugify(cohort.external_key)
    elif cohort.mode == COHORT_MODE_SELF_PACED:
        slug = SELF_PACED_SLUG
    else:
        slug = slugify(cohort.name)
    slug = slug[:_SLUG_MAX_LENGTH]
    return slug or f'cohort-{cohort.pk}'


def _unique_slug(cohort, mirror_pk):
    """Keep ``(course, slug)`` unique (``cb_cohort_course_slug_unique``).

    A collision with another mirror in the same course gets the AISL cohort
    id appended, which is stable across re-runs.
    """
    base = mirror_base_slug(cohort)
    taken = CurriculumCohort.objects.filter(
        course_id=cohort.course_id, slug=base,
    ).exclude(pk=mirror_pk)
    if not taken.exists():
        return base
    suffix = f'-{cohort.pk}'
    return f'{base[:_SLUG_MAX_LENGTH - len(suffix)]}{suffix}'


def _as_date(cohort, field_name):
    field = cohort._meta.get_field(field_name)
    return field.to_python(getattr(cohort, field_name))


def mirror_values(cohort, *, today=None):
    """Fields the mirror copies from the AISL cohort.

    ``project_passing_score`` is not listed: it is set to ``0`` (pass means
    delivering your reviews) when the mirror is created and is never
    overwritten afterwards.
    """
    today = today or timezone.localdate()
    # An unsaved-then-saved instance may still hold the ISO strings a caller
    # passed in; normalize them the way the database stored them.
    start_date = _as_date(cohort, 'start_date')
    end_date = _as_date(cohort, 'end_date')
    finished = end_date is not None and end_date < today
    return {
        'course_id': cohort.course_id,
        'title': cohort.name,
        'mode': cohort.mode,
        'start_date': start_date,
        'end_date': end_date,
        'max_participants': cohort.max_participants,
        'visible': cohort.is_active,
        'finished': finished,
    }


def ensure_curriculum_cohort(cohort):
    """Create or update the mirror of ``cohort`` and return it. Idempotent.

    Writes only the fields that changed, so a second call is a read.
    """
    with transaction.atomic():
        mirror = None
        if cohort.curriculum_cohort_id is not None:
            mirror = (
                CurriculumCohort.objects.select_for_update()
                .filter(pk=cohort.curriculum_cohort_id)
                .first()
            )
        values = mirror_values(cohort)
        if mirror is None:
            mirror = CurriculumCohort.objects.create(
                slug=_unique_slug(cohort, None),
                project_passing_score=0,
                **values,
            )
            type(cohort).objects.filter(pk=cohort.pk).update(
                curriculum_cohort=mirror,
            )
            cohort.curriculum_cohort = mirror
            return mirror

        values['slug'] = _unique_slug(cohort, mirror.pk)
        changed = [
            field for field, value in values.items()
            if getattr(mirror, field) != value
        ]
        if changed:
            for field in changed:
                setattr(mirror, field, values[field])
            mirror.save(update_fields=changed)
        return mirror


def remove_unused_curriculum_cohort(curriculum_cohort_id):
    """Delete a mirror whose AISL cohort is gone, unless anything uses it.

    A mirror with enrollments, projects or homework is kept: deleting it
    would cascade learner coursework.
    """
    if curriculum_cohort_id is None:
        return False
    mirror = CurriculumCohort.objects.filter(pk=curriculum_cohort_id).first()
    if mirror is None:
        return False
    if (
        mirror.enrollments.exists()
        or mirror.projects.exists()
        or mirror.homeworks.exists()
    ):
        return False
    mirror.delete()
    return True


def ensure_curriculum_enrollment(user, cohort):
    """Return the user's active curriculum enrollment in ``cohort``'s mirror.

    Creates the mirror and the enrollment when missing. Coursework submissions
    and reviews need this row; it is created lazily before a coursework write.
    """
    mirror = ensure_curriculum_cohort(cohort)
    with transaction.atomic():
        enrollment = (
            CurriculumEnrollment.objects.select_for_update()
            .filter(user=user, cohort=mirror, unenrolled_at__isnull=True)
            .first()
        )
        if enrollment is None:
            enrollment = CurriculumEnrollment.objects.create(
                user=user, cohort=mirror,
            )
        return enrollment


def review_path(project, review=None, **kwargs):
    """``COURSEWORK_REVIEW_URL_BUILDER``: AISL route of a review (or the list).

    Returns a site-relative path. A durable mail context must not store a
    link (#1613), so the mail worker hook
    (``email_app.hooks.resolve_auth_mail_context``) prefixes the site URL.
    Phase 6 of #1696 keys ``course_project_review_form`` by ``PeerReview.id``.
    """
    route_kwargs = {
        'slug': project.cohort.course.slug,
        'attempt_slug': project.slug,
    }
    if review is None:
        return reverse('course_project_reviews', kwargs=route_kwargs)
    return reverse(
        'course_project_review_form',
        kwargs={**route_kwargs, 'submission_id': review.id},
    )
