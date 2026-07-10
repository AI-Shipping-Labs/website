from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from content.models import MarketingPage
from content.models.marketing_page import STATUS_PUBLISHED


def marketing_page_preview(request, preview_token):
    """Private draft preview by high-entropy token."""
    page = get_object_or_404(MarketingPage, preview_token=preview_token)
    if page.status == STATUS_PUBLISHED:
        return redirect(page.get_absolute_url())

    response = render(
        request,
        'content/marketing_page.html',
        {
            'marketing_page': page,
            'draft_preview': True,
        },
    )
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response


def marketing_page_fallback(request, path=''):
    """Final project-level fallback for published standalone pages."""
    public_path = request.path or ''
    if not public_path.startswith('/'):
        public_path = f'/{public_path}'
    try:
        page = MarketingPage.objects.get(
            public_path=public_path,
            status=STATUS_PUBLISHED,
        )
    except MarketingPage.DoesNotExist as exc:
        raise Http404('Marketing page not found') from exc

    return render(
        request,
        'content/marketing_page.html',
        {
            'marketing_page': page,
            'draft_preview': False,
        },
    )
