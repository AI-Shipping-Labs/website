"""Opaque request/correlation IDs for tracing and the access log."""

from community_base.kernel.context import (
    bind_context,
    external_context_id_or_new,
    is_safe_external_context_id,
    reset_context,
)


class RequestIdMiddleware:
    """Attach opaque request/correlation IDs to every response.

    Mirrors the website canonical policy: accept a bounded external
    ``X-Request-ID`` / ``X-Correlation-ID`` or mint an opaque replacement,
    so the gunicorn access log can join a line to a trace without ever
    logging a client address, query string, or header value.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get('X-Request-ID', '')
        request_id = external_context_id_or_new(supplied)
        supplied_correlation = request.headers.get('X-Correlation-ID', '')
        if is_safe_external_context_id(supplied_correlation):
            correlation_id = supplied_correlation
        else:
            correlation_id = request_id
        request.request_id = request_id
        request.correlation_id = correlation_id
        tokens = bind_context(
            request_id=request_id,
            correlation_id=correlation_id,
        )
        try:
            response = self.get_response(request)
            response['X-Request-ID'] = request_id
            response['X-Correlation-ID'] = correlation_id
            return response
        finally:
            reset_context(tokens)
