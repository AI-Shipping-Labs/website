"""AISL adapter for the shared community-base project submission form (#1777).

The field contract and its validation live in ``community_base.coursework``
(C5.2n, pinned at v0.5.20); this module is the site-side adoption for
``content.models.peer_review.ProjectSubmission``.

- ``CourseProjectSubmissionTarget`` exposes the package project toggles for one
  AISL submission slot (a dated ``CourseProject`` attempt, or ``None`` for the
  undated legacy route): commit id on and required, learning in public on with
  the package default cap, time spent on, and the deadline lock mapped from
  ``CourseProject.submission_due_at``. AISL ``CourseProject`` has no package
  state columns, so the wrapper derives them: the review lock is the learner's
  own submission status, and an undated slot never expires.
- ``AislProjectSubmissionForm`` subclasses the shared form so saving writes
  AISL's ``content.ProjectSubmission`` (``github_link`` maps to
  ``project_url``). The package save path writes the package coursework
  models and must not be used for persistence on this site.

Capability difference documented per issue #1777 (removal): AISL does not offer
self-service submission removal. Review batches on this site assign named
reviewers to a specific submission row, so a learner-side delete would strand
or re-roll peer-review assignments -- a peer-review-rules change this issue
deliberately does not make. The host template hides the shared partial's
Remove button, and the views reject ``action=delete`` POSTs without side
effects.

Certificate name: the shared field persists to the learner's curriculum
enrollment (``content.services.coursework_bridge``), never to a project-only
shadow field. The view resolves the AISL cohort; the form writes the name.
"""

from community_base.coursework.models import ProjectState
from community_base.coursework.project_forms import ProjectSubmissionForm
from community_base.curriculum.models import Enrollment as CurriculumEnrollment
from django import forms
from django.db import transaction

from content.models.cohort import CohortEnrollment
from content.models.peer_review import ProjectSubmission

REMOVAL_UNAVAILABLE_MESSAGE = (
    'Submissions cannot be removed on AI Shipping Labs: peer reviews are '
    'assigned to a specific submission. Contact support if you need yours '
    'withdrawn.'
)

# CMP parity: the package ``coursework.Project.learning_in_public_cap_project``
# default. DataTalksClub renders the same cap on its reference form.
LEARNING_IN_PUBLIC_CAP = 14

DESCRIPTION_HELP = 'Tell reviewers about your project.'


class CourseProjectSubmissionTarget:
    """The package project-toggles contract for one AISL submission slot."""

    # CMP-parity capabilities for AISL attempts (issue #1777): the shared form
    # collects the commit id (required), learning in public links and time
    # spent. FAQ contribution stays a DataTalksClub-only field: it appears
    # only when the adapter enables it, and AISL never does.
    commit_id_field = True
    time_spent_project_field = True
    learning_in_public_cap_project = LEARNING_IN_PUBLIC_CAP
    # AISL review batches are deadline-driven (``form_review_batches``); the
    # pooled-review lock never applies.
    uses_pooled_review = False

    def __init__(self, course_project, submission=None):
        self.course_project = course_project
        self.submission = submission

    @property
    def submission_due_date(self):
        """The package deadline attribute, mapped from the AISL field."""
        if self.course_project is None:
            # The undated /courses/<slug>/submit slot has no deadline; its
            # lock is the submission status (see ``state``).
            return None
        return self.course_project.submission_due_at

    @property
    def state(self):
        """Collecting until the learner's own review lifecycle starts.

        ``CourseProject`` has no package ``state`` column; the submission
        status is the site's review lock, and past it the shared form closes.
        """
        if self.submission is not None and self.submission.status != 'submitted':
            return ProjectState.PEER_REVIEWING.value
        return ProjectState.COLLECTING_SUBMISSIONS.value


