"""Record a homework or project submission on behalf of a member (staff).

Backs ``POST /api/submissions/on-behalf`` (``asl homework submit``). The
use case: a member sent their answer some other way (an email with a
project idea, a message in Slack) and staff want it on record as that
member's submission, possibly backdated.

What this module guarantees:

- Scoring goes through the same code as a learner's own submit:
  ``save_submission`` for homework (auto-score, full answer-set replace,
  cohort auto-enrollment) and the ``ProjectSubmission`` model validation
  for projects. Answers are normalized to the stored shape first (1-based
  option indices for choice questions).
- The submission row is marked ``submitted_on_behalf_by`` + note, and one
  ``CommunityAuditLog`` row (``submission_on_behalf``) names the acting
  staff user and API token. A learner's own later save clears the marker.
- An existing submission is never overwritten unless ``replace`` is set.
  A project already in peer review is never overwritten at all: reviews
  are assigned to that exact row.
- ``dry_run`` resolves, validates and scores everything, writes nothing,
  and fails exactly where the real call would.
- The member is not notified unless ``notify`` is set; then they get the
  bell notification plus the ``submission_recorded`` email.

The homework open/closed state and deadlines are deliberately NOT checked:
recording a late or retroactive submission is the point of this tool. The
response reports the homework state so the operator can see it.
"""

import datetime
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.services.email_resolution import resolve_user_by_email
from community.models import CommunityAuditLog
from content.models import CohortEnrollment, Course, CourseProject, ProjectSubmission, Unit
from content.models.homework import (
    MAX_LEARNING_IN_PUBLIC_LINKS,
    Homework,
    QuestionType,
    Submission,
)
from content.services import completion as completion_service
from content.services.course_cohorts import get_course_cohort_by_key
from content.services.homework_scoring import is_answer_correct
from content.services.homework_submissions import save_submission
from content.services.project_form_adapter import (
    LEARNING_IN_PUBLIC_CAP as PROJECT_LEARNING_IN_PUBLIC_CAP,
)
from content.services.project_form_adapter import submission_cohort
from content.services.submission_errors import OnBehalfError
from content.services.submission_errors import refuse_existing as _refuse_existing
from email_app.package_mail import send_package_mail
from notifications.models import Notification

logger = logging.getLogger(__name__)

User = get_user_model()

KIND_HOMEWORK = 'homework'
KIND_PROJECT = 'project'
KINDS = (KIND_HOMEWORK, KIND_PROJECT)

AUDIT_ACTION = 'submission_on_behalf'
NOTIFICATION_TYPE = 'submission_recorded'
EMAIL_TEMPLATE = 'submission_recorded'
NOTE_MAX_LENGTH = 2000

CHOICE_TYPES = (QuestionType.MULTIPLE_CHOICE, QuestionType.CHECKBOXES)


@dataclass
class Actor:
    """Who is recording the submission: the staff user and token label."""

    user: object
    token_label: str = ''


# --- Lookups -----------------------------------------------------------------


def resolve_member(identifier):
    """Resolve the target member from an email (primary, then alias) or id."""
    if isinstance(identifier, bool) or identifier in (None, ''):
        raise OnBehalfError(
            'user is required (email or numeric id)', 'missing_field',
            details={'field': 'user'},
        )
    raw = str(identifier).strip()
    if raw.isdigit():
        user = User.objects.filter(pk=int(raw)).first()
    else:
        user = resolve_user_by_email(raw.lower())
    if user is None:
        raise OnBehalfError(
            f'User not found: {raw}', 'user_not_found', status=404,
            details={'field': 'user'},
        )
    return user


def _course(slug):
    if not slug:
        raise OnBehalfError(
            'course is required', 'missing_field', details={'field': 'course'},
        )
    course = Course.objects.filter(slug=str(slug).strip()).first()
    if course is None:
        raise OnBehalfError(
            f'Course not found: {slug}', 'unknown_course', status=404,
            details={'field': 'course'},
        )
    return course


