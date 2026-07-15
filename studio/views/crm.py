"""Studio CRM views (issue #560).

The CRM surface is the canonical, staff-only place where relationship
context lives: plans, member notes, persona, summary, next steps.
Member-facing surfaces must NOT render any field on :class:`CRMRecord`.

The CRM is opt-in: a user is in the CRM iff a :class:`CRMRecord` row
exists for them. Staff create the row via the ``Track in CRM`` button on
``/studio/users/<id>/``.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.lifecycle import (
    ACCOUNT_LIFECYCLE_CHOICES,
    account_lifecycle_q,
    derive_account_lifecycle,
    lifecycle_label,
    normalize_account_lifecycle,
)
from accounts.models import TierOverride
from community.models import STATUS_BOOKED, BookedCall
from community.slack_config import get_slack_plan_sprints_user_token
from crm.models import (
    STATUS_CHOICES,
    AppliedProgressChange,
    CRMRecord,
    IngestedProgressEvent,
    SlackChannelIngest,
)
from crm.services.activity_context import (
    ACTIVITY_CATEGORIES,
    ACTIVITY_CATEGORY_ALL,
    ACTIVITY_CATEGORY_LABELS,
    DEFAULT_ACTIVITY_LIMIT,
    build_activity_context,
    normalize_activity_category,
)
from crm.services.slack_updates import unmatched_threads
from crm.tasks.apply_plan_sprint_progress import reverse_change, reverse_event
from plans.models import InterviewNote, Plan
from questionnaires.models import Persona
from questionnaires.onboarding import (
    flatten_response_answers,
    get_onboarding_response,
)
from studio.decorators import staff_required

User = get_user_model()

CRM_LIST_PAGE_SIZE = 50
FILTER_ALL = 'all'
FILTER_ACTIVE = 'active'
FILTER_ARCHIVED = 'archived'
VALID_LIST_FILTERS = {FILTER_ALL, FILTER_ACTIVE, FILTER_ARCHIVED}
DEFAULT_LIST_FILTER = FILTER_ACTIVE


def _normalize_list_filter(raw):
    if raw in VALID_LIST_FILTERS:
        return raw
    return DEFAULT_LIST_FILTER


def _normalize_account_lifecycle_filter(raw):
    return normalize_account_lifecycle(raw)


def _active_tier_info(user):
    """Return effective tier display fields for CRM list/detail templates."""
    base_name = user.tier.name if user.tier_id else 'Free'
    base_slug = user.tier.slug if user.tier_id else 'free'
    base_info = {'name': base_name, 'slug': base_slug, 'source': 'stripe' if user.stripe_customer_id else 'default'}
    override = (
        TierOverride.objects
        .filter(
            user_id=user.pk,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .select_related('override_tier')
        .order_by('-created_at')
        .first()
    )
    if override is None:
        return base_info
    # Local base comparison is internal to this effective display helper: only
    # show the override source when it exceeds the stored subscription tier.
    base_level = user.tier.level if user.tier_id else 0
    if override.override_tier.level <= base_level:
        return base_info
    return {
        'name': override.override_tier.name,
        'slug': override.override_tier.slug,
        'source': 'override',
    }


@staff_required
@require_POST
def crm_track(request, user_id):
    """Create-or-redirect-to a CRM record for the given user (idempotent).

    POST-only. If a record already exists for ``user_id`` the response
    redirects to that record without creating a duplicate. Otherwise a
    new record is created with ``created_by=request.user`` and the
    response redirects to the new record.
    """
    user = get_object_or_404(User, pk=user_id)
    existing = CRMRecord.objects.filter(user=user).first()
    if existing is not None:
        return redirect('studio_crm_detail', crm_id=existing.pk)
    record = CRMRecord.objects.create(user=user, created_by=request.user)
    messages.success(request, 'Now tracking this user in the CRM.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
def crm_list(request):
    """List all CRM records.

    Filter chips: ``all`` / ``active`` (default) / ``archived``.
    Search: case-insensitive substring on ``user.email`` and ``persona``.
    Pagination: 50 per page (mirrors the user list).
    """
    active_filter = _normalize_list_filter(request.GET.get('filter', ''))
    account_lifecycle_filter = _normalize_account_lifecycle_filter(
        request.GET.get('account_lifecycle', ''),
    )
    search = (request.GET.get('q', '') or '').strip()

    records_qs = (
        CRMRecord.objects
        .select_related('user', 'user__tier', 'user__attribution')
        .annotate(
            plans_count=Count('user__plans', distinct=True),
            notes_count=Count('user__interview_notes', distinct=True),
        )
        .order_by('-updated_at')
    )

    if active_filter == FILTER_ACTIVE:
        records_qs = records_qs.filter(status='active')
    elif active_filter == FILTER_ARCHIVED:
        records_qs = records_qs.filter(status='archived')

    if account_lifecycle_filter:
        records_qs = records_qs.filter(
            account_lifecycle_q(
                account_lifecycle_filter,
                user_prefix='user__',
            )
        )

    if search:
        records_qs = records_qs.filter(
            Q(user__email__icontains=search) | Q(persona__icontains=search)
        )

    paginator = Paginator(records_qs, CRM_LIST_PAGE_SIZE)
    try:
        page_number = int(request.GET.get('page', '1'))
    except (TypeError, ValueError):
        page_number = 1
    if page_number < 1:
        page_number = 1
    if page_number > paginator.num_pages:
        page_number = paginator.num_pages
    page = paginator.page(page_number)

    rows = []
    for record in page.object_list:
        tier_info = _active_tier_info(record.user)
        account_lifecycle = derive_account_lifecycle(record.user)
        rows.append({
            'pk': record.pk,
            'user_pk': record.user.pk,
            'email': record.user.email,
            'full_name': f'{record.user.first_name} {record.user.last_name}'.strip(),
            'tier_name': tier_info['name'],
            'tier_slug': tier_info['slug'],
            'tier_source': tier_info['source'],
            'account_lifecycle': account_lifecycle,
            'account_lifecycle_label': lifecycle_label(account_lifecycle),
            'persona': record.persona,
            'status': record.status,
            'status_display': record.get_status_display(),
            'updated_at': record.updated_at,
            'plans_count': record.plans_count,
            'notes_count': record.notes_count,
        })

    counts = {
        'all': CRMRecord.objects.count(),
        'active': CRMRecord.objects.filter(status='active').count(),
        'archived': CRMRecord.objects.filter(status='archived').count(),
    }

    return render(request, 'studio/crm/list.html', {
        'rows': rows,
        'paginator': paginator,
        'page': page,
        'active_filter': active_filter,
        'account_lifecycle_filter': account_lifecycle_filter,
        'search': search,
        'counts': counts,
        'filter_all': FILTER_ALL,
        'filter_active': FILTER_ACTIVE,
        'filter_archived': FILTER_ARCHIVED,
        'account_lifecycle_choices': ACCOUNT_LIFECYCLE_CHOICES,
    })


def _get_record(crm_id):
    return get_object_or_404(
        CRMRecord.objects.select_related('user', 'user__tier', 'user__attribution'),
        pk=crm_id,
    )


def _activity_filter_chips(request, active_category):
    """Return CRM activity filter chip data while preserving query params."""
    categories = (ACTIVITY_CATEGORY_ALL, *ACTIVITY_CATEGORIES)
    chips = []
    for category in categories:
        params = request.GET.copy()
        params['activity_category'] = category
        query = params.urlencode()
        chips.append({
            'category': category,
            'label': ACTIVITY_CATEGORY_LABELS[category],
            'url': f'?{query}' if query else request.path,
            'active': category == active_category,
        })
    return chips


def _record_detail_context(record, request):
    """Build the shared context for the CRM detail page."""
    tier_info = _active_tier_info(record.user)
    account_lifecycle = derive_account_lifecycle(record.user)
    activity_category = normalize_activity_category(
        request.GET.get('activity_category', ''),
    )
    activity_context = build_activity_context(
        record.user,
        limit=DEFAULT_ACTIVITY_LIMIT,
        category=activity_category,
        include_category_counts=True,
    )
    note_queryset = (
        InterviewNote.objects
        .filter(member=record.user)
        .select_related('plan__sprint', 'created_by')
        .order_by('-created_at')
    )
    member_plans = list(
        Plan.objects
        .filter(member=record.user)
        .select_related('sprint')
        .order_by('-sprint__start_date', '-created_at')
    )

    # Onboarding answers (issue #871). Reuse the shared answer-flattening
    # helper that backs the read-only ``/api/onboarding/responses/<email>``
    # API so the CRM page and the API never diverge on answer-type handling.
    onboarding_response = get_onboarding_response(record.user)
    onboarding_answers = (
        flatten_response_answers(onboarding_response)
        if onboarding_response is not None
        else []
    )
    onboarding_submitted = (
        onboarding_response is not None
        and onboarding_response.status == 'submitted'
    )

    # Booked calls captured from Calendly (issue #884). Show the member's
    # active (not-canceled) bookings, soonest first, with the host.
    booked_calls = list(
        BookedCall.objects
        .filter(member=record.user, status=STATUS_BOOKED)
        .select_related('host')
        .order_by('scheduled_at', 'created_at')
    )

    return {
        'record': record,
        'detail_user': record.user,
        'tier_name': tier_info['name'],
        'tier_slug': tier_info['slug'],
        'tier_source': tier_info['source'],
        'account_lifecycle': account_lifecycle,
        'account_lifecycle_label': lifecycle_label(account_lifecycle),
        'member_plans': member_plans,
        'booked_calls': booked_calls,
        'activities': activity_context['activities'],
        'activity_total': activity_context['activity_total'],
        'activity_limit': activity_context['activity_limit'],
        'activity_has_more': activity_context['activity_has_more'],
        'first_payment_at': activity_context['first_payment_at'],
        'active_activity_category': activity_category,
        'activity_filter_chips': _activity_filter_chips(
            request, activity_category,
        ),
        'activity_category_counts': activity_context['activity_category_counts'],
        'onboarding_response': onboarding_response,
        'onboarding_answers': onboarding_answers,
        'onboarding_submitted': onboarding_submitted,
        'internal_notes': note_queryset.internal(),
        'external_notes': note_queryset.external(),
        # ``current_plan`` is consumed by the reused
        # ``_member_notes.html`` partial when the staff "Add member
        # note" button needs a plan prefill. The CRM record is
        # member-scoped, not plan-scoped, so we pass ``None``.
        'current_plan': None,
        'django_admin_url': (
            f'/admin/accounts/user/{record.user.pk}/change/'
        ),
        'record_status_choices': STATUS_CHOICES,
        # Structured persona dropdown (issue #802). #801 added the FK but
        # deferred the Studio control; staff pick an active Persona here.
        # Blank leaves the free-text ``persona`` as the source of truth.
        'persona_choices': list(
            Persona.objects.filter(is_active=True).order_by('order', 'name')
        ),
    }


@staff_required
def crm_detail(request, crm_id):
    """Render the CRM record detail page (header + 5 sections)."""
    record = _get_record(crm_id)
    return render(
        request,
        'studio/crm/detail.html',
        _record_detail_context(record, request),
    )


@staff_required
@require_POST
def crm_edit(request, crm_id):
    """Save the snapshot card fields (persona, summary, next_steps)."""
    record = _get_record(crm_id)
    record.persona = (request.POST.get('persona') or '').strip()[:120]
    record.summary = (request.POST.get('summary') or '').strip()
    record.next_steps = (request.POST.get('next_steps') or '').strip()
    # Structured persona (issue #802): blank clears the FK.
    raw_persona_ref = (request.POST.get('persona_ref') or '').strip()
    if raw_persona_ref.isdigit():
        record.persona_ref = Persona.objects.filter(
            pk=int(raw_persona_ref), is_active=True,
        ).first()
    else:
        record.persona_ref = None
    record.save()
    messages.success(request, 'CRM record updated.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
def crm_slack_ingest_review(request):
    """Staff-only review surface for `#plan-sprints` ingest (issue #889).

    Lists unmatched threads (root author not matched to a member) so
    updates from people we could not auto-match are not lost, plus the
    most recent ingest runs so staff can confirm the daily job ran.
    """
    return render(request, 'studio/crm/slack_ingest.html', {
        'unmatched_threads': unmatched_threads(),
        'recent_runs': SlackChannelIngest.objects.all()[:10],
        'reply_token_configured': bool(get_slack_plan_sprints_user_token()),
    })


def _safe_back(request):
    """Redirect target after an undo: the referring page, else CRM list.

    Only a same-host referer is honoured so the redirect cannot be aimed
    off-site by a crafted Referer header.
    """
    referer = request.META.get('HTTP_REFERER', '')
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect('studio_crm_list')


@staff_required
@require_POST
def crm_slack_progress_undo(request, event_id):
    """Undo a whole auto-applied progress event (issue #890, Phase 2).

    Restores every recorded change's plan item to its ``previous_done_at``
    and deletes the event. Items the event never recorded — including manual
    completions — are untouched.
    """
    event = get_object_or_404(IngestedProgressEvent, pk=event_id)
    reverse_event(event)
    messages.success(
        request, 'Reverted all auto-applied changes from this Slack update.',
    )
    return _safe_back(request)


@staff_required
@require_POST
def crm_slack_progress_change_undo(request, change_id):
    """Undo a single auto-applied change (issue #890, Phase 2).

    Restores only that change's plan item and deletes the change row,
    leaving the rest of the event's changes applied and the event in place.
    """
    change = get_object_or_404(AppliedProgressChange, pk=change_id)
    reverse_change(change)
    messages.success(request, 'Reverted the auto-applied change.')
    return _safe_back(request)


@staff_required
@require_POST
def crm_archive(request, crm_id):
    """Set the record's status to ``archived``."""
    record = _get_record(crm_id)
    record.status = 'archived'
    record.save(update_fields=['status', 'updated_at'])
    messages.info(request, 'CRM record archived.')
    return redirect('studio_crm_detail', crm_id=record.pk)


@staff_required
@require_POST
def crm_reactivate(request, crm_id):
    """Set the record's status back to ``active``."""
    record = _get_record(crm_id)
    record.status = 'active'
    record.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'CRM record reactivated.')
    return redirect('studio_crm_detail', crm_id=record.pk)