class AislProjectSubmissionForm(ProjectSubmissionForm):
    """The shared form persisting to ``content.ProjectSubmission``.

    Extra keyword arguments beyond the shared form's:

    - ``cohort``: the AISL cohort a created submission is filed under
      (the view resolves it; ``None`` for self-paced learners);
    - ``certificate_cohort``: the AISL cohort whose curriculum enrollment
      receives the certificate name (any active enrollment; may differ from
      ``cohort`` for self-paced learners).
    """

    # The legacy AISL description rides along as the documented subclass
    # extra field so reviewer context is preserved (issue #1777).
    description = forms.CharField(
        label='Description',
        required=False,
        help_text=DESCRIPTION_HELP,
        widget=forms.Textarea(
            attrs={
                'class': 'cb-input',
                'rows': 4,
                'placeholder': 'Tell reviewers about your project...',
            },
        ),
    )

    def __init__(
        self,
        data=None,
        *,
        project,
        submission=None,
        enrollment=None,
        user=None,
        course=None,
        cohort=None,
        certificate_cohort=None,
        commit_id_enabled=True,
        **kwargs,
    ):
        self.course = course
        self.cohort = cohort
        self.certificate_cohort = certificate_cohort
        super().__init__(
            data,
            project=CourseProjectSubmissionTarget(project, submission),
            submission=submission,
            enrollment=enrollment,
            user=user,
            **kwargs,
        )
        if not commit_id_enabled:
            # Legacy API clients predate commit capture: the field disappears,
            # so an update keeps the stored commit id (see ``save``) instead of
            # failing the shared required check.
            self.fields.pop('commit_id', None)

    def _initial_from(self, submission, enrollment) -> dict:
        """Prefill from the AISL submission row (``project_url`` holds the link)."""
        initial = {}
        if submission is not None:
            initial = {
                'github_link': submission.project_url,
                'commit_id': submission.commit_id,
                'learning_in_public_links': list(
                    submission.learning_in_public_links or []
                ),
                'time_spent': submission.time_spent,
                'description': submission.description,
            }
        if enrollment is not None:
            initial['certificate_name'] = (
                enrollment.certificate_name or enrollment.display_name
            )
        return initial

    def apply_extra_fields(self, submission: ProjectSubmission) -> None:
        submission.description = (self.cleaned_data.get('description') or '').strip()

    def save(self) -> tuple[ProjectSubmission, bool]:
        """Create or update the AISL submission. Call only after ``is_valid()``.

        Keeps the site's update semantics: ``submitted_at`` is not bumped
        (review batch formation keys off it), and the certificate name is
        written to the curriculum enrollment, lazily created only when a name
        was actually submitted.
        """
        data = self.cleaned_data
        existing = self.submission
        with transaction.atomic():
            if existing is not None:
                submission = existing
                created = False
            else:
                course = (
                    self.project.course_project.course
                    if self.project.course_project is not None
                    else self.course
                )
                submission = ProjectSubmission(
                    user=self.user,
                    course=course,
                    course_project=self.project.course_project,
                    cohort=self.cohort,
                )
                created = True
            submission.project_url = data['github_link']
            if 'commit_id' in self.fields:
                submission.commit_id = data.get('commit_id', '')
            if 'learning_in_public_links' in self.fields:
                submission.learning_in_public_links = (
                    data.get('learning_in_public_links') or []
                )
            if 'time_spent' in self.fields:
                submission.time_spent = data.get('time_spent')
            self.apply_extra_fields(submission)
            submission.full_clean()
            submission.save()
            self._save_certificate_name()
        self.submission = submission
        return submission, created

    def _save_certificate_name(self) -> None:
        certificate_name = (self.cleaned_data.get('certificate_name') or '').strip()
        if not certificate_name:
            return
        enrollment = self.enrollment
        if enrollment is None:
            from content.services.coursework_bridge import ensure_curriculum_enrollment

            if self.certificate_cohort is None:
                return
            enrollment = ensure_curriculum_enrollment(
                self.user, self.certificate_cohort,
            )
        if certificate_name != enrollment.certificate_name:
            enrollment.certificate_name = certificate_name
            enrollment.save(update_fields=['certificate_name'])


def submission_cohort(user, course, preferred_cohort=None):
    """The AISL cohort a created submission is filed under (site rule).

    The attempt's cohort wins; a self-paced learner files no cohort on the
    submission, matching the pre-#1777 behavior.
    """
    if preferred_cohort is not None:
        return preferred_cohort
    enrollment = CohortEnrollment.objects.filter(
        user=user,
        cohort__course=course,
        cohort__is_active=True,
        cohort__mode='cohort',
    ).select_related('cohort').first()
    return enrollment.cohort if enrollment else None


def certificate_cohort(user, course, preferred_cohort=None):
    """The AISL cohort whose curriculum enrollment stores the certificate name.

    Prefers the attempt's cohort; falls back to any active enrollment in the
    course, so self-paced learners keep a certificate-name record too.
    """
    if preferred_cohort is not None:
        return preferred_cohort
    enrollment = CohortEnrollment.objects.filter(
        user=user,
        cohort__course=course,
        cohort__is_active=True,
    ).select_related('cohort').first()
    return enrollment.cohort if enrollment else None


def lookup_curriculum_enrollment(user, cohort):
    """Read-only curriculum enrollment lookup (never creates the mirror).

    View GETs use this to prefill the certificate name; the write path
    (``ensure_curriculum_enrollment``) runs only when a form actually saves.
    """
    if cohort is None or cohort.curriculum_cohort_id is None:
        return None
    return CurriculumEnrollment.objects.filter(
        user=user,
        cohort_id=cohort.curriculum_cohort_id,
        unenrolled_at__isnull=True,
    ).first()


def build_submission_form(
    project=None,
    *,
    data=None,
    submission=None,
    enrollment=None,
    user=None,
    course=None,
    cohort=None,
    certificate_cohort=None,
    commit_id_enabled=True,
):
    """The AISL adapter form for one submission slot (dated or undated).

    The certificate name field renders only when a curriculum enrollment can
    store it, i.e. the learner has a bridgeable AISL cohort (issue #1777:
    "certificate name appears where a certificate can be issued").
    """
    kwargs = {}
    if certificate_cohort is None:
        kwargs['certificate_name_field'] = False
    return AislProjectSubmissionForm(
        data,
        project=project,
        submission=submission,
        enrollment=enrollment,
        user=user,
        course=course,
        cohort=cohort,
        certificate_cohort=certificate_cohort,
        commit_id_enabled=commit_id_enabled,
        **kwargs,
    )
