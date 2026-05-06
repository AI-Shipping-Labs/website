"""Studio dashboard view."""

from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.utils import timezone
from django_q.models import OrmQ

from content.models import Article, Course, Download, Project
from email_app.models import EmailCampaign
from events.models import Event
from plans.models import Plan, Sprint
from studio.decorators import staff_required
from studio.worker_health import get_worker_status

User = get_user_model()


@staff_required
def dashboard(request):
    """Studio dashboard with quick stats."""
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

    # Recent items for quick access
    recent_articles = Article.objects.order_by('-updated_at')[:5]
    recent_events = Event.objects.order_by('-created_at')[:5]
    pending_projects = Project.objects.filter(status='pending_review').order_by('-created_at')[:5]

    worker_info = get_worker_status()
    try:
        queue_depth = OrmQ.objects.count()
    except Exception:
        queue_depth = 0

    return render(request, 'studio/dashboard.html', {
        'stats': stats,
        'recent_articles': recent_articles,
        'recent_events': recent_events,
        'pending_projects': pending_projects,
        'worker_info': worker_info,
        'worker_queue_depth': queue_depth,
    })
