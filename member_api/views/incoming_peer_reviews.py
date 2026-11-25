"""Read released incoming reviews for the authenticated submission owner."""

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from content.access import can_access
from content.models import ProjectSubmission
from content.services.attempt_reviews import assigned_reviews_for
from content.services.incoming_peer_reviews import incoming_breakdown
from content.services.project_phase import FEEDBACK, attempt_phase


def _released(submission, user):
    course = submission.course
    if course.status != 'published' or not course.peer_review_enabled:
        return False
    if not can_access(user, course):
        return False
    project = submission.course_project
    if project is None:
        return submission.status in ('review_complete', 'certified')
    assigned = assigned_reviews_for(user, project)
    phase = attempt_phase(project, submission, assigned, timezone.now())
    return phase.key == FEEDBACK


@csrf_exempt
@require_methods('GET')
@member_api_key_required('peer_reviews:read')
@openapi_spec(tag='Peer Reviews', methods={
    'GET': {
        'summary': 'Read released incoming reviews on your own submission',
        'description': (
            'Requires peer_reviews:read and course access. Another owner or unreleased '
            'feedback returns 403. Dated attempts release at Completed; self-paced '
            'attempts release in the existing feedback phase. Only completed reviews '
            'are exposed, with anonymous Reviewer N labels and exact preliminary '
            'criterion medians; criterion feedback is null when not stored.'
        ),
        'responses': {
            200: {'description': 'Incoming reviews and preliminary score breakdown.'},
            403: {'description': 'Not your submission or feedback not released.'},
            404: {'description': 'Submission not found.'},
        },
    },
})
def incoming_peer_reviews(request, submission_id):
    submission = ProjectSubmission.objects.select_related(
        'course', 'course_project', 'course_project__cohort',
    ).filter(pk=submission_id).first()
    if submission is None:
        return error_response('Submission not found', 'submission_not_found', status=404)
    if submission.user_id != request.user.pk:
        return error_response('This submission belongs to another student', 'forbidden', status=403)
    if not _released(submission, request.user):
        return error_response('Incoming feedback is not available yet', 'feedback_not_released', status=403)
    return JsonResponse(incoming_breakdown(submission))
