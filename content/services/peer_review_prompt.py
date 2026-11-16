"""Course Home's "Complete your peer reviews" prompt (one card, every tab)."""

from dataclasses import dataclass

from content.services.attempt_reviews import next_pending_url
from content.services.course_commitments import short_when_label
from content.services.course_schedule import cohort_projects
from content.services.learner_attempt_phases import (
    assigned_by_project,
    learner_attempt_phases,
    learner_submissions,
)
from content.services.project_phase import REVIEW, WAITING

PENDING, WAITING_STATE = 'pending', 'waiting'


@dataclass(frozen=True)
class PeerReviewPrompt:
    state: str
    project: object
    done: int
    total: int
    deadline: object
    deadline_label: str
    action_url: str

    @property
    def project_title(self):
        return self.project.title


def _candidates(course, cohort, user, now):
    projects = list(cohort_projects(course, cohort).order_by('review_due_at', 'pk'))
    if not projects:
        return [], {}, {}
    submissions = learner_submissions(user, projects)
    assigned = assigned_by_project(user, projects)
    phases = learner_attempt_phases(
        user, course, projects, now, submissions=submissions, assigned=assigned,
    )
    return projects, phases, assigned


def _prompt(state, course, project, phase, assigned, timezone_name):
    deadline = project.review_due_at
    if project.is_self_paced:
        deadline = None
    deadline_label = ''
    if deadline is not None:
        deadline_label = short_when_label(deadline, timezone_name)
    return PeerReviewPrompt(
        state=state, project=project,
        done=phase.reviews_done, total=phase.reviews_assigned,
        deadline=deadline, deadline_label=deadline_label,
        action_url=next_pending_url(course, project, assigned.get(project.pk, [])),
    )


def _submissions_closed(project, now):
    """Self-paced attempts wait for a review group without a deadline."""
    return project.is_self_paced or not project.is_submission_open(now)


def _first(projects, phases, wanted):
    for project in projects:
        if wanted(project, phases[project.pk]):
            return project
    return None


def peer_review_prompt(user, course, cohort, now, timezone_name):
    """The prompt for the most urgent attempt, or ``None``."""
    if not getattr(user, 'is_authenticated', False) or not course.peer_review_enabled:
        return None
    projects, phases, assigned = _candidates(course, cohort, user, now)
    checks = (
        (PENDING, lambda p, ph: ph.key == REVIEW and ph.reviews_pending),
        (WAITING_STATE, lambda p, ph: ph.key == WAITING and _submissions_closed(p, now)),
    )
    for state, wanted in checks:
        project = _first(projects, phases, wanted)
        if project is not None:
            return _prompt(
                state, course, project, phases[project.pk], assigned, timezone_name,
            )
    return None
