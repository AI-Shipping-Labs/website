"""Admin for EmailCampaign with custom actions for test send and campaign send."""

import json
import logging

from django.contrib import admin, messages
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html

from email_app.models import EmailCampaign

logger = logging.getLogger(__name__)


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = [
        'subject',
        'status',
        'target_level_display',
        'sent_count',
        'sent_at',
        'created_at',
    ]
    list_filter = ['status', 'target_min_level']
    search_fields = ['subject']
    ordering = ['-created_at']
    readonly_fields = ['status', 'sent_at', 'sent_count', 'created_at', 'recipient_count_display']
    fields = [
        'subject',
        'body',
        'target_min_level',
        'recipient_count_display',
        'status',
        'sent_count',
        'sent_at',
        'created_at',
    ]

    def get_readonly_fields(self, request, obj=None):
        """Make all fields readonly for sent/sending campaigns."""
        if obj and obj.status in ('sending', 'sent'):
            return [
                'subject', 'body', 'target_min_level',
                'status', 'sent_at', 'sent_count', 'created_at',
                'recipient_count_display',
            ]
        return self.readonly_fields

    def target_level_display(self, obj):
        """Display the target audience label."""
        level_map = dict(EmailCampaign.TARGET_LEVEL_CHOICES)
        return level_map.get(obj.target_min_level, str(obj.target_min_level))
    target_level_display.short_description = 'Target Audience'

    def recipient_count_display(self, obj):
        """Display estimated recipient count."""
        if obj and obj.pk:
            count = obj.get_recipient_count()
            return f'{count} eligible recipients'
        return 'Save the campaign first to see recipient count'
    recipient_count_display.short_description = 'Estimated Recipients'

    def get_urls(self):
        custom_urls = [
            path(
                '<int:campaign_id>/send-test/',
                self.admin_site.admin_view(self.send_test_view),
                name='email_app_emailcampaign_send_test',
            ),
            path(
                '<int:campaign_id>/send-campaign/',
                self.admin_site.admin_view(self.send_campaign_view),
                name='email_app_emailcampaign_send_campaign',
            ),
            path(
                '<int:campaign_id>/recipient-count/',
                self.admin_site.admin_view(self.recipient_count_view),
                name='email_app_emailcampaign_recipient_count',
            ),
        ]
        return custom_urls + super().get_urls()

    def send_test_view(self, request, campaign_id):
        """Send a test email to the logged-in admin user."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            campaign = EmailCampaign.objects.get(pk=campaign_id)
        except EmailCampaign.DoesNotExist:
            return JsonResponse({'error': 'Campaign not found'}, status=404)

        try:
            import markdown as md
            from django.template.loader import render_to_string
            from email_app.services.email_service import EmailService, EmailServiceError

            service = EmailService()
            body_html = md.markdown(campaign.body, extensions=['extra'])
            unsubscribe_url = service._build_unsubscribe_url(request.user)

            full_html = render_to_string('email_app/base_email.html', {
                'subject': f'[TEST] {campaign.subject}',
                'body_html': body_html,
                'unsubscribe_url': unsubscribe_url,
            })

            ses_message_id = service._send_ses(
                request.user.email,
                f'[TEST] {campaign.subject}',
                full_html,
            )

            return JsonResponse({
                'status': 'ok',
                'message': f'Test email sent to {request.user.email}',
                'ses_message_id': ses_message_id,
            })

        except EmailServiceError as e:
            logger.exception("Failed to send test email for campaign %s", campaign_id)
            return JsonResponse({
                'status': 'error',
                'message': f'Failed to send test email: {e}',
            }, status=500)

    def send_campaign_view(self, request, campaign_id):
        """Enqueue the campaign for background sending."""
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            campaign = EmailCampaign.objects.get(pk=campaign_id)
        except EmailCampaign.DoesNotExist:
            return JsonResponse({'error': 'Campaign not found'}, status=404)

        if campaign.status != 'draft':
            return JsonResponse({
                'status': 'error',
                'message': f'Campaign is already {campaign.status}.',
            }, status=400)

        # Enqueue the background job
        from jobs.tasks import async_task
        task_id = async_task(
            'email_app.tasks.send_campaign.send_campaign',
            campaign_id=campaign.pk,
        )

        logger.info(
            "Enqueued campaign %s for sending (task_id=%s)",
            campaign_id, task_id,
        )

        return JsonResponse({
            'status': 'ok',
            'message': 'Campaign queued for sending.',
            'task_id': str(task_id) if task_id else None,
        })

    def recipient_count_view(self, request, campaign_id):
        """Return the estimated recipient count for a campaign."""
        try:
            campaign = EmailCampaign.objects.get(pk=campaign_id)
        except EmailCampaign.DoesNotExist:
            return JsonResponse({'error': 'Campaign not found'}, status=404)

        count = campaign.get_recipient_count()
        return JsonResponse({
            'count': count,
            'target_min_level': campaign.target_min_level,
        })

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Add campaign action buttons to the change form."""
        extra_context = extra_context or {}
        try:
            campaign = EmailCampaign.objects.get(pk=object_id)
            extra_context['campaign'] = campaign
            extra_context['is_draft'] = campaign.status == 'draft'
            extra_context['recipient_count'] = campaign.get_recipient_count()
            extra_context['send_test_url'] = reverse(
                'admin:email_app_emailcampaign_send_test',
                args=[object_id],
            )
            extra_context['send_campaign_url'] = reverse(
                'admin:email_app_emailcampaign_send_campaign',
                args=[object_id],
            )
        except EmailCampaign.DoesNotExist:
            pass
        return super().change_view(
            request, object_id, form_url, extra_context,
        )

    change_form_template = 'admin/email_app/emailcampaign/change_form.html'
