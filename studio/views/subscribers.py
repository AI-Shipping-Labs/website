"""Studio views for subscriber management."""

import csv

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import render

from email_app.models import NewsletterSubscriber
from studio.decorators import staff_required

User = get_user_model()


@staff_required
def subscriber_list(request):
    """List subscribers with status filters."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    subscribers = NewsletterSubscriber.objects.all()
    if status_filter == 'active':
        subscribers = subscribers.filter(is_active=True)
    elif status_filter == 'inactive':
        subscribers = subscribers.filter(is_active=False)
    if search:
        subscribers = subscribers.filter(email__icontains=search)

    # Build a mapping of subscriber emails to User IDs for the "Login as" button
    subscriber_emails = list(subscribers.values_list('email', flat=True))
    email_to_user_id = dict(
        User.objects.filter(email__in=subscriber_emails).values_list('email', 'pk')
    )

    return render(request, 'studio/subscribers/list.html', {
        'subscribers': subscribers,
        'email_to_user_id': email_to_user_id,
        'status_filter': status_filter,
        'search': search,
        'total_count': NewsletterSubscriber.objects.count(),
        'active_count': NewsletterSubscriber.objects.filter(is_active=True).count(),
        'inactive_count': NewsletterSubscriber.objects.filter(is_active=False).count(),
    })


@staff_required
def subscriber_export_csv(request):
    """Export subscribers as CSV."""
    status_filter = request.GET.get('status', '')

    subscribers = NewsletterSubscriber.objects.all()
    if status_filter == 'active':
        subscribers = subscribers.filter(is_active=True)
    elif status_filter == 'inactive':
        subscribers = subscribers.filter(is_active=False)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="subscribers.csv"'

    writer = csv.writer(response)
    writer.writerow(['Email', 'Subscribed At', 'Active'])
    for sub in subscribers:
        writer.writerow([
            sub.email,
            sub.subscribed_at.isoformat() if sub.subscribed_at else '',
            'Yes' if sub.is_active else 'No',
        ])

    return response
