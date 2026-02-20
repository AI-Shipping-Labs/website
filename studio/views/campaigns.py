"""Studio views for email campaign management."""

from django.shortcuts import render, redirect, get_object_or_404

from email_app.models import EmailCampaign
from studio.decorators import staff_required


@staff_required
def campaign_list(request):
    """List all email campaigns with stats."""
    search = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')

    campaigns = EmailCampaign.objects.all()
    if search:
        campaigns = campaigns.filter(subject__icontains=search)
    if status_filter:
        campaigns = campaigns.filter(status=status_filter)

    return render(request, 'studio/campaigns/list.html', {
        'campaigns': campaigns,
        'search': search,
        'status_filter': status_filter,
    })


@staff_required
def campaign_create(request):
    """Create a new email campaign."""
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        body = request.POST.get('body', '')
        target_min_level = int(request.POST.get('target_min_level', 0))

        campaign = EmailCampaign.objects.create(
            subject=subject,
            body=body,
            target_min_level=target_min_level,
            status='draft',
        )
        return redirect('studio_campaign_list')

    return render(request, 'studio/campaigns/form.html', {
        'campaign': None,
        'form_action': 'create',
    })


@staff_required
def campaign_detail(request, campaign_id):
    """View campaign details with preview and send controls."""
    campaign = get_object_or_404(EmailCampaign, pk=campaign_id)

    recipient_count = campaign.get_recipient_count()

    return render(request, 'studio/campaigns/detail.html', {
        'campaign': campaign,
        'recipient_count': recipient_count,
    })
