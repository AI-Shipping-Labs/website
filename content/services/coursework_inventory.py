"""Count report of a course's legacy project and peer-review rows (#1696).

``build_coursework_inventory(course)`` reads only the current legacy tables
(``content.Cohort``, ``CohortEnrollment``, ``CourseProject``,
``ProjectSubmission``, ``PeerReview`` and ``CourseCertificate``) and never
writes. It is the baseline for moving projects onto
``community_base.coursework``: the staff API
``GET /api/courses/<slug>/coursework-inventory`` returns it, and the later
migration's dry run reuses it for its before and after counts.

Definitions that the later migration relies on:

- A cohort "has projects" when a ``CourseProject`` or a ``ProjectSubmission``
  points at it, or when it is self-paced and the course has an unscoped
  (null-cohort) ``CourseProject``. Unscoped attempts map to the self-paced
  cohort.
- An unscoped attempt with zero submissions is a preview attempt. The
  migration skips it, so it is listed here.
- A reviewer "without a submission" has no ``ProjectSubmission`` for the same
  attempt as the submission they review. For legacy rows with
  ``course_project=NULL`` the attempt is the course-level legacy submission.
  The migration creates one volunteer submission per such
  ``(reviewer, attempt)`` pair.
- A legacy pooled group is the set of self-paced legacy submissions
  (``course_project=NULL`` and no cohort or a self-paced cohort) that share
  one ``batch_assigned_at``: one ``form_review_batches`` pool run.
"""

from collections import Counter, defaultdict

from content.models import (
    Cohort,
    CohortEnrollment,
    CourseCertificate,
    CourseProject,
    PeerReview,
    ProjectSubmission,
)
from content.models.cohort import (
    COHORT_MODE_CHOICES,
    COHORT_MODE_SELF_PACED,
)
from content.models.peer_review import SUBMISSION_STATUS_CHOICES


def _iso(value):
    return value.isoformat() if value else None


def _cohort_ref(cohort):
    return {
        'cohort_id': cohort.pk,
        'external_key': cohort.external_key,
        'name': cohort.name,
        'mode': cohort.mode,
    }


