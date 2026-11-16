"""Sidebar sub-items of the current project attempt, like homework steps."""

from dataclasses import dataclass

from django.urls import reverse

from content.services.peer_pseudonym import peer_pseudonym
from content.services.project_phase import FEEDBACK

SUBMIT_PAGE, REVIEWS_PAGE, EVALUATION_PAGE = 'submit', 'reviews', 'evaluation'


@dataclass(frozen=True)
class AttemptNavPage:
    """Which attempt page is open: ``kind`` plus the evaluated submission."""

    kind: str
    submission_id: int | None = None


@dataclass(frozen=True)
class AttemptNavItem:
    key: str
    title: str
    url: str
    done: bool
    current: bool


def _attempt_url(name, course, project, **extra):
    return reverse(name, kwargs={'slug': course.slug, 'attempt_slug': project.slug, **extra})


def _review_items(course, project, assigned, page):
    items = []
    for number, review in enumerate(assigned, start=1):
        current = page.kind == EVALUATION_PAGE and page.submission_id == review.submission_id
        items.append(AttemptNavItem(
            key='review',
            title=f'Review {number}: {peer_pseudonym(review.submission.user_id, course.pk)}',
            url=_attempt_url(
                'course_project_review_form', course, project,
                submission_id=review.submission_id,
            ),
            done=review.is_complete,
            current=current,
        ))
    return items


def attempt_nav_items(course, project, phase, *, has_submission, assigned, page):
    """Submit, the assigned reviews, then Feedback in the feedback phase."""
    items = [AttemptNavItem(
        key='submit', title='Submit',
        url=_attempt_url('course_project_submit', course, project),
        done=has_submission, current=page.kind == SUBMIT_PAGE,
    )]
    items.extend(_review_items(course, project, assigned, page))
    if phase.key == FEEDBACK:
        items.append(AttemptNavItem(
            key='feedback', title='Feedback',
            url=_attempt_url('course_project_reviews', course, project),
            done=phase.results_final, current=page.kind == REVIEWS_PAGE,
        ))
    return tuple(items)
