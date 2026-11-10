"""Security headers that are not yet safe to enforce site-wide."""

CONTENT_SECURITY_POLICY_REPORT_ONLY = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'self'"
)


class ContentSecurityPolicyReportOnlyMiddleware:
    """Report a conservative CSP baseline without blocking current assets."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '').split(';', 1)[0].strip()
        if content_type == 'text/html':
            response.setdefault(
                'Content-Security-Policy-Report-Only',
                CONTENT_SECURITY_POLICY_REPORT_ONLY,
            )
        return response