def build_coursework_inventory(course):
    """Return the dry-run count report for ``course`` as a JSON-ready dict."""
    cohorts = list(Cohort.objects.filter(course=course).order_by('start_date', 'pk'))
    projects = list(
        CourseProject.objects.filter(course=course).order_by('submission_due_at', 'pk')
    )
    submissions = list(
        ProjectSubmission.objects.filter(course=course).values(
            'id', 'user_id', 'course_project_id', 'cohort_id', 'status',
            'description', 'batch_assigned_at', 'review_deadline',
        )
    )
    reviews = list(
        PeerReview.objects.filter(submission__course=course).values(
            'reviewer_id', 'submission__course_project_id', 'score', 'is_complete',
        )
    )
    enrollment_counts = Counter(
        CohortEnrollment.objects.filter(cohort__course=course)
        .values_list('cohort_id', flat=True)
    )

    project_counts = Counter(p.cohort_id for p in projects if p.cohort_id)
    submission_counts_by_cohort = Counter(
        s['cohort_id'] for s in submissions if s['cohort_id']
    )
    submission_counts_by_project = Counter(
        s['course_project_id'] for s in submissions if s['course_project_id']
    )
    has_unscoped_projects = any(p.cohort_id is None for p in projects)
    cohort_modes = {c.pk: c.mode for c in cohorts}

    def has_projects(cohort):
        return bool(
            project_counts[cohort.pk]
            or submission_counts_by_cohort[cohort.pk]
            or (has_unscoped_projects and cohort.mode == COHORT_MODE_SELF_PACED)
        )

    by_mode = {mode: 0 for mode, _label in COHORT_MODE_CHOICES}
    for cohort in cohorts:
        by_mode[cohort.mode] = by_mode.get(cohort.mode, 0) + 1

    cohorts_with_projects = [c for c in cohorts if has_projects(c)]

    unscoped_without_submissions = [
        p.slug for p in projects
        if p.cohort_id is None and not submission_counts_by_project[p.pk]
    ]

    status_counts = {status: 0 for status, _label in SUBMISSION_STATUS_CHOICES}
    for submission in submissions:
        status_counts[submission['status']] = status_counts.get(submission['status'], 0) + 1
    blank_descriptions = sum(1 for s in submissions if not (s['description'] or '').strip())

    # ``None`` is the course-level legacy attempt (``course_project=NULL``).
    submitters = {(s['user_id'], s['course_project_id']) for s in submissions}
    orphan_reviews = [
        r for r in reviews
        if (r['reviewer_id'], r['submission__course_project_id']) not in submitters
    ]
    orphan_pairs = {
        (r['reviewer_id'], r['submission__course_project_id']) for r in orphan_reviews
    }

    pooled = defaultdict(list)
    for submission in submissions:
        if submission['course_project_id'] or not submission['batch_assigned_at']:
            continue
        cohort_mode = cohort_modes.get(submission['cohort_id'])
        if submission['cohort_id'] and cohort_mode != COHORT_MODE_SELF_PACED:
            continue
        pooled[submission['batch_assigned_at']].append(submission)
    pooled_groups = [
        {
            'batch_assigned_at': _iso(assigned_at),
            'review_deadline': _iso(max(
                (s['review_deadline'] for s in members if s['review_deadline']),
                default=None,
            )),
            'size': len(members),
        }
        for assigned_at, members in sorted(pooled.items())
    ]

    certificates = CourseCertificate.objects.filter(course=course)

    return {
        'course': course.slug,
        'cohorts': {
            'total': len(cohorts),
            'by_mode': by_mode,
            'items': [
                {
                    **_cohort_ref(cohort),
                    'is_active': cohort.is_active,
                    'start_date': _iso(cohort.start_date),
                    'end_date': _iso(cohort.end_date),
                    'enrollment_count': enrollment_counts[cohort.pk],
                    'project_count': project_counts[cohort.pk],
                    'submission_count': submission_counts_by_cohort[cohort.pk],
                    'has_projects': has_projects(cohort),
                }
                for cohort in cohorts
            ],
        },
        'enrollments': {
            'cohorts_with_projects': len(cohorts_with_projects),
            'cohort_enrollments': sum(
                enrollment_counts[c.pk] for c in cohorts_with_projects
            ),
            'by_cohort': [
                {**_cohort_ref(c), 'count': enrollment_counts[c.pk]}
                for c in cohorts_with_projects
            ],
        },
        'projects': {
            'total': len(projects),
            'null_cohort': sum(1 for p in projects if p.cohort_id is None),
            'by_cohort': [
                {**_cohort_ref(c), 'count': project_counts[c.pk]}
                for c in cohorts if project_counts[c.pk]
            ],
            'unscoped_without_submissions': {
                'count': len(unscoped_without_submissions),
                'slugs': unscoped_without_submissions,
            },
            'items': [
                {
                    'id': p.pk,
                    'slug': p.slug,
                    'title': p.title,
                    'cohort_id': p.cohort_id,
                    'submission_due_at': _iso(p.submission_due_at),
                    'review_due_at': _iso(p.review_due_at),
                    'peer_review_count': p.peer_review_count,
                    'submission_count': submission_counts_by_project[p.pk],
                }
                for p in projects
            ],
        },
        'submissions': {
            'total': len(submissions),
            'by_status': status_counts,
            'without_course_project': sum(
                1 for s in submissions if s['course_project_id'] is None
            ),
            'description': {
                'blank': blank_descriptions,
                'non_blank': len(submissions) - blank_descriptions,
            },
        },
        'reviews': {
            'total': len(reviews),
            'complete': sum(1 for r in reviews if r['is_complete']),
            'incomplete': sum(1 for r in reviews if not r['is_complete']),
            'scored': sum(1 for r in reviews if r['score'] is not None),
            'unscored': sum(1 for r in reviews if r['score'] is None),
            'reviewers_without_submission': {
                'pairs': len(orphan_pairs),
                'reviews': len(orphan_reviews),
            },
        },
        'batches': {
            'legacy_pooled_groups': len(pooled_groups),
            'submissions_in_groups': sum(g['size'] for g in pooled_groups),
            'groups': pooled_groups,
        },
        'certificates': {
            'total': certificates.count(),
            'with_submission': certificates.filter(submission__isnull=False).count(),
            'revoked': certificates.filter(revoked_at__isnull=False).count(),
        },
    }
