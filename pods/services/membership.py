"""Pod rules shared by member views, Studio and the staff API (issue #1918).

One mechanism for approval and the waiting list: a ``PodJoinRequest`` is
``pending`` while the pod has a free seat and ``waitlisted`` while it is
full. Nobody is ever approved automatically; when a seat opens the oldest
waitlisted requests move back to ``pending`` for the owner (or staff).
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.services.email_resolution import normalize_email, resolve_users_by_emails
from content.models import CohortEnrollment
from notifications.models import Notification
from pods.models import (
    MEETING_MINUTES_CHOICES,
    MEMBERSHIP_SOURCE_CREATOR,
    MEMBERSHIP_SOURCE_REQUEST,
    MEMBERSHIP_SOURCE_STAFF,
    OPEN_REQUEST_STATUSES,
    POD_SOURCE_MEMBER,
    POD_SOURCE_STUDIO,
    POD_STATUS_ARCHIVED,
    POD_STATUS_CHOICES,
    POD_STATUS_CLOSED,
    POD_STATUS_OPEN,
    REQUEST_STATUS_APPROVED,
    REQUEST_STATUS_CANCELLED,
    REQUEST_STATUS_DECLINED,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_WAITLISTED,
    REQUEST_STATUS_WITHDRAWN,
    Pod,
    PodJoinRequest,
    PodMembership,
)
from pods.services import config as pods_config
from pods.services.people import is_cohort_participant, is_dated_cohort

NOTIFICATION_POD_REQUEST = 'pod_request'
NOTIFICATION_POD_REQUEST_DECIDED = 'pod_request_decided'

MSG_SELF_PACED = 'Pods need a dated cohort. A self-paced cohort cannot have pods.'
MSG_NOT_OPEN = 'This pod is not accepting requests right now.'
MSG_ALREADY_MEMBER = 'You are already in this pod.'
MSG_ALREADY_REQUESTED = 'You already asked to join this pod.'
MSG_NOT_ELIGIBLE = 'Only members of this cohort can join its pods.'
MSG_REQUEST_NOT_OPEN = 'This request has already been answered.'
MSG_NOT_A_MEMBER = 'That person is not a member of this pod.'

VALID_MEETING_MINUTES = {value for value, _label in MEETING_MINUTES_CHOICES}
VALID_STATUSES = {value for value, _label in POD_STATUS_CHOICES}


class PodError(Exception):
    """A rule violation with a member-facing message and a stable code."""

    def __init__(self, message, code='validation_error', field=''):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def msg_pod_full(pod, count=None):
    count = member_count(pod) if count is None else count
    return f'This pod is full ({count} of {pod.max_members}). Raise the size limit first.'


def msg_open_request_limit(limit):
    return (
        f'You already have {limit} open requests in this cohort. '
        'Withdraw one to request another pod.'
    )


def msg_created_limit(limit):
    return f'You can start up to {limit} pods in this cohort.'


def msg_size_below_members(count):
    return f'The size limit cannot be lower than the current number of members ({count}).'


# --- URLs -----------------------------------------------------------------

def pod_url(pod):
    return reverse('pod_detail', kwargs={'slug': pod.cohort.course.slug, 'pod_id': pod.pk})


def pods_tab_url(cohort):
    url = reverse('course_pods', kwargs={'slug': cohort.course.slug})
    return f'{url}?{urlencode({"cohort": cohort.external_key})}' if cohort.external_key else url


# --- Counts ---------------------------------------------------------------

def member_count(pod):
    return pod.memberships.count()


def free_seats(pod, count=None):
    count = member_count(pod) if count is None else count
    return max(pod.max_members - count, 0)


def is_member(pod, user):
    if not getattr(user, 'is_authenticated', False):
        return False
    return pod.memberships.filter(user=user).exists()


def open_request_for(pod, user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return pod.join_requests.filter(user=user, status__in=OPEN_REQUEST_STATUSES).first()


def waitlist_position(join_request):
    if join_request.status != REQUEST_STATUS_WAITLISTED:
        return None
    return PodJoinRequest.objects.filter(
        pod_id=join_request.pod_id,
        status=REQUEST_STATUS_WAITLISTED,
        created_at__lt=join_request.created_at,
    ).count() + 1


# --- Field validation -----------------------------------------------------

@dataclass
class PodFields:
    name: str = ''
    purpose: str = ''
    max_members: int = 4
    meeting_count: int = 1
    meeting_minutes: int = 60


def _parse_int(raw, field_name, label, minimum, maximum, errors):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        errors[field_name] = f'{label} must be a whole number.'
        return None
    if value < minimum or value > maximum:
        errors[field_name] = f'{label} must be between {minimum} and {maximum}.'
        return None
    return value


def clean_pod_fields(data, *, current_members=0, partial=False, existing=None):
    """Validate the editable settings. Returns ``(cleaned_dict, errors)``.

    ``data`` maps field names to raw values; missing keys take the configured
    defaults (create) or are skipped (``partial=True``).
    """
    errors = {}
    cleaned = {}
    defaults = {
        'max_members': pods_config.default_max_members(),
        'meeting_count': pods_config.default_meeting_count(),
        'meeting_minutes': pods_config.default_meeting_minutes(),
    }

    def present(key):
        return key in data and data[key] is not None and str(data[key]).strip() != ''

    if not partial or 'name' in data:
        name = str(data.get('name') or '').strip()
        if not name:
            errors['name'] = 'Name is required.'
        elif len(name) > 80:
            errors['name'] = 'Name must be 80 characters or fewer.'
        else:
            cleaned['name'] = name
    if not partial or 'purpose' in data:
        purpose = str(data.get('purpose') or '').strip()
        if not purpose:
            errors['purpose'] = 'Purpose is required.'
        elif len(purpose) > 500:
            errors['purpose'] = 'Purpose must be 500 characters or fewer.'
        else:
            cleaned['purpose'] = purpose
    if present('max_members') or not partial:
        raw = data['max_members'] if present('max_members') else defaults['max_members']
        value = _parse_int(raw, 'max_members', 'Size limit', 1, 12, errors)
        if value is not None:
            if value < current_members:
                errors['max_members'] = msg_size_below_members(current_members)
            else:
                cleaned['max_members'] = value
    if present('meeting_count') or not partial:
        raw = data['meeting_count'] if present('meeting_count') else defaults['meeting_count']
        value = _parse_int(raw, 'meeting_count', 'Number of meetings', 1, 20, errors)
        if value is not None:
            cleaned['meeting_count'] = value
    if present('meeting_minutes') or not partial:
        raw = data['meeting_minutes'] if present('meeting_minutes') else defaults['meeting_minutes']
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            value = None
        if value not in VALID_MEETING_MINUTES:
            errors['meeting_minutes'] = 'Meeting length must be 30, 45, 60 or 90 minutes.'
        else:
            cleaned['meeting_minutes'] = value
    return cleaned, errors


# --- Notifications --------------------------------------------------------

def _notify(user, title, url, notification_type, body=''):
    if user is None:
        return None
    return Notification.objects.create(
        user=user,
        title=title,
        body=body,
        url=url,
        notification_type=notification_type,
    )


# --- Queue maintenance ----------------------------------------------------

def _sync_queue(pod, *, notify_owner=True):
    """Keep pending/waitlisted consistent with the free seats.

    Full pod: every pending request becomes waitlisted. Free seats: the
    oldest waitlisted requests move to pending, one per free seat not
    already covered by a pending request, and the owner is told once.
    """
    if pod.status == POD_STATUS_ARCHIVED:
        return 0
    seats = free_seats(pod)
    if seats == 0:
        pod.join_requests.filter(status=REQUEST_STATUS_PENDING).update(
            status=REQUEST_STATUS_WAITLISTED,
        )
        return 0
    pending = pod.join_requests.filter(status=REQUEST_STATUS_PENDING).count()
    to_promote = max(seats - pending, 0)
    if not to_promote:
        return 0
    promote_ids = list(
        pod.join_requests.filter(status=REQUEST_STATUS_WAITLISTED)
        .order_by('created_at', 'pk')
        .values_list('pk', flat=True)[:to_promote]
    )
    if not promote_ids:
        return 0
    PodJoinRequest.objects.filter(pk__in=promote_ids).update(status=REQUEST_STATUS_PENDING)
    if notify_owner and pod.owner_id:
        _notify(
            pod.owner,
            f'A seat opened in {pod.name}',
            pod_url(pod),
            NOTIFICATION_POD_REQUEST,
        )
    return len(promote_ids)


def _lock(pod):
    return Pod.objects.select_for_update().select_related('cohort__course', 'owner').get(pk=pod.pk)


# --- Creating pods --------------------------------------------------------

def ensure_dated_cohort(cohort):
    if not is_dated_cohort(cohort):
        raise PodError(MSG_SELF_PACED, code='validation_error', field='cohort')


@transaction.atomic
def start_member_pod(user, cohort, data):
    """A member starts a pod of one: owner and first member."""
    ensure_dated_cohort(cohort)
    if not is_cohort_participant(user, cohort):
        raise PodError(MSG_NOT_ELIGIBLE, code='not_eligible')
    cleaned, errors = clean_pod_fields(data)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    limit = pods_config.max_created_per_member()
    created = Pod.objects.filter(
        cohort=cohort, created_by=user, source=POD_SOURCE_MEMBER,
    ).exclude(status=POD_STATUS_ARCHIVED).count()
    if created >= limit:
        raise PodError(msg_created_limit(limit), code='pod_limit')
    pod = Pod.objects.create(
        cohort=cohort,
        owner=user,
        created_by=user,
        source=POD_SOURCE_MEMBER,
        **cleaned,
    )
    PodMembership.objects.create(pod=pod, user=user, added_by=user, source=MEMBERSHIP_SOURCE_CREATOR)
    return pod


@dataclass
class AddResults:
    added: list = field(default_factory=list)
    not_enrolled: list = field(default_factory=list)
    unknown_user: list = field(default_factory=list)
    already_member: list = field(default_factory=list)
    over_capacity: list = field(default_factory=list)

    def as_dict(self):
        return {
            'added': self.added,
            'not_enrolled': self.not_enrolled,
            'unknown_user': self.unknown_user,
            'already_member': self.already_member,
            'over_capacity': self.over_capacity,
        }

    def merge(self, other):
        for key, values in other.as_dict().items():
            target = getattr(self, key)
            target.extend(v for v in values if v not in target)


def normalize_email_list(raw):
    """Accept a list or a newline/comma separated string; dedupe in order."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace(',', '\n').splitlines()
    seen = []
    for item in raw:
        if not isinstance(item, str):
            continue
        email = normalize_email(item)
        if email and email not in seen:
            seen.append(email)
    return seen


