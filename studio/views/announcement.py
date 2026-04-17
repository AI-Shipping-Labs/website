"""Studio view for managing the site-wide announcement banner."""

from django.contrib import messages
from django.shortcuts import redirect, render

from integrations.middleware import clear_announcement_banner_cache
from integrations.models import AnnouncementBanner
from studio.decorators import staff_required


@staff_required
def announcement_banner_edit(request):
    """Edit the singleton announcement banner.

    GET creates the singleton row on first access and renders the form
    with the current values. POST saves the form and bumps ``version`` if
    the message or link URL changed, then redirects back to this view.
    """
    banner = AnnouncementBanner.get_singleton()

    if request.method == 'POST':
        message = request.POST.get('message', '').strip()
        link_url = request.POST.get('link_url', '').strip()
        link_label = request.POST.get('link_label', '').strip() or 'Read more'
        is_enabled = request.POST.get('is_enabled') == 'on'
        is_dismissible = request.POST.get('is_dismissible') == 'on'

        if not message:
            messages.error(request, 'Banner message is required.')
            return render(request, 'studio/announcement/edit.html', {
                'banner': banner,
                # Pass back the submitted values so the form is sticky.
                'preview': {
                    'message': message,
                    'link_url': link_url,
                    'link_label': link_label,
                    'is_enabled': is_enabled,
                    'is_dismissible': is_dismissible,
                    'version': banner.version,
                },
            })

        # Bump version when the message or link URL changes so previously
        # dismissed users see the banner again on their next page load.
        if message != banner.message or link_url != banner.link_url:
            banner.version = banner.version + 1

        banner.message = message
        banner.link_url = link_url
        banner.link_label = link_label
        banner.is_enabled = is_enabled
        banner.is_dismissible = is_dismissible
        banner.save()
        clear_announcement_banner_cache()

        messages.success(request, 'Announcement banner saved.')
        return redirect('studio_announcement_banner')

    return render(request, 'studio/announcement/edit.html', {
        'banner': banner,
        'preview': {
            'message': banner.message,
            'link_url': banner.link_url,
            'link_label': banner.link_label or 'Read more',
            'is_enabled': banner.is_enabled,
            'is_dismissible': banner.is_dismissible,
            'version': banner.version,
        },
    })
