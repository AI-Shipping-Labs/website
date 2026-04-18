from django.shortcuts import render

from content.views.home import FAQ_ITEMS


def faq(request):
    """Standalone FAQ page.

    Renders the same FAQ items shown on the marketing homepage so logged-in
    users (whose `/` lands on the dashboard) still have a destination for
    footer / header FAQ links.
    """
    return render(request, "content/faq.html", {"faq_items": FAQ_ITEMS})
