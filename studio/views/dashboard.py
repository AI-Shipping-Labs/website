"""Studio dashboard view."""

from django.shortcuts import render
from django.utils import timezone

from studio.decorators import staff_required


@staff_required
def dashboard(request):
    """Studio dashboard with quick stats."""
    from content.models import Course, Article, Download, Project, Recording
    from events.models import Event
    from email_app.models import NewsletterSubscriber, EmailCampaign

    stats = {
        'total_courses': Course.objects.count(),
        'published_courses': Course.objects.filter(status='published').count(),
        'total_articles': Article.objects.count(),
        'published_articles': Article.objects.filter(published=True).count(),
        'active_subscribers': NewsletterSubscriber.objects.filter(is_active=True).count(),
        'total_subscribers': NewsletterSubscriber.objects.count(),
        'upcoming_events': Event.objects.filter(
            status='upcoming',
            start_datetime__gte=timezone.now(),
        ).count(),
        'total_events': Event.objects.count(),
        'total_recordings': Recording.objects.count(),
        'total_downloads': Download.objects.count(),
        'pending_projects': Project.objects.filter(status='pending_review').count(),
        'total_campaigns': EmailCampaign.objects.count(),
    }

    # Recent items for quick access
    recent_articles = Article.objects.order_by('-updated_at')[:5]
    recent_events = Event.objects.order_by('-created_at')[:5]
    pending_projects = Project.objects.filter(status='pending_review').order_by('-created_at')[:5]

    return render(request, 'studio/dashboard.html', {
        'stats': stats,
        'recent_articles': recent_articles,
        'recent_events': recent_events,
        'pending_projects': pending_projects,
    })