def resolve_homework(*, homework_id=None, course=None, homework=None,
                     cohort=None, member=None):
    """Find one ``Homework`` by id, or by course + homework slug (+ cohort).

    A homework slug exists once per cohort. Without ``cohort``, the
    cohorts the member is enrolled in break a tie; anything still
    ambiguous is refused with the candidate cohort keys.
    """
    base = Homework.objects.select_related('cohort', 'cohort__course')
    if homework_id not in (None, ''):
        try:
            pk = int(homework_id)
        except (TypeError, ValueError):
            raise OnBehalfError(
                'homework_id must be an integer', 'invalid_type',
                details={'field': 'homework_id', 'expected': 'integer'},
            ) from None
        found = base.filter(pk=pk).first()
        if found is None:
            raise OnBehalfError(
                f'Homework not found: {pk}', 'unknown_homework', status=404,
                details={'field': 'homework_id'},
            )
        return found

    if not homework:
        raise OnBehalfError(
            'Pass homework_id, or course and homework (the homework slug)',
            'missing_field', details={'field': 'homework'},
        )
    course_row = _course(course)
    candidates = base.filter(cohort__course=course_row, slug=str(homework).strip())
    if cohort not in (None, ''):
        cohort_row = get_course_cohort_by_key(course_row, cohort)
        if cohort_row is None:
            raise OnBehalfError(
                f'Unknown cohort {cohort!r} for course {course_row.slug!r}',
                'unknown_cohort', status=404, details={'field': 'cohort'},
            )
        candidates = candidates.filter(cohort=cohort_row)
    matches = list(candidates.order_by('pk'))
    if not matches:
        raise OnBehalfError(
            f'Homework {homework!r} not found in course {course_row.slug!r}',
            'unknown_homework', status=404, details={'field': 'homework'},
        )
    if len(matches) > 1 and member is not None:
        enrolled_cohort_ids = set(
            CohortEnrollment.objects.filter(
                user=member, cohort__in=[m.cohort for m in matches],
            ).values_list('cohort_id', flat=True)
        )
        enrolled = [m for m in matches if m.cohort_id in enrolled_cohort_ids]
        if len(enrolled) == 1:
            return enrolled[0]
    if len(matches) > 1:
        raise OnBehalfError(
            f'Homework {homework!r} exists in several cohorts; pass cohort',
            'ambiguous_homework', status=400,
            details={
                'field': 'cohort',
                'candidates': [
                    {'homework_id': m.pk, 'cohort': m.cohort.external_key or None,
                     'cohort_name': m.cohort.name}
                    for m in matches
                ],
            },
        )
    return matches[0]


def resolve_project(*, course=None, project=None):
    """Return ``(course, course_project_or_None)``.

    ``project`` is a dated attempt's slug. Omitting it targets the undated
    legacy ``/courses/<slug>/submit`` slot, which only exists while the
    course has no dated attempts.
    """
    course_row = _course(course)
    if project not in (None, ''):
        attempt = CourseProject.objects.filter(
            course=course_row, slug=str(project).strip(),
        ).select_related('cohort').first()
        if attempt is None:
            raise OnBehalfError(
                f'Project {project!r} not found in course {course_row.slug!r}',
                'unknown_project', status=404, details={'field': 'project'},
            )
        return course_row, attempt
    slugs = list(
        CourseProject.objects.filter(course=course_row)
        .order_by('submission_due_at', 'pk').values_list('slug', flat=True)
    )
    if slugs:
        raise OnBehalfError(
            'This course has dated project attempts; pass project',
            'missing_field', details={'field': 'project', 'candidates': slugs},
        )
    return course_row, None


# --- Field validation --------------------------------------------------------


