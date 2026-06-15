"""Studio views for revoking / un-revoking course certificates (issue #949).

A :class:`CourseCertificate` is a granted credential with a public page at
``/certificates/<uuid>``. Hard-deleting it would 404 any shared link and
erase the audit trail, so the only destructive control here is a soft
revoke: revoking stamps ``revoked_at``/``revoked_by`` (and an optional
reason) and the public page then renders a "revoked" state. Revocation is
reversible via un-revoke, which clears the three fields.

All views are staff-only (anonymous -> login, non-staff -> 403) and
POST-only. The control is reached from the course peer-reviews page.
"""

import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.models.peer_review import CourseCertificate
from studio.decorators import staff_required

logger = logging.getLogger(__name__)


def _redirect_back(request, certificate):
    """Redirect back to the originating peer-reviews page.

    The revoke control lives on the course peer-reviews page; redirect
    there so the operator stays in context. Falls back to that page even
    when no explicit ``next`` is posted because the certificate already
    carries its course.
    """
    return redirect(
        'studio_peer_review_management',
        course_id=certificate.course_id,
    )


@staff_required
@require_POST
def certificate_revoke(request, certificate_id):
    """Soft-revoke a course certificate.

    Stamps ``revoked_at``/``revoked_by`` and an optional ``revoked_reason``
    from POST. Idempotent: revoking an already-revoked certificate refreshes
    the actor/timestamp without error.
    """
    certificate = get_object_or_404(CourseCertificate, pk=certificate_id)
    certificate.revoked_at = timezone.now()
    certificate.revoked_by = request.user
    certificate.revoked_reason = (request.POST.get('revoked_reason') or '').strip()[:200]
    certificate.save(
        update_fields=['revoked_at', 'revoked_by', 'revoked_reason'],
    )
    logger.info(
        'studio.certificate_revoke actor=%s certificate_id=%s user_id=%s',
        request.user.pk, certificate.pk, certificate.user_id,
    )
    messages.success(
        request,
        f'Certificate for {certificate.user.email} revoked.',
    )
    return _redirect_back(request, certificate)


@staff_required
@require_POST
def certificate_unrevoke(request, certificate_id):
    """Reverse a revocation: clear the revoked fields."""
    certificate = get_object_or_404(CourseCertificate, pk=certificate_id)
    certificate.revoked_at = None
    certificate.revoked_by = None
    certificate.revoked_reason = ''
    certificate.save(
        update_fields=['revoked_at', 'revoked_by', 'revoked_reason'],
    )
    logger.info(
        'studio.certificate_unrevoke actor=%s certificate_id=%s user_id=%s',
        request.user.pk, certificate.pk, certificate.user_id,
    )
    messages.success(
        request,
        f'Certificate for {certificate.user.email} restored.',
    )
    return _redirect_back(request, certificate)
