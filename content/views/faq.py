from django.shortcuts import render

from content.views.home import FAQ_ITEMS


def faq(request):
    return render(request, 'content/faq.html', {'faq_items': FAQ_ITEMS})
