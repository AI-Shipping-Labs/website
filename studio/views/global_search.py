"""Privacy-safe, staff-session global search for Studio navigation."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from accounts.utils.display import display_name
from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from studio.decorators import staff_required

User = get_user_model()

GROUP_LIMIT = 8
CANDIDATE_LIMIT = 50
GROUP_NAMES = (
    'pages', 'users', 'events', 'event_series', 'workshops', 'articles',
    'courses', 'recordings', 'downloads', 'projects', 'campaigns',
)

# Keep labels and route names aligned with the destinations rendered by the
# Studio sidebar. Aliases are deliberately explicit and contain no private
# data. ``superuser`` mirrors the two sidebar-only privilege gates.
NAVIGATION_ITEMS = (
    ('Dashboard', 'studio_dashboard', (), False),
    ('Events', 'studio_event_list', (), False),
    ('Event series', 'studio_event_series_list', (), False),
    ('Event hosts', 'studio_host_list', ('hosts',), False),
    ('Articles', 'studio_article_list', (), False),
    ('Marketing pages', 'studio_marketing_page_list', (), False),
    ('Courses', 'studio_course_list', (), False),
    ('Projects', 'studio_project_list', (), False),
    ('Workshops', 'studio_workshop_list', (), False),
    ('Recordings', 'studio_recording_list', (), False),
    ('Downloads', 'studio_download_list', (), False),
    ('Users', 'studio_user_list', (), False),
    ('Call hosts (scheduling)', 'studio_call_host_list', ('call hosts',), False),
    ('Imports', 'studio_import_batch_list', (), False),
    ('Tier overrides', 'studio_tier_overrides_list', (), False),
    ('Tags', 'studio_tag_list', (), False),
    ('Merge accounts', 'studio_user_merge', (), False),
    ('Payment mismatches', 'studio_payment_mismatch_list', (), False),
    ('New user', 'studio_user_create', (), True),
    ('CRM', 'studio_crm_list', (), False),
    ('AI Assistant', 'studio_assistant', (), False),
    ('Sprints', 'studio_sprint_list', (), False),
    ('Plans', 'studio_plan_list', (), False),
    ('Questionnaires', 'studio_questionnaire_list', (), False),
    ('Personas', 'studio_persona_list', (), False),
    ('Notification log', 'studio_notification_log', (), False),
    ('Campaigns', 'studio_campaign_list', (), False),
    ('Email templates', 'studio_email_template_list', (), False),
    ('Announcement banner', 'studio_announcement_banner', (), False),
    ('UTM campaigns', 'studio_utm_campaign_list', (), False),
    ('UTM analytics', 'studio_utm_dashboard', (), False),
    ('Signup analytics', 'studio_signup_analytics', (), False),
    ('Content sync', 'studio_sync_dashboard', ('sync',), False),
    ('Worker', 'studio_worker', ('worker tasks',), False),
    ('SES events', 'studio_ses_event_list', (), False),
    ('Email log', 'studio_email_log_list', (), False),
    ('Maven events', 'studio_maven_event_list', (), False),
    ('Redirects', 'studio_redirect_list', (), False),
    ('Trigger subscriptions', 'studio_trigger_subscription_list', (), False),
    ('Event widgets', 'studio_trigger_widget_list', ('trigger widgets',), False),
    ('Event emissions', 'studio_trigger_emission_list', ('trigger emissions',), False),
    ('Webhook deliveries', 'studio_trigger_delivery_list', ('trigger deliveries',), False),
    ('Settings', 'studio_settings', ('configuration',), False),
    ('API tokens', 'studio_api_token_list', ('tokens',), True),
    ('API docs', 'api_docs', ('documentation',), False),
)


def _empty_groups():
    return {name: [] for name in GROUP_NAMES}


def _rank_text(query, *values):
    query = query.lower()
    lowered = [(value or '').lower() for value in values]
    if any(value == query for value in lowered):
        return 0
    if any(value.startswith(query) for value in lowered):
        return 1
    return 2


def _display_status(obj, field='status'):
    getter = getattr(obj, f'get_{field}_display', None)
    if getter:
        return getter()
    value = getattr(obj, field, '')
    if isinstance(value, bool):
        return 'Published' if value else 'Draft'
    return str(value).replace('_', ' ').title() if value else ''


def _result(*, obj_id, group, item_type, label, summary, metadata, url):
    return {
        'id': obj_id,
        'group': group,
        'type': item_type,
        'label': label,
        'summary': summary,
        'metadata': metadata,
        'url': url,
    }


def _page_results(query, user):
    ranked = []
    for label, route_name, aliases, superuser_only in NAVIGATION_ITEMS:
        if superuser_only and not user.is_superuser:
            continue
        values = (label, *aliases)
        if not any(query.lower() in value.lower() for value in values):
            continue
        ranked.append((
            _rank_text(query, *values),
            label.lower(),
            _result(
                obj_id=route_name,
                group='pages',
                item_type='Page',
                label=label,
                summary='Studio navigation',
                metadata='',
                url=reverse(route_name),
            ),
        ))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked[:GROUP_LIMIT]]


def _user_results(query):
    predicate = (
        Q(email__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
    )
    if query.isdigit():
        predicate |= Q(pk=int(query))
    candidates = list(
        User.objects.filter(predicate).select_related('tier').order_by('email')[
            :CANDIDATE_LIMIT
        ]
    )
    candidates.sort(key=lambda user: (
        _rank_text(
            query, user.email, user.first_name, user.last_name,
            display_name(user), str(user.pk),
        ),
        user.email.lower(),
    ))
    return [
        _result(
            obj_id=user.pk,
            group='users',
            item_type='User',
            label=display_name(user),
            summary=user.email,
            metadata=(
                f'{user.tier.name if user.tier_id else "Free"} · '
                f'{"verified" if user.email_verified else "unverified"} · '
                f'{user.get_bounce_state_display()}'
            ),
            url=reverse('studio_user_detail', kwargs={'user_id': user.pk}),
        )
        for user in candidates[:GROUP_LIMIT]
    ]


def _model_results(
    query, *, queryset, group, item_type, label_field, slug_field,
    url_name, url_kwarg, metadata,
):
    predicate = Q(**{f'{label_field}__icontains': query})
    if slug_field:
        predicate |= Q(**{f'{slug_field}__icontains': query})
    candidates = list(queryset.filter(predicate).order_by(label_field)[:CANDIDATE_LIMIT])
    candidates.sort(key=lambda obj: (
        _rank_text(
            query,
            getattr(obj, label_field),
            getattr(obj, slug_field) if slug_field else '',
        ),
        getattr(obj, label_field).lower(),
    ))
    return [
        _result(
            obj_id=obj.pk,
            group=group,
            item_type=item_type,
            label=getattr(obj, label_field),
            summary=getattr(obj, slug_field) if slug_field else '',
            metadata=metadata(obj),
            url=reverse(url_name, kwargs={url_kwarg: obj.pk}),
        )
        for obj in candidates[:GROUP_LIMIT]
    ]


def _campaign_results(query):
    return _model_results(
        query,
        queryset=EmailCampaign.objects.all(),
        group='campaigns',
        item_type='Campaign',
        label_field='subject',
        slug_field=None,
        url_name='studio_campaign_detail',
        url_kwarg='campaign_id',
        metadata=lambda obj: _display_status(obj),
    )


@staff_required
def global_search(request):
    """Return compact, allowlisted, grouped Studio results."""
    query = request.GET.get('q', '').strip()
    groups = _empty_groups()
    if len(query) < 2:
        return JsonResponse({'query': query, 'results': groups})

    groups['pages'] = _page_results(query, request.user)
    groups['users'] = _user_results(query)
    groups['events'] = _model_results(
        query, queryset=Event.objects.all(), group='events', item_type='Event',
        label_field='title', slug_field='slug', url_name='studio_event_edit',
        url_kwarg='event_id', metadata=lambda obj: _display_status(obj),
    )
    groups['event_series'] = _model_results(
        query, queryset=EventSeries.objects.all(), group='event_series',
        item_type='Event series', label_field='name', slug_field='slug',
        url_name='studio_event_series_detail', url_kwarg='series_id',
        metadata=lambda obj: _display_status(obj),
    )
    for group, model, item_type, route, kwarg, status in (
        ('workshops', Workshop, 'Workshop', 'studio_workshop_detail', 'workshop_id', lambda obj: 'Workshop'),
        ('articles', Article, 'Article', 'studio_article_edit', 'article_id', lambda obj: 'Published' if obj.published else 'Draft'),
        ('courses', Course, 'Course', 'studio_course_edit', 'course_id', _display_status),
        ('downloads', Download, 'Download', 'studio_download_edit', 'download_id', lambda obj: 'Published' if obj.published else 'Draft'),
        ('projects', Project, 'Project', 'studio_project_review', 'project_id', _display_status),
    ):
        groups[group] = _model_results(
            query, queryset=model.objects.all(), group=group, item_type=item_type,
            label_field='title', slug_field='slug', url_name=route,
            url_kwarg=kwarg, metadata=status,
        )
    groups['recordings'] = _model_results(
        query, queryset=Event.objects.exclude(recording_url=''),
        group='recordings', item_type='Recording', label_field='title',
        slug_field='slug', url_name='studio_recording_edit',
        url_kwarg='recording_id', metadata=_display_status,
    )
    groups['campaigns'] = _campaign_results(query)
    return JsonResponse({'query': query, 'results': groups})
