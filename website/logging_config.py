"""Safe request context fields for Django and application log records."""

import logging

from community_base.kernel.context import (
    current_correlation_id,
    current_request_id,
    is_valid_context_id,
)


class RequestContextFilter(logging.Filter):
    """Add validated request and correlation IDs without serializing requests."""

    def filter(self, record):
        request = getattr(record, 'request', None)
        request_id = getattr(request, 'request_id', None)
        correlation_id = getattr(request, 'correlation_id', None)
        if not is_valid_context_id(request_id):
            request_id = current_request_id()
        if not is_valid_context_id(correlation_id):
            correlation_id = current_correlation_id()
        record.request_id = request_id if is_valid_context_id(request_id) else '-'
        record.correlation_id = (
            correlation_id if is_valid_context_id(correlation_id) else '-'
        )
        return True
