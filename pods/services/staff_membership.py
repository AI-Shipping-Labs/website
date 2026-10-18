"""Staff/API study-group creation, bulk membership, and settings."""

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from accounts.services.email_resolution import normalize_email, resolve_users_by_emails
from pods.models import (
    OPEN_REQUEST_STATUSES,
    POD_STATUS_ARCHIVED,
    POD_STATUS_CHOICES,
    REQUEST_STATUS_CANCELLED,
    Pod,
)
from pods.services.activity import activity_for_pod, as_activity
from pods.services.meeting_rules import used_meeting_count
from pods.services.membership_common import (
    PodError,
    add_member,
    ensure_can_have_pods,
    lock_pod,
    msg_count_below_used,
    msg_owner_not_enrolled,
)
from pods.services.pod_fields import clean_pod_fields

VALID_STATUSES = {value for value, _label in POD_STATUS_CHOICES}


@dataclass
class AddResults:
    added: list = field(default_factory=list)
    not_enrolled: list = field(default_factory=list)
    unknown_user: list = field(default_factory=list)
    already_member: list = field(default_factory=list)

    def as_dict(self):
        return {
            "added": self.added,
            "not_enrolled": self.not_enrolled,
            "unknown_user": self.unknown_user,
            "already_member": self.already_member,
        }

    def merge(self, other):
        for key, values in other.as_dict().items():
            target = getattr(self, key)
            for value in values:
                if value not in target:
                    target.append(value)


def normalize_email_list(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace(",", "\n").splitlines()
    seen = []
    for item in raw:
        if not isinstance(item, str):
            continue
        email = normalize_email(item)
        if email and email not in seen:
            seen.append(email)
    return seen


def _resolve_owner(owner_email, activity):
    owner_email = normalize_email(owner_email or "")
    if not owner_email:
        return None, owner_email
    owner = resolve_users_by_emails([owner_email]).get(owner_email)
    if owner is None:
        raise PodError(f"No user with email {owner_email}.", code="unknown_user", field="owner_email")
    if not activity.is_enrolled(owner):
        raise PodError(
            msg_owner_not_enrolled(owner_email, activity.noun),
            code="owner_not_enrolled",
            field="owner_email",
        )
    return owner, owner_email


def _owner_first(owner_email, member_emails):
    emails = normalize_email_list(member_emails)
    if not owner_email:
        return emails
    without_owner = []
    for email in emails:
        if email != owner_email:
            without_owner.append(email)
    return [owner_email, *without_owner]


def _existing_pod(activity, name):
    return (
        Pod.objects.select_for_update()
        .filter(name__iexact=name, **activity.pod_filter())
        .exclude(status=POD_STATUS_ARCHIVED)
        .order_by("pk")
        .first()
    )


@transaction.atomic
def create_staff_pod(
    *, data, cohort=None, sprint=None, activity=None, owner_email="", member_emails=None, actor=None, source
):
    activity = as_activity(activity or sprint or cohort)
    ensure_can_have_pods(activity)
    cleaned, errors = clean_pod_fields(data, staff=True)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    owner, owner_email = _resolve_owner(owner_email, activity)
    emails = _owner_first(owner_email, member_emails)
    existing = _existing_pod(activity, cleaned["name"])
    if existing is not None:
        return existing, add_members_by_email(existing, emails, actor=actor, source=source), False
    created_by = None
    if getattr(actor, "is_authenticated", False):
        created_by = actor
    pod = Pod.objects.create(
        **activity.pod_filter(),
        owner=owner,
        created_by=created_by,
        source=source,
        **cleaned,
    )
    return pod, add_members_by_email(pod, emails, actor=actor, source=source), True


@transaction.atomic
def add_members_by_email(pod, emails, *, actor=None, source):
    pod = lock_pod(pod)
    results = AddResults()
    emails = normalize_email_list(emails)
    users_by_email = {}
    if emails:
        users_by_email = resolve_users_by_emails(emails)
    enrolled_ids = activity_for_pod(pod).enrolled_user_ids(u.pk for u in users_by_email.values())
    for email in emails:
        user = users_by_email.get(email)
        if user is None:
            results.unknown_user.append(email)
        elif pod.memberships.filter(user=user).exists():
            results.already_member.append(email)
        elif user.pk not in enrolled_ids:
            results.not_enrolled.append(email)
        else:
            add_member(pod, user, actor=actor, source=source)
            results.added.append(email)
    return results


def _settings_changes(pod, data, staff):
    cleaned, errors = clean_pod_fields(data, partial=True, staff=staff)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    if "meeting_count" in cleaned and cleaned["meeting_count"] != pod.meeting_count:
        used = used_meeting_count(pod)
        if cleaned["meeting_count"] < used:
            raise PodError(msg_count_below_used(used), field="meeting_count")
    return cleaned


def _status_change(pod, data, cleaned, staff):
    if "status" not in data:
        return
    new_status = str(data["status"] or "").strip()
    if new_status not in VALID_STATUSES:
        raise PodError("Status must be open, closed or archived.", field="status")
    archive_change = POD_STATUS_ARCHIVED in (new_status, pod.status) and new_status != pod.status
    if archive_change and not staff:
        raise PodError("Only staff can archive or unarchive a pod.", code="forbidden", field="status")
    cleaned["status"] = new_status


def _owner_change(pod, data, cleaned, staff):
    if "owner" not in data:
        return
    if not staff:
        raise PodError("Only staff can change the owner.", code="forbidden", field="owner")
    owner = data["owner"]
    if owner is not None and not pod.memberships.filter(user=owner).exists():
        raise PodError("The owner must be a member of the pod.", field="owner_email")
    cleaned["owner"] = owner


@transaction.atomic
def update_pod(pod, data, *, actor=None, staff=False):
    pod = lock_pod(pod)
    cleaned = _settings_changes(pod, data, staff)
    _status_change(pod, data, cleaned, staff)
    _owner_change(pod, data, cleaned, staff)
    for optional in ("slack_channel_url", "meeting_url"):
        if optional in data:
            cleaned[optional] = data[optional]
    for key, value in cleaned.items():
        setattr(pod, key, value)
    pod.save()
    if pod.status == POD_STATUS_ARCHIVED:
        pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES).update(
            status=REQUEST_STATUS_CANCELLED,
            decided_at=timezone.now(),
        )
    return pod


def can_manage(pod, user):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_staff) or (pod.owner_id is not None and pod.owner_id == user.pk)
