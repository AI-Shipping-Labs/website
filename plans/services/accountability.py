"""Sprint accountability partner assignment helpers."""

import random

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from plans.models import (
    ACCOUNTABILITY_SOURCE_MANUAL,
    ACCOUNTABILITY_SOURCE_RANDOM,
    SprintAccountabilityPartner,
    SprintEnrollment,
)


def _enrolled_user_ids(sprint):
    return set(
        SprintEnrollment.objects
        .filter(sprint=sprint)
        .values_list('user_id', flat=True)
    )


def _validate_pair(*, sprint, member, partner):
    if member.pk == partner.pk:
        raise ValidationError('A member cannot be their own partner.')
    enrolled_ids = _enrolled_user_ids(sprint)
    missing = []
    if member.pk not in enrolled_ids:
        missing.append(member.email)
    if partner.pk not in enrolled_ids:
        missing.append(partner.email)
    if missing:
        raise ValidationError(
            'Accountability partners must both be enrolled in this sprint.'
        )


def assign_accountability_partners(
    *, sprint, member, partner, assigned_by, source=ACCOUNTABILITY_SOURCE_MANUAL,
):
    """Assign ``member`` and ``partner`` to each other in ``sprint``."""
    _validate_pair(sprint=sprint, member=member, partner=partner)
    created_count = 0
    with transaction.atomic():
        for left, right in ((member, partner), (partner, member)):
            assignment, created = SprintAccountabilityPartner.objects.get_or_create(
                sprint=sprint,
                member=left,
                partner=right,
                defaults={
                    'source': source,
                    'assigned_by': assigned_by,
                },
            )
            if created:
                created_count += 1
            elif source == ACCOUNTABILITY_SOURCE_MANUAL and (
                assignment.source != source
                or assignment.assigned_by_id != getattr(assigned_by, 'pk', None)
            ):
                assignment.source = source
                assignment.assigned_by = assigned_by
                assignment.save(update_fields=['source', 'assigned_by', 'updated_at'])
    return created_count


def remove_accountability_partners(*, sprint, member, partner):
    """Remove the reciprocal partner assignment for two members."""
    return SprintAccountabilityPartner.objects.filter(
        sprint=sprint,
    ).filter(
        Q(member=member, partner=partner) | Q(member=partner, partner=member)
    ).delete()[0]


def clear_accountability_for_member(*, sprint, member):
    """Remove all partner assignments involving ``member`` in ``sprint``."""
    return SprintAccountabilityPartner.objects.filter(
        sprint=sprint,
    ).filter(Q(member=member) | Q(partner=member)).delete()[0]


def accountability_partners_by_user(sprint):
    """Return ``{member_id: [partner_user, ...]}`` for a sprint."""
    assignments = (
        SprintAccountabilityPartner.objects
        .filter(sprint=sprint)
        .select_related('partner')
        .order_by('partner__email', 'partner_id')
    )
    partners_by_user = {}
    for assignment in assignments:
        partners_by_user.setdefault(assignment.member_id, []).append(
            assignment.partner,
        )
    return partners_by_user


def randomize_accountability_partners(*, sprint, assigned_by, rng=None):
    """Randomly assign partners for members without manual partners.

    Existing random assignments are cleared before rerolling. Manual
    assignments remain in place, and members with any remaining partner are
    excluded from the random pool. If the random pool has an odd size, the
    final three members become a fully connected three-person pod.
    """
    if rng is None:
        rng = random

    with transaction.atomic():
        SprintAccountabilityPartner.objects.filter(
            sprint=sprint,
            source=ACCOUNTABILITY_SOURCE_RANDOM,
        ).delete()

        enrolled_members = list(
            sprint.enrollments
            .select_related('user')
            .order_by('enrolled_at', 'pk')
        )
        existing_member_ids = set(
            SprintAccountabilityPartner.objects
            .filter(sprint=sprint)
            .values_list('member_id', flat=True)
        )
        pool = [
            enrollment.user
            for enrollment in enrolled_members
            if enrollment.user_id not in existing_member_ids
        ]
        rng.shuffle(pool)

        pair_count = 0
        while len(pool) >= 4:
            member = pool.pop()
            partner = pool.pop()
            assign_accountability_partners(
                sprint=sprint,
                member=member,
                partner=partner,
                assigned_by=assigned_by,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            )
            pair_count += 1

        if len(pool) == 3:
            first, second, third = pool
            for member, partner in (
                (first, second),
                (first, third),
                (second, third),
            ):
                assign_accountability_partners(
                    sprint=sprint,
                    member=member,
                    partner=partner,
                    assigned_by=assigned_by,
                    source=ACCOUNTABILITY_SOURCE_RANDOM,
                )
            pair_count += 3
        elif len(pool) == 2:
            assign_accountability_partners(
                sprint=sprint,
                member=pool[0],
                partner=pool[1],
                assigned_by=assigned_by,
                source=ACCOUNTABILITY_SOURCE_RANDOM,
            )
            pair_count += 1

    return {
        'assigned_pair_count': pair_count,
        'unassigned_count': len(pool) if len(pool) == 1 else 0,
    }