@transaction.atomic
def create_staff_pod(*, cohort, data, owner_email='', member_emails=None, actor=None, source):
    """Studio/API create. Returns ``(pod, AddResults, created)``.

    The owner (when given) must be enrolled and becomes the first member.

    Idempotent: a non-archived pod in the same cohort with the same name
    (case-insensitive) is the same pod. It is returned unchanged with
    ``created=False``; only emails not yet in it are added, so a retried or
    double-submitted create never puts the same people in two pods. Change
    settings or the owner of an existing pod with an update instead.
    """
    ensure_dated_cohort(cohort)
    cleaned, errors = clean_pod_fields(data)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    owner = None
    owner_email = normalize_email(owner_email or '')
    if owner_email:
        owner = resolve_users_by_emails([owner_email]).get(owner_email)
        if owner is None:
            raise PodError(f'No user with email {owner_email}.', code='unknown_user', field='owner_email')
        if not CohortEnrollment.objects.filter(cohort=cohort, user=owner).exists():
            raise PodError(
                f'{owner_email} is not enrolled in this cohort.',
                code='owner_not_enrolled',
                field='owner_email',
            )
    emails = normalize_email_list(member_emails)
    if owner_email:
        emails = [owner_email] + [e for e in emails if e != owner_email]
    existing = (
        Pod.objects.select_for_update()
        .filter(cohort=cohort, name__iexact=cleaned['name'])
        .exclude(status=POD_STATUS_ARCHIVED)
        .order_by('pk')
        .first()
    )
    if existing is not None:
        return existing, add_members_by_email(existing, emails, actor=actor, source=source), False
    pod = Pod.objects.create(
        cohort=cohort,
        owner=owner,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
        source=source,
        **cleaned,
    )
    results = add_members_by_email(pod, emails, actor=actor, source=source)
    return pod, results, True


