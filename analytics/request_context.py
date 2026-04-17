"""Thread-local helpers used by attribution to reach the active request.

The UTM capture middleware sets the current `HttpRequest` on a thread-local
for the duration of each request. Signal handlers (specifically the
`post_save` handler that creates a `UserAttribution` row when a `User` is
created) read it back so they can access UTM cookies/session without
having them threaded through every code path.

We also expose a small helper to flag "user creation triggered by Stripe
webhook" so the signal can record `signup_path='stripe_checkout'` on the
attribution row even though there is no request bound to the thread (the
webhook spawns user creation from inside an ORM call wrapped in a
threadlocal-less context — this flag is the only signal we have).
"""

import threading

_state = threading.local()


# --- Request --------------------------------------------------------------

def set_current_request(request):
    """Bind a request to the current thread. Called by the middleware."""
    _state.request = request


def get_current_request():
    """Return the request bound to this thread, or None."""
    return getattr(_state, 'request', None)


def clear_current_request():
    """Remove the request binding. Called by the middleware on the way out."""
    if hasattr(_state, 'request'):
        del _state.request


# --- Stripe-user-creation flag -------------------------------------------

def set_stripe_user_creation():
    """Mark the next `User.objects.create_user` call as Stripe-driven.

    Called by `payments.services.handle_checkout_completed` immediately
    before it creates a user from a webhook event. The post_save signal
    reads and clears this flag so it can record the right `signup_path`.
    """
    _state.stripe_user_creation = True


def consume_stripe_user_creation():
    """Return True if the flag was set, and clear it."""
    flag = getattr(_state, 'stripe_user_creation', False)
    if flag:
        _state.stripe_user_creation = False
    return flag


__all__ = [
    'set_current_request',
    'get_current_request',
    'clear_current_request',
    'set_stripe_user_creation',
    'consume_stripe_user_creation',
]
