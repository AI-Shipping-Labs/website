"""Instructor account auto-linking (issue #1345).

Fills the operator-set ``Instructor.user`` FK (added in #1341, the target of
the ``content_comment`` in-app notification) from a verified platform account
whose email iexact-matches ``Instructor.email``. The rule is deliberately
conservative:

- It only ever fills a NULL ``Instructor.user`` — it never overwrites an
  operator override (a link set manually in Django admin, Studio, or the API).
- It matches on a verified email exclusively (``email__iexact`` +
  ``email_verified=True``). ``User.email`` is unique so there is at most one
  match. Name matching is intentionally excluded to avoid mis-delivery.
- An empty ``Instructor.email`` is the escape hatch: it is never auto-linked
  and simply surfaces in the Studio discoverability list.

Trigger points: best-effort during instructor sync, the ``link_instructors``
management command, and the ``POST /api/instructors/reconcile`` endpoint.
"""

import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

User = get_user_model()


def find_matching_user(email):
    """Return the verified ``User`` whose email iexact-matches ``email``.

    Returns ``None`` when ``email`` is empty or no verified account matches.
    ``User.email`` is unique, so at most one row is ever returned.
    """
    if not email:
        return None
    return (
        User.objects.filter(email__iexact=email, email_verified=True).first()
    )


def auto_link_instructor(instructor, *, dry_run=False):
    """Apply the auto-link rule to a single instructor.

    Returns one of:

    - ``'linked'`` — a verified match was found and the (previously NULL)
      ``user`` was filled (or would be, when ``dry_run``).
    - ``'already_linked'`` — ``user`` was already set; left untouched.
    - ``'no_email'`` — ``email`` is empty; left unlinked.
    - ``'no_match'`` — no verified account matched; left unlinked.
    """
    if instructor.user_id is not None:
        return 'already_linked'
    if not instructor.email:
        return 'no_email'
    match = find_matching_user(instructor.email)
    if match is None:
        return 'no_match'
    if not dry_run:
        instructor.user = match
        instructor.save(update_fields=['user'])
    return 'linked'


def reconcile_instructors(*, dry_run=False):
    """Run the auto-link rule across every instructor and return a summary.

    Idempotent: a second run with no new verified accounts links nothing.

    Returns a dict with ``scanned``, ``newly_linked``, ``left_unlinked``
    (``no_email`` + ``no_match``), ``already_linked``, and the per-reason
    counts ``no_email`` / ``no_match``.
    """
    from content.models import Instructor

    scanned = 0
    newly_linked = 0
    already_linked = 0
    no_email = 0
    no_match = 0

    for instructor in Instructor.objects.all():
        scanned += 1
        result = auto_link_instructor(instructor, dry_run=dry_run)
        if result == 'linked':
            newly_linked += 1
        elif result == 'already_linked':
            already_linked += 1
        elif result == 'no_email':
            no_email += 1
        else:
            no_match += 1

    return {
        'scanned': scanned,
        'newly_linked': newly_linked,
        'left_unlinked': no_email + no_match,
        'already_linked': already_linked,
        'no_email': no_email,
        'no_match': no_match,
    }


def _comment_content_ids_for_instructor(instructor):
    """Return the set of content_id UUIDs for the instructor's comment-bearing
    content surfaces (course units + workshop tutorial pages).
    """
    from content.models import Unit, WorkshopPage

    unit_ids = Unit.objects.filter(
        module__course__instructors=instructor,
    ).values_list('content_id', flat=True)
    page_ids = WorkshopPage.objects.filter(
        workshop__instructors=instructor,
    ).values_list('content_id', flat=True)
    return set(unit_ids) | set(page_ids)


def comment_content_count(instructor):
    """Count distinct comment-bearing content items attributed to instructor.

    Counts the course units and workshop tutorial pages taught by the
    instructor that have at least one Q&A comment. Used by the Studio list and
    the API to surface the highest-value unlinked rows.
    """
    from comments.models import Comment

    content_ids = _comment_content_ids_for_instructor(instructor)
    if not content_ids:
        return 0
    return (
        Comment.objects.filter(content_id__in=content_ids)
        .values('content_id')
        .distinct()
        .count()
    )