@transaction.atomic
def add_members_by_email(pod, emails, *, actor=None, source):
    """Staff/API add. Idempotent; respects capacity; returns AddResults."""
    pod = _lock(pod)
    results = AddResults()
    emails = normalize_email_list(emails)
    users_by_email = resolve_users_by_emails(emails) if emails else {}
    count = member_count(pod)
    enrolled_ids = set(
        CohortEnrollment.objects.filter(
            cohort=pod.cohort, user__in=[u.pk for u in users_by_email.values()],
        ).values_list('user_id', flat=True)
    )
    for email in emails:
        user = users_by_email.get(email)
        if user is None:
            results.unknown_user.append(email)
            continue
        if pod.memberships.filter(user=user).exists():
            results.already_member.append(email)
            continue
        if user.pk not in enrolled_ids:
            results.not_enrolled.append(email)
            continue
        if count >= pod.max_members:
            results.over_capacity.append(email)
            continue
        _add_member(pod, user, actor=actor, source=source)
        count += 1
        results.added.append(email)
    if results.added:
        _sync_queue(pod)
    return results


def membership_source(source):
    """Map a pod/caller source to a ``PodMembership.source`` choice.

    Studio creates pods with ``source='studio'`` but its members are
    staff-added; the API keeps ``api``.
    """
    return {POD_SOURCE_STUDIO: MEMBERSHIP_SOURCE_STAFF}.get(source, source)


