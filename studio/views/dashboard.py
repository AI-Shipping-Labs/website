"""Studio dashboard view."""

from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ, Task

from accounts.models import ImportBatch
from content.models import Article, Course, Download, Project
from email_app.models import EmailCampaign
from events.models import Event
from integrations.models import ContentSource
from plans.models import Plan, Sprint
from studio.decorators import staff_required
from studio.worker_health import get_worker_status

User = get_user_model()


def _quick_action(label, url_name, icon, description):
    return {
        'label': label,
        'url': reverse(url_name),
        'icon': icon,
        'description': description,
    }


def _attention_item(label, count, url, icon, tone='warning', description=''):
    return {
        'label': label,
        'count': count,
        'url': url,
        'icon': icon,
        'tone': tone,
        'description': description,
    }


@staff_required
def dashboard(request):
    """Studio dashboard focused on daily operational work."""
    now = timezone.now()
    stats = {
        'total_courses': Course.objects.count(),
        'published_courses': Course.objects.filter(status='published').count(),
        'total_articles': Article.objects.count(),
        'published_articles': Article.objects.filter(published=True).count(),
        'active_subscribers': User.objects.filter(unsubscribed=False).count(),
        'total_subscribers': User.objects.count(),
        'upcoming_events': Event.objects.filter(
            status='upcoming',
            start_datetime__gte=timezone.now(),
        ).count(),
        'total_events': Event.objects.count(),
        'total_recordings': Event.objects.filter(
            recording_url__isnull=False,
        ).exclude(recording_url='').count(),
        'total_downloads': Download.objects.count(),
        'pending_projects': Project.objects.filter(status='pending_review').count(),
        'total_campaigns': EmailCampaign.objects.count(),
        # Sprint plan tile (issue #442). Two flat ``count()`` queries --
        # no per-sprint annotation, no N+1. The ``sprint_list`` view's
        # annotated plan_count is a separate concern and not reused here.
        'active_sprints': Sprint.objects.filter(status='active').count(),
        'total_plans': Plan.objects.count(),
    }

    worker_info = get_worker_status()
    try:
        queue_depth = OrmQ.objects.count()
    except Exception:
        queue_depth = 0
    failed_task_count = Task.objects.filter(success=False).count()

    failed_sync_count = ContentSource.objects.filter(last_sync_status='failed').count()
    failed_import_count = ImportBatch.objects.filter(
        status=ImportBatch.STATUS_FAILED,
    ).count()
    draft_content_count = (
        Course.objects.filter(status='draft').count()
        + Article.objects.filter(published=False).count()
    )

    pending_projects = Project.objects.filter(
        status='pending_review',
    ).order_by('-created_at')[:5]
    upcoming_events = Event.objects.filter(
        status='upcoming',
        start_datetime__gte=now,
    ).order_by('start_datetime')[:5]
    recent_users = User.objects.filter(is_staff=False).order_by('-date_joined')[:5]
    recent_articles = Article.objects.order_by('-updated_at')[:5]
    recent_content = [
        {
            'title': article.title,
            'kind': 'Article',
            'updated_at': article.updated_at,
            'url': reverse('studio_article_edit', kwargs={'article_id': article.pk}),
            'status': 'Published' if article.published else 'Draft',
        }
        for article in Article.objects.order_by('-updated_at')[:3]
    ]
    recent_content += [
        {
            'title': course.title,
            'kind': 'Course',
            'updated_at': course.updated_at,
            'url': reverse('studio_course_edit', kwargs={'course_id': course.pk}),
            'status': course.get_status_display(),
        }
        for course in Course.objects.order_by('-updated_at')[:3]
    ]
    recent_content += [
        {
            'title': event.title,
            'kind': 'Event',
            'updated_at': event.updated_at,
            'url': reverse('studio_event_edit', kwargs={'event_id': event.pk}),
            'status': event.get_status_display(),
        }
        for event in Event.objects.order_by('-updated_at')[:3]
    ]
    recent_content.sort(key=lambda item: item['updated_at'], reverse=True)
    recent_content = recent_content[:5]

    attention_items = []
    if worker_info['expect_worker'] and not worker_info['alive']:
        queued_tasks = f"{queue_depth} queued task{'s' if queue_depth != 1 else ''}"
        attention_items.append(_attention_item(
            'Worker not running',
            'Down',
            reverse('studio_worker'),
            'server-crash',
            tone='critical',
            description=f'Async sync and notification tasks are unavailable; {queued_tasks}.',
        ))
    if stats['pending_projects']:
        attention_items.append(_attention_item(
            'Pending project reviews',
            stats['pending_projects'],
            f"{reverse('studio_project_list')}?status=pending_review",
            'folder-kanban',
            description='Review submitted projects before publishing.',
        ))
    if failed_sync_count:
        attention_items.append(_attention_item(
            'Failed content syncs',
            failed_sync_count,
            reverse('studio_sync_dashboard'),
            'refresh-cw-off',
            tone='critical',
            description='Inspect failed repositories and retry when ready.',
        ))
    if failed_import_count:
        attention_items.append(_attention_item(
            'Failed user imports',
            failed_import_count,
            reverse('studio_import_batch_list'),
            'upload-cloud',
            tone='critical',
            description='Review failed batches before importing again.',
        ))
    if failed_task_count:
        attention_items.append(_attention_item(
            'Failed worker tasks',
            failed_task_count,
            reverse('studio_worker'),
            'circle-alert',
            tone='critical',
            description='Open the worker dashboard to inspect failures.',
        ))
    if draft_content_count:
        attention_items.append(_attention_item(
            'Draft content',
            draft_content_count,
            reverse('studio_article_list'),
            'file-clock',
            tone='neutral',
            description='Draft courses and articles may need review.',
        ))
    if stats['upcoming_events']:
        attention_items.append(_attention_item(
            'Upcoming events',
            stats['upcoming_events'],
            reverse('studio_event_list'),
            'calendar-clock',
            tone='neutral',
            description='Check Zoom links, details, and readiness.',
        ))

    quick_actions = [
        _quick_action('Sync Dashboard', 'studio_sync_dashboard', 'refresh-cw', 'Run or inspect content syncs.'),
        _quick_action('Courses', 'studio_course_list', 'book-open', 'Review course content.'),
        _quick_action('Users', 'studio_user_list', 'users', 'Search subscribers and members.'),
        _quick_action('Project reviews', 'studio_project_list', 'folder-kanban', 'Approve pending projects.'),
        _quick_action('Events', 'studio_event_list', 'calendar', 'Check upcoming sessions.'),
        _quick_action('Worker dashboard', 'studio_worker', 'server', 'Inspect queue and failures.'),
    ]
    quick_actions[3]['url'] += '?status=pending_review'

    summary_metrics = [
        {'label': 'Courses', 'value': stats['total_courses'], 'detail': f"{stats['published_courses']} published"},
        {'label': 'Articles', 'value': stats['published_articles'], 'detail': f"{stats['total_articles']} total"},
        {'label': 'Subscribers', 'value': stats['active_subscribers'], 'detail': f"{stats['total_subscribers']} total"},
        {'label': 'Events', 'value': stats['upcoming_events'], 'detail': f"{stats['total_events']} total"},
        {'label': 'Sprints', 'value': stats['active_sprints'], 'detail': f"{stats['total_plans']} plan{'s' if stats['total_plans'] != 1 else ''}"},
        {'label': 'Downloads', 'value': stats['total_downloads'], 'detail': 'available'},
    ]

    return render(request, 'studio/dashboard.html', {
        'stats': stats,
        'summary_metrics': summary_metrics,
        'attention_items': attention_items,
        'quick_actions': quick_actions,
        'recent_articles': recent_articles,
        'recent_content': recent_content,
        'recent_users': recent_users,
        'upcoming_events': upcoming_events,
        'pending_projects': pending_projects,
        'worker_info': worker_info,
        'worker_queue_depth': queue_depth,
    })
