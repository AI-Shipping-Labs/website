"""Hard-deprecated local Stripe checkout and subscription mutation views."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from integrations.config import get_config


def _checkout_disabled_response():
    """Return a 410 response for obsolete local Stripe mutation APIs."""
    return JsonResponse({
        'error': 'Local Stripe checkout is deprecated. Use payment links or the Customer Portal.',
        'portal_url': get_config('STRIPE_CUSTOMER_PORTAL_URL', ''),
    }, status=410)


@login_required
@require_POST
def create_checkout(request):
    """Deprecated: paid signup now goes through Stripe Payment Links."""
    return _checkout_disabled_response()


@login_required
@require_POST
def upgrade(request):
    """Deprecated: subscription changes now go through Customer Portal."""
    return _checkout_disabled_response()


@login_required
@require_POST
def downgrade(request):
    """Deprecated: subscription changes now go through Customer Portal."""
    return _checkout_disabled_response()


@login_required
@require_POST
def cancel(request):
    """Deprecated: cancellations now go through Customer Portal."""
    return _checkout_disabled_response()