def _add_member(pod, user, *, actor, source):
    membership = PodMembership.objects.create(
        pod=pod,
        user=user,
        added_by=actor if getattr(actor, 'is_authenticated', False) else None,
        source=membership_source(source),
    )
    pod.join_requests.filter(user=user, status__in=OPEN_REQUEST_STATUSES).update(
        status=REQUEST_STATUS_APPROVED,
        decided_at=timezone.now(),
        decided_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    return membership


# --- Requests -------------------------------------------------------------

@transaction.atomic
def request_to_join(pod, user, message=''):
    pod = _lock(pod)
    if pod.status != POD_STATUS_OPEN:
        raise PodError(MSG_NOT_OPEN, code='pod_not_open')
    if not is_cohort_participant(user, pod.cohort):
        raise PodError(MSG_NOT_ELIGIBLE, code='not_eligible')
    if pod.memberships.filter(user=user).exists():
        raise PodError(MSG_ALREADY_MEMBER, code='already_member')
    if pod.join_requests.filter(user=user, status__in=OPEN_REQUEST_STATUSES).exists():
        raise PodError(MSG_ALREADY_REQUESTED, code='request_exists')
    limit = pods_config.max_open_requests_per_member()
    open_in_cohort = PodJoinRequest.objects.filter(
        user=user, pod__cohort=pod.cohort, status__in=OPEN_REQUEST_STATUSES,
    ).count()
    if open_in_cohort >= limit:
        raise PodError(msg_open_request_limit(limit), code='request_limit')
    message = (message or '').strip()
    if len(message) > 300:
        raise PodError('Keep your message to 300 characters or fewer.', field='message')
    status = REQUEST_STATUS_PENDING if free_seats(pod) else REQUEST_STATUS_WAITLISTED
    join_request = PodJoinRequest.objects.create(
        pod=pod, user=user, message=message, status=status,
    )
    if status == REQUEST_STATUS_PENDING and pod.owner_id and pod.owner_id != user.pk:
        _notify(
            pod.owner,
            f'New request to join {pod.name}',
            pod_url(pod),
            NOTIFICATION_POD_REQUEST,
        )
    return join_request


def _lock_request(join_request):
    return PodJoinRequest.objects.select_for_update().select_related('pod', 'user').get(pk=join_request.pk)


@transaction.atomic
def approve_request(join_request, actor):
    join_request = _lock_request(join_request)
    pod = _lock(join_request.pod)
    if not join_request.is_open:
        raise PodError(MSG_REQUEST_NOT_OPEN, code='request_not_open')
    count = member_count(pod)
    if count >= pod.max_members:
        raise PodError(msg_pod_full(pod, count), code='pod_full')
    if not pod.memberships.filter(user=join_request.user).exists():
        PodMembership.objects.create(
            pod=pod,
            user=join_request.user,
            added_by=actor if getattr(actor, 'is_authenticated', False) else None,
            source=MEMBERSHIP_SOURCE_REQUEST,
        )
    join_request.status = REQUEST_STATUS_APPROVED
    join_request.decided_at = timezone.now()
    join_request.decided_by = actor if getattr(actor, 'is_authenticated', False) else None
    join_request.save(update_fields=['status', 'decided_at', 'decided_by'])
    _sync_queue(pod)
    _notify(
        join_request.user,
        f'You joined {pod.name}',
        pod_url(pod),
        NOTIFICATION_POD_REQUEST_DECIDED,
    )
    return join_request


@transaction.atomic
def decline_request(join_request, actor):
    join_request = _lock_request(join_request)
    pod = join_request.pod
    if not join_request.is_open:
        raise PodError(MSG_REQUEST_NOT_OPEN, code='request_not_open')
    join_request.status = REQUEST_STATUS_DECLINED
    join_request.decided_at = timezone.now()
    join_request.decided_by = actor if getattr(actor, 'is_authenticated', False) else None
    join_request.save(update_fields=['status', 'decided_at', 'decided_by'])
    _notify(
        join_request.user,
        f'Your request to join {pod.name} was not accepted',
        pods_tab_url(pod.cohort),
        NOTIFICATION_POD_REQUEST_DECIDED,
        body='Browse other pods or start your own.',
    )
    return join_request


@transaction.atomic
def withdraw_request(join_request, user):
    join_request = _lock_request(join_request)
    if join_request.user_id != user.pk or not join_request.is_open:
        raise PodError(MSG_REQUEST_NOT_OPEN, code='request_not_open')
    join_request.status = REQUEST_STATUS_WITHDRAWN
    join_request.decided_at = timezone.now()
    join_request.save(update_fields=['status', 'decided_at'])
    return join_request


# --- Leaving and removal --------------------------------------------------

@transaction.atomic
def remove_member(pod, user, *, actor=None):
    """Leave or staff removal, with the ownership and archive rules."""
    pod = _lock(pod)
    deleted, _ = pod.memberships.filter(user=user).delete()
    if not deleted:
        raise PodError(MSG_NOT_A_MEMBER, code='not_a_member')
    remaining = list(pod.memberships.select_related('user').order_by('joined_at', 'pk'))
    update_fields = []
    if pod.owner_id == user.pk:
        pod.owner = remaining[0].user if remaining else None
        update_fields.append('owner')
    if not remaining and pod.source == POD_SOURCE_MEMBER:
        pod.status = POD_STATUS_ARCHIVED
        update_fields.append('status')
        pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES).update(
            status=REQUEST_STATUS_CANCELLED,
            decided_at=timezone.now(),
        )
    if update_fields:
        update_fields.append('updated_at')
        pod.save(update_fields=update_fields)
    if pod.status != POD_STATUS_ARCHIVED:
        _sync_queue(pod)
    return pod


