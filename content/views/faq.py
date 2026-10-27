from django.shortcuts import render

from content.caching import cache_anonymous_page
from content.views.home import FAQ_ITEMS


@cache_anonymous_page()
def faq(request):
    return render(request, 'content/faq.html', {'faq_items': FAQ_ITEMS})
