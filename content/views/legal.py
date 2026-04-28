"""Legal pages: Terms of Service, Privacy Policy, and Impressum.

These pages are publicly reachable without authentication. Operator info
(legal entity, address, VAT, contact) is baked into the templates per
issue #368.
"""

from django.shortcuts import render

LAST_UPDATED = 'April 27, 2026'
LAST_UPDATED_DE = '27. April 2026'


def terms(request):
    """Terms of Service (English)."""
    return render(request, 'legal/terms.html', {'last_updated': LAST_UPDATED})


def privacy(request):
    """Privacy Policy (English)."""
    return render(request, 'legal/privacy.html', {'last_updated': LAST_UPDATED})


def impressum(request):
    """Impressum (German legal convention; content in German)."""
    return render(request, 'legal/impressum.html', {'last_updated': LAST_UPDATED_DE})