def parse_submitted_at(value):
    """Parse an ISO-8601 override; naive times use the site time zone."""
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise OnBehalfError(
            'submitted_at must be an ISO-8601 string', 'invalid_type',
            details={'field': 'submitted_at', 'expected': 'string'},
        )
    try:
        parsed = parse_datetime(value.strip())
    except ValueError:
        parsed = None
    if parsed is None:
        raise OnBehalfError(
            f'submitted_at is not an ISO-8601 datetime: {value}',
            'invalid_value', details={'field': 'submitted_at'},
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    if parsed > timezone.now() + datetime.timedelta(minutes=5):
        raise OnBehalfError(
            'submitted_at cannot be in the future', 'invalid_value',
            details={'field': 'submitted_at'},
        )
    return parsed


def _url(value, field, *, required=False):
    text = (value or '').strip() if isinstance(value, str) else value
    if text in (None, ''):
        if required:
            raise OnBehalfError(
                f'{field} is required', 'missing_field', details={'field': field},
            )
        return ''
    if not isinstance(text, str):
        raise OnBehalfError(
            f'{field} must be a URL string', 'invalid_type',
            details={'field': field, 'expected': 'string'},
        )
    try:
        URLValidator()(text)
    except ValidationError:
        raise OnBehalfError(
            f'{field} is not a valid URL: {text}', 'invalid_value',
            details={'field': field},
        ) from None
    return text


def _links(value, cap):
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise OnBehalfError(
            'learning_in_public_links must be a list of URLs', 'invalid_type',
            details={'field': 'learning_in_public_links', 'expected': 'array'},
        )
    links = [_url(item, 'learning_in_public_links') for item in value]
    links = [link for link in links if link]
    if len(links) > cap:
        raise OnBehalfError(
            f'At most {cap} learning_in_public_links are allowed',
            'invalid_value', details={'field': 'learning_in_public_links'},
        )
    return links


def _hours(value, field):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        value = None
    try:
        hours = float(value)
    except (TypeError, ValueError):
        hours = -1
    if hours < 0:
        raise OnBehalfError(
            f'{field} must be a non-negative number of hours', 'invalid_value',
            details={'field': field},
        )
    return hours


def _note(value):
    if value in (None, ''):
        return ''
    if not isinstance(value, str):
        raise OnBehalfError(
            'note must be a string', 'invalid_type',
            details={'field': 'note', 'expected': 'string'},
        )
    note = value.strip()
    if len(note) > NOTE_MAX_LENGTH:
        raise OnBehalfError(
            f'note is longer than {NOTE_MAX_LENGTH} characters', 'invalid_value',
            details={'field': 'note'},
        )
    return note


def _question_key(question):
    """The stepper's stable question key (``source_question_id`` or ``q-<pk>``)."""
    return question.source_question_id or f'q-{question.pk}'


def _choice_answer(question, value):
    """Normalize a choice answer to stored 1-based indices.

    Accepts an index (``2``), several (``[1, 3]`` or ``"1,3"``), or the
    exact option label(s).
    """
    options = question.options_list
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and all(
        part.strip().isdigit() for part in value.split(',') if part.strip()
    ):
        items = [part for part in value.split(',') if part.strip()]
    else:
        items = [value]
    positions = set()
    for item in items:
        if isinstance(item, bool):
            item = None
        if isinstance(item, int) or (isinstance(item, str) and item.strip().isdigit()):
            index = int(item)
        elif isinstance(item, str) and item.strip() in options:
            index = options.index(item.strip()) + 1
        else:
            index = 0
        if not 1 <= index <= len(options):
            raise OnBehalfError(
                f'Answer {item!r} is not an option of question '
                f'{_question_key(question)!r} (use a 1-based index or the '
                f'exact option text)',
                'invalid_answer', details={'field': 'answers', 'question': _question_key(question)},
            )
        positions.add(index)
    if question.question_type == QuestionType.MULTIPLE_CHOICE and len(positions) > 1:
        raise OnBehalfError(
            f'Question {_question_key(question)!r} takes a single option',
            'invalid_answer', details={'field': 'answers', 'question': _question_key(question)},
        )
    return ','.join(str(p) for p in sorted(positions))


def normalize_answers(homework, raw_answers):
    """Map ``{question key: answer}`` to ``{question pk: stored answer text}``.

    A key is a question's ``source_question_id``, its ``q-<pk>`` stepper
    key, or its numeric pk. Blank answers are dropped (the question then
    has no answer, exactly like a learner leaving it blank).
    """
    if raw_answers in (None, ''):
        return {}
    if not isinstance(raw_answers, dict):
        raise OnBehalfError(
            'answers must be an object of {question key: answer}', 'invalid_type',
            details={'field': 'answers', 'expected': 'object'},
        )
    questions = list(homework.questions.all())
    by_key = {}
    for question in questions:
        by_key[_question_key(question)] = question
        by_key[f'q-{question.pk}'] = question
        by_key[str(question.pk)] = question
    normalized = {}
    for key, value in raw_answers.items():
        question = by_key.get(str(key).strip())
        if question is None:
            raise OnBehalfError(
                f'Unknown question {key!r} for this homework',
                'unknown_question',
                details={
                    'field': 'answers',
                    'question': str(key),
                    'allowed': [_question_key(q) for q in questions],
                },
            )
        if value is None or (isinstance(value, (str, list)) and not value):
            continue
        if question.question_type in CHOICE_TYPES:
            text = _choice_answer(question, value)
        else:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise OnBehalfError(
                    f'Answer for {key!r} must be text', 'invalid_answer',
                    details={'field': 'answers', 'question': str(key)},
                )
            text = str(value).strip()
        if text:
            normalized[question.pk] = text
    return normalized


# --- Serialization -----------------------------------------------------------


def _email(user):
    return getattr(user, 'email', None) if user is not None else None


def _homework_submission_summary(submission):
    answers = sorted(submission.answers.select_related('question'), key=lambda a: a.question_id)
    return {
        'id': submission.pk,
        'submitted_at': submission.submitted_at.isoformat(),
        'homework_link': submission.homework_link or '',
        'learning_in_public_links': list(submission.learning_in_public_links or []),
        'time_spent_lectures': submission.time_spent_lectures,
        'time_spent_homework': submission.time_spent_homework,
        'questions_score': submission.questions_score,
        'total_score': submission.total_score,
        'answers': [
            {
                'question_id': a.question_id,
                'question_key': _question_key(a.question),
                'answer_text': a.answer_text,
                'is_correct': a.is_correct,
            }
            for a in answers
        ],
        'submitted_on_behalf_by': _email(submission.submitted_on_behalf_by),
        'on_behalf_note': submission.on_behalf_note,
    }


def _project_submission_summary(submission):
    return {
        'id': submission.pk,
        'submitted_at': submission.submitted_at.isoformat() if submission.submitted_at else None,
        'status': submission.status,
        'project_url': submission.project_url,
        'description': submission.description,
        'commit_id': submission.commit_id,
        'learning_in_public_links': list(submission.learning_in_public_links or []),
        'time_spent': submission.time_spent,
        'cohort': submission.cohort.external_key if submission.cohort_id else None,
        'submitted_on_behalf_by': _email(submission.submitted_on_behalf_by),
        'on_behalf_note': submission.on_behalf_note,
    }


def _homework_payload(homework):
    return {
        'id': homework.pk,
        'slug': homework.slug,
        'title': homework.title,
        'course': homework.cohort.course.slug,
        'cohort': homework.cohort.external_key or None,
        'state': homework.state,
        'accepting_submissions': homework.is_accepting_submissions,
    }


def _status(existing, dry_run):
    verb = 'replace' if existing else 'create'
    if dry_run:
        return f'would_{verb}'
    return 'replaced' if existing else 'created'


def _audit(member, actor, details):
    return CommunityAuditLog.objects.create(
        user=member,
        action=AUDIT_ACTION,
        details=json.dumps(
            {
                **details,
                'actor_email': _email(actor.user),
                'actor_token': actor.token_label,
            },
            sort_keys=True,
        ),
    )


# --- Homework ----------------------------------------------------------------


def homework_unit(homework):
    """The earliest syllabus unit carrying this homework, or ``None``."""
    if homework.content_id is None:
        return None
    units = (
        Unit.objects
        .filter(module__course=homework.cohort.course, source_content_id=homework.content_id)
        .select_related('module', 'module__parent', 'module__course')
    )
    return min(
        units,
        key=lambda unit: (
            (unit.module.parent.sort_order, unit.module.parent.pk) if unit.module.parent
            else (unit.module.sort_order, unit.module.pk),
            unit.module.sort_order, unit.sort_order, unit.pk,
        ),
        default=None,
    )


def submit_homework_on_behalf(
    *, actor, member, homework, answers=None, homework_link='',
    learning_in_public_links=None, time_spent_lectures=None,
    time_spent_homework=None, submitted_at=None, note='', replace=False,
    dry_run=False, notify=False,
):
    """Record (or replace) ``member``'s submission for ``homework``."""
    answers_by_question_id = normalize_answers(homework, answers)
    homework_link = _url(homework_link, 'homework_link')
    links = _links(learning_in_public_links, MAX_LEARNING_IN_PUBLIC_LINKS)
    time_spent_lectures = _hours(time_spent_lectures, 'time_spent_lectures')
    time_spent_homework = _hours(time_spent_homework, 'time_spent_homework')
    submitted_at = parse_submitted_at(submitted_at)
    note = _note(note)

    existing = (
        Submission.objects.select_related('submitted_on_behalf_by')
        .filter(homework=homework, student=member).first()
    )
    existing_summary = _homework_submission_summary(existing) if existing else None
    if existing is not None and not replace:
        _refuse_existing(existing_summary, 'homework')

    enrolled = CohortEnrollment.objects.filter(user=member, cohort=homework.cohort).exists()
    payload = {
        'kind': KIND_HOMEWORK,
        'dry_run': dry_run,
        'status': _status(existing, dry_run),
        'user': {'id': member.pk, 'email': member.email},
        'homework': _homework_payload(homework),
        'cohort_enrollment_created': not enrolled,
        'replaced_submission': existing_summary,
        'notify': notify,
        'audit_log_id': None,
    }

    if dry_run:
        questions = {q.pk: q for q in homework.questions.all()}
        preview_answers = []
        score = 0
        for question_id, text in sorted(answers_by_question_id.items()):
            question = questions[question_id]
            correct = is_answer_correct(question, text)
            if correct:
                score += question.scores_for_correct_answer
            preview_answers.append({
                'question_id': question_id,
                'question_key': _question_key(question),
                'answer_text': text,
                'is_correct': correct,
            })
        payload['submission'] = {
            'id': existing.pk if existing else None,
            'submitted_at': (submitted_at or timezone.now()).isoformat(),
            'homework_link': homework_link,
            'learning_in_public_links': links,
            'time_spent_lectures': time_spent_lectures,
            'time_spent_homework': time_spent_homework,
            'questions_score': score,
            'total_score': score,
            'answers': preview_answers,
            'submitted_on_behalf_by': _email(actor.user),
            'on_behalf_note': note,
        }
        return payload

    with transaction.atomic():
        submission = save_submission(
            homework, member,
            homework_link=homework_link,
            answers_by_question_id=answers_by_question_id,
            learning_in_public_links=links,
            time_spent_lectures=time_spent_lectures,
            time_spent_homework=time_spent_homework,
            submitted_at=submitted_at,
            on_behalf_by=actor.user,
            on_behalf_note=note,
        )
        if homework.stepper_enabled:
            # The learner's own stepper submit marks the unit complete.
            unit = homework_unit(homework)
            if unit is not None:
                completion_service.mark_completed(member, unit, when=submission.submitted_at)
        audit = _audit(member, actor, {
            'kind': KIND_HOMEWORK,
            'homework_id': homework.pk,
            'submission_id': submission.pk,
            'replaced': existing is not None,
            'submitted_at': submission.submitted_at.isoformat(),
            'note': note,
            'notify': notify,
        })

    submission.refresh_from_db()
    payload['submission'] = _homework_submission_summary(submission)
    payload['audit_log_id'] = audit.pk
    if notify:
        payload['notification'] = notify_member(
            member, submission,
            assignment_title=homework.title,
            course_title=homework.cohort.course.title,
            url=homework_page_path(homework),
        )
    return payload


def homework_page_path(homework):
    """Site-relative learner URL for the homework, or the course page."""
    unit = homework_unit(homework)
    if unit is None:
        return homework.cohort.course.get_absolute_url()
    path = unit.get_absolute_url()
    key = (homework.cohort.external_key or '').strip()
    if key:
        path = f'{path}?{urlencode({"cohort": key})}'
    return path


# --- Project -----------------------------------------------------------------


def project_page_path(course, attempt):
    """Site-relative learner URL of the project submission form."""
    if attempt is not None:
        return reverse('course_project_submit', kwargs={'slug': course.slug, 'attempt_slug': attempt.slug})
    return reverse('project_submit', kwargs={'slug': course.slug})


def submit_project_on_behalf(
    *, actor, member, course, attempt=None, project_url='', description='',
    commit_id='', learning_in_public_links=None, time_spent=None,
    submitted_at=None, note='', replace=False, dry_run=False, notify=False,
):
    """Record (or replace) ``member``'s project submission.

    A replace overwrites every submission field; ``submitted_at`` keeps its
    original value unless overridden, because review batches key off it.
    """
    project_url = _url(project_url, 'project_url', required=True)
    if description not in (None, '') and not isinstance(description, str):
        raise OnBehalfError(
            'description must be a string', 'invalid_type',
            details={'field': 'description', 'expected': 'string'},
        )
    description = (description or '').strip()
    commit_id = (commit_id or '').strip() if isinstance(commit_id, str) else commit_id
    if commit_id and (not isinstance(commit_id, str) or not (
        7 <= len(commit_id) <= 40 and all(c in '0123456789abcdefABCDEF' for c in commit_id)
    )):
        raise OnBehalfError(
            'commit_id must be 7-40 hex characters', 'invalid_value',
            details={'field': 'commit_id'},
        )
    links = _links(learning_in_public_links, PROJECT_LEARNING_IN_PUBLIC_CAP)
    time_spent = _hours(time_spent, 'time_spent')
    submitted_at = parse_submitted_at(submitted_at)
    note = _note(note)

    existing_qs = ProjectSubmission.objects.select_related('cohort', 'submitted_on_behalf_by').filter(
        user=member, course=course,
    )
    existing_qs = (
        existing_qs.filter(course_project=attempt) if attempt is not None
        else existing_qs.filter(course_project__isnull=True)
    )
    existing = existing_qs.first()
    existing_summary = _project_submission_summary(existing) if existing else None
    if existing is not None and existing.status != 'submitted':
        raise OnBehalfError(
            f'The submission is already {existing.status}; peer reviews are '
            'assigned to it, so it cannot be replaced',
            'submission_locked', status=409,
            details={'existing_submission': existing_summary},
        )
    if existing is not None and not replace:
        _refuse_existing(existing_summary, 'project')

    cohort = (
        existing.cohort if existing is not None
        else submission_cohort(member, course, attempt.cohort if attempt else None)
    )
    payload = {
        'kind': KIND_PROJECT,
        'dry_run': dry_run,
        'status': _status(existing, dry_run),
        'user': {'id': member.pk, 'email': member.email},
        'project': {
            'course': course.slug,
            'slug': attempt.slug if attempt else None,
            'title': attempt.title if attempt else course.title,
            'submission_due_at': attempt.submission_due_at.isoformat() if attempt else None,
        },
        'replaced_submission': existing_summary,
        'notify': notify,
        'audit_log_id': None,
    }

    submission = existing or ProjectSubmission(
        user=member, course=course, course_project=attempt, cohort=cohort,
    )
    submission.project_url = project_url
    submission.description = description
    submission.commit_id = commit_id or ''
    submission.learning_in_public_links = links
    submission.time_spent = time_spent
    submission.submitted_on_behalf_by = actor.user
    submission.on_behalf_note = note
    try:
        submission.full_clean()
    except ValidationError as exc:
        raise OnBehalfError(
            'Submission failed validation', 'invalid_value',
            details={'errors': exc.message_dict},
        ) from None

    if dry_run:
        summary = _project_submission_summary(submission)
        summary['submitted_at'] = (
            submitted_at or (existing.submitted_at if existing else timezone.now())
        ).isoformat()
        payload['submission'] = summary
        return payload

    with transaction.atomic():
        submission.save()
        if submitted_at is not None:
            # ``submitted_at`` is auto_now_add; a backdate needs its own write.
            ProjectSubmission.objects.filter(pk=submission.pk).update(submitted_at=submitted_at)
        audit = _audit(member, actor, {
            'kind': KIND_PROJECT,
            'course': course.slug,
            'project': attempt.slug if attempt else None,
            'submission_id': submission.pk,
            'replaced': existing is not None,
            'submitted_at': (submitted_at or submission.submitted_at).isoformat(),
            'note': note,
            'notify': notify,
        })

    submission.refresh_from_db()
    payload['submission'] = _project_submission_summary(submission)
    payload['audit_log_id'] = audit.pk
    if notify:
        payload['notification'] = notify_member(
            member, submission,
            assignment_title=attempt.title if attempt else 'Course project',
            course_title=course.title,
            url=project_page_path(course, attempt),
        )
    return payload


# --- Notification ------------------------------------------------------------


def notify_member(member, submission, *, assignment_title, course_title, url):
    """Bell notification + ``submission_recorded`` email (``notify`` only).

    Best effort after the submission committed: a failure is reported in
    the response and logged, never rolled back into the submission.
    """
    result = {'notification_id': None, 'email_delivery_id': None, 'error': ''}
    try:
        notification = Notification.objects.create(
            user=member,
            title=f'Your submission for {assignment_title} was recorded',
            body=(
                f'The AI Shipping Labs team recorded your submission for '
                f'{assignment_title} ({course_title}).'
            ),
            url=url,
            notification_type=NOTIFICATION_TYPE,
        )
        result['notification_id'] = notification.pk
        delivery = send_package_mail(
            member, EMAIL_TEMPLATE,
            {'assignment_title': assignment_title, 'course_title': course_title},
            related=submission,
        )
        result['email_delivery_id'] = str(delivery.pk) if delivery is not None else None
    except Exception as exc:  # noqa: BLE001 -- the submission is already saved
        logger.exception(
            'submission_recorded notification failed for submission %s', submission.pk,
        )
        result['error'] = type(exc).__name__
    return result