def remove_user_from_cohort(user, cohort):
    """Unenrollment: leave every pod of ``cohort`` and cancel open requests."""
    for pod in Pod.objects.filter(cohort=cohort, memberships__user=user).distinct():
        remove_member(pod, user)
    PodJoinRequest.objects.filter(
        user=user, pod__cohort=cohort, status__in=OPEN_REQUEST_STATUSES,
    ).update(status=REQUEST_STATUS_CANCELLED, decided_at=timezone.now())


# --- Settings -------------------------------------------------------------

@transaction.atomic
def update_pod(pod, data, *, actor=None, staff=False):
    """Apply a partial settings change. Returns the pod.

    Owners may edit the text fields, the numbers, and switch ``open`` /
    ``closed``. Only staff archive or unarchive, or change the owner
    (``owner`` key: a ``User`` that must be a member, or ``None``).
    """
    pod = _lock(pod)
    count = member_count(pod)
    cleaned, errors = clean_pod_fields(data, current_members=count, partial=True)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    if 'status' in data:
        new_status = str(data['status'] or '').strip()
        if new_status not in VALID_STATUSES:
            raise PodError('Status must be open, closed or archived.', field='status')
        archive_change = POD_STATUS_ARCHIVED in (new_status, pod.status) and new_status != pod.status
        if archive_change and not staff:
            raise PodError('Only staff can archive or unarchive a pod.', code='forbidden', field='status')
        cleaned['status'] = new_status
    if 'owner' in data:
        if not staff:
            raise PodError('Only staff can change the owner.', code='forbidden', field='owner')
        owner = data['owner']
        if owner is not None and not pod.memberships.filter(user=owner).exists():
            raise PodError('The owner must be a member of the pod.', field='owner_email')
        cleaned['owner'] = owner
    if 'slack_channel_url' in data:
        cleaned['slack_channel_url'] = data['slack_channel_url']
    for key, value in cleaned.items():
        setattr(pod, key, value)
    pod.save()
    if pod.status == POD_STATUS_ARCHIVED:
        pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES).update(
            status=REQUEST_STATUS_CANCELLED, decided_at=timezone.now(),
        )
    else:
        _sync_queue(pod)
    return pod


def can_manage(pod, user):
    """Owner or staff: approve/decline, edit settings."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_staff) or (pod.owner_id is not None and pod.owner_id == user.pk)


__all__ = [
    'AddResults',
    'NOTIFICATION_POD_REQUEST',
    'NOTIFICATION_POD_REQUEST_DECIDED',
    'POD_STATUS_CLOSED',
    'PodError',
    'add_members_by_email',
    'approve_request',
    'can_manage',
    'clean_pod_fields',
    'create_staff_pod',
    'decline_request',
    'free_seats',
    'is_member',
    'member_count',
    'msg_pod_full',
    'open_request_for',
    'pod_url',
    'pods_tab_url',
    'remove_member',
    'remove_user_from_cohort',
    'request_to_join',
    'start_member_pod',
    'update_pod',
    'waitlist_position',
    'withdraw_request',
]
