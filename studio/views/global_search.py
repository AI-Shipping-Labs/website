"""Staff-only global search for Studio navigation.

This endpoint intentionally serves the in-Studio session UI only. Token
authenticated production API global search is out of scope for issue #1191.
"""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from accounts.utils.display import display_name
from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event
from studio.decorators import staff_required

User = get_user_model()

GROUP_LIMIT = 5
CANDIDATE_LIMIT = 50


def _empty_groups():
    return {
        'users': [],
        'content': [],
        'events': [],
        'campaigns': [],
    }


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


def _user_results(query):
    predicate = (
        Q(email__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
    )
    if query.isdigit():
        predicate |= Q(pk=int(query))

    candidates = list(
        User.objects
        .filter(predicate)
        .select_related('tier')
        .order_by('email')[:CANDIDATE_LIMIT]
    )
    query_lower = query.lower()
    candidates.sort(key=lambda user: (
        _rank_text(
            query_lower,
            user.email,
            user.first_name,
            user.last_name,
            display_name(user),
            str(user.pk),
        ),
        user.email.lower(),
    ))

    results = []
    for user in candidates[:GROUP_LIMIT]:
        tier_name = user.tier.name if user.tier_id else 'Free'
        verified = 'verified' if user.email_verified else 'unverified'
        bounce = user.get_bounce_state_display()
        metadata = f'{tier_name} · {verified} · {bounce}'
        results.append({
            'id': user.pk,
            'group': 'users',
            'type': 'User',
            'label': display_name(user),
            'summary': user.email,
            'metadata': metadata,
            'url': reverse('studio_user_detail', kwargs={'user_id': user.pk}),
        })
    return results


def _content_item(obj, content_type, url_name, url_kwarg, status):
    return {
        'id': obj.pk,
        'group': 'content',
        'type': content_type,
        'label': obj.title,
        'summary': obj.slug,
        'metadata': status,
        'url': reverse(url_name, kwargs={url_kwarg: obj.pk}),
    }


def _content_results(query):
    sources = [
        (
            Article.objects.filter(Q(title__icontains=query) | Q(slug__icontains=query)),
            'Article',
            'studio_article_edit',
            'article_id',
            lambda obj: 'Published' if obj.published else 'Draft',
        ),
        (
            Course.objects.filter(Q(title__icontains=query) | Q(slug__icontains=query)),
            'Course',
            'studio_course_edit',
            'course_id',
            lambda obj: _display_status(obj),
        ),
        (
            Workshop.objects.filter(Q(title__icontains=query) | Q(slug__icontains=query)),
            'Workshop',
            'studio_workshop_detail',
            'workshop_id',
            lambda obj: 'Workshop',
        ),
        (
            Event.objects
            .filter(Q(title__icontains=query) | Q(slug__icontains=query))
            .exclude(recording_url=''),
            'Recording',
            'studio_recording_edit',
            'recording_id',
            lambda obj: _display_status(obj),
        ),
        (
            Download.objects.filter(Q(title__icontains=query) | Q(slug__icontains=query)),
            'Download',
            'studio_download_edit',
            'download_id',
            lambda obj: 'Published' if obj.published else 'Draft',
        ),
        (
            Project.objects.filter(Q(title__icontains=query) | Q(slug__icontains=query)),
            'Project',
            'studio_project_review',
            'project_id',
            lambda obj: _display_status(obj),
        ),
    ]

    results = []
    for queryset, content_type, url_name, url_kwarg, status_func in sources:
        for obj in queryset.order_by('title')[:CANDIDATE_LIMIT]:
            results.append((
                _rank_text(query, obj.title, obj.slug),
                obj.title.lower(),
                _content_item(
                    obj,
                    content_type,
                    url_name,
                    url_kwarg,
                    status_func(obj),
                ),
            ))
    results.sort(key=lambda item: (item[0], item[1], item[2]['type']))
    return [item[2] for item in results[:GROUP_LIMIT]]


def _event_results(query):
    candidates = list(
        Event.objects
        .filter(Q(title__icontains=query) | Q(slug__icontains=query))
        .order_by('-start_datetime')[:CANDIDATE_LIMIT]
    )
    candidates.sort(key=lambda event: (
        _rank_text(query, event.title, event.slug),
        event.start_datetime,
        event.title.lower(),
    ))
    return [
        {
            'id': event.pk,
            'group': 'events',
            'type': 'Event',
            'label': event.title,
            'summary': event.slug,
            'metadata': f'{_display_status(event)} · {event.start_datetime:%Y-%m-%d}',
            'url': reverse('studio_event_edit', kwargs={'event_id': event.pk}),
        }
        for event in candidates[:GROUP_LIMIT]
    ]


def _campaign_results(query):
    candidates = list(
        EmailCampaign.objects
        .filter(subject__icontains=query)
        .order_by('-created_at')[:CANDIDATE_LIMIT]
    )
    candidates.sort(key=lambda campaign: (
        _rank_text(query, campaign.subject),
        campaign.subject.lower(),
    ))
    results = []
    for campaign in candidates[:GROUP_LIMIT]:
        metadata = _display_status(campaign)
        if campaign.sent_at:
            metadata = f'{metadata} · sent {campaign.sent_at:%Y-%m-%d}'
        results.append({
            'id': campaign.pk,
            'group': 'campaigns',
            'type': 'Campaign',
            'label': campaign.subject,
            'summary': '',
            'metadata': metadata,
            'url': reverse('studio_campaign_detail', kwargs={'campaign_id': campaign.pk}),
        })
    return results


@staff_required
def global_search(request):
    """Return compact grouped Studio search results for staff operators."""
    query = request.GET.get('q', '').strip()
    groups = _empty_groups()
    if len(query) < 2:
        return JsonResponse({'query': query, 'results': groups})

    groups['users'] = _user_results(query)
    groups['content'] = _content_results(query)
    groups['events'] = _event_results(query)
    groups['campaigns'] = _campaign_results(query)
    return JsonResponse({'query': query, 'results': groups})
