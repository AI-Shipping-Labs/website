"""Central environment, route, and state-aware search-indexing policy.

The development deployment must remain fully functional and crawlable while
never being eligible for indexing.  Classify the deployment from Django's
environment-backed ``SITE_BASE_URL`` setting rather than the runtime database
override: an operator edit must not be able to turn off this safety rail, and
the check must remain available before database-backed middleware runs.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.urls import Resolver404, resolve

DEV_SITE_HOST = 'dev.aishippinglabs.com'
PRODUCTION_SITE_HOST = 'aishippinglabs.com'
NOINDEX_ROBOTS_DIRECTIVE = 'noindex, nofollow, noarchive'
NOINDEX_ROBOTS_META_CONTENT = 'noindex,nofollow,noarchive'

# Backwards-compatible names retained for #1376 callers and tests.
DEV_ROBOTS_DIRECTIVE = NOINDEX_ROBOTS_DIRECTIVE
DEV_ROBOTS_META_CONTENT = NOINDEX_ROBOTS_META_CONTENT

_PRIVATE_PATH_FAMILIES = (
    '/accounts',
    '/account',
    '/onboarding',
    '/studio',
    '/admin',
)

_PRIVATE_ROUTE_NAMES = frozenset({
    # Personalized/member pages.
    'notification_list',
    'request_a_call',
    'poll_list',
    'poll_detail',
    'member_api_docs',
    # Course participation workspaces. Public course/module/unit pages are
    # intentionally absent even when their URL contains numeric segments.
    'project_submit',
    'peer_review_dashboard',
    'peer_review_form',
    # Event action workspaces. Public event and series pages remain indexable
    # for signed-in visitors.
    'event_join',
    'event_join_legacy',
    'event_cancel_registration',
    'event_cancel_registration_action',
    # Private sprint participation workspaces and their form actions. Public
    # sprint discovery/detail and join/leave entry points are not included.
    'cohort_board',
    'sprint_feedback_fill',
    'sprint_feedback_submit',
    'member_plan_detail',
    'my_plan_detail',
    'my_plan_markdown_download',
    'my_plan_edit',
    'update_plan_visibility',
    'update_plan_goal',
    'carry_over_tasks',
    'undo_slack_progress',
    'week_note_create',
    'week_note_update',
    'week_note_delete',
})

_PRIVATE_API_RESULT_PATHS = frozenset({
    '/api/password-reset',
    '/api/verify-email',
    '/api/unsubscribe',
    '/api/maven-email-opt-out',
})

# Keep in sync with the recognized recovery messages in
# ``payments.services.membership_context``. Unknown query values are ordinary
# public Membership views and must remain indexable.
_CHECKOUT_ERROR_CODES = frozenset({
    'temporarily_unavailable',
    'invalid_interval',
    'tier_unavailable',
})

SEARCH_ENVIRONMENT_PRODUCTION = 'production'
SEARCH_ENVIRONMENT_DEV = 'dev'
SEARCH_ENVIRONMENT_OTHER = 'other'


def search_indexing_environment():
    """Classify the deployment from the environment-backed site hostname."""
    try:
        hostname = urlsplit(str(settings.SITE_BASE_URL).strip()).hostname
    except ValueError:
        return SEARCH_ENVIRONMENT_OTHER

    hostname = (hostname or '').lower()
    if hostname == PRODUCTION_SITE_HOST:
        return SEARCH_ENVIRONMENT_PRODUCTION
    if hostname == DEV_SITE_HOST:
        return SEARCH_ENVIRONMENT_DEV
    return SEARCH_ENVIRONMENT_OTHER


def search_indexing_disabled():
    """Return whether this process is serving the development deployment."""
    return search_indexing_environment() == SEARCH_ENVIRONMENT_DEV


def production_search_indexing_enabled():
    """Return whether this process is the explicitly classified production."""
    return search_indexing_environment() == SEARCH_ENVIRONMENT_PRODUCTION


def _in_path_family(path, family):
    return path == family or path.startswith(f'{family}/')


def _resolved_route_name(request):
    match = getattr(request, 'resolver_match', None)
    if match is not None:
        return match.url_name

    # RemoveTrailingSlashMiddleware returns before Django resolves the request,
    # but the outer indexing middleware still needs to classify that redirect.
    # Resolve only its slashless target so unknown and public redirects do not
    # inherit a private directive from a broad path-prefix guess.
    path = request.path_info
    if path == '/' or not path.endswith('/'):
        return None
    try:
        return resolve(
            path.rstrip('/'),
            urlconf=getattr(request, 'urlconf', None),
        ).url_name
    except Resolver404:
        return None


def production_request_indexing_disabled(request):
    """Return whether a production request is a private/operational surface."""
    path = request.path.rstrip('/') or '/'

    if any(_in_path_family(path, family) for family in _PRIVATE_PATH_FAMILIES):
        return True
    if path in _PRIVATE_API_RESULT_PATHS:
        return True
    if _resolved_route_name(request) in _PRIVATE_ROUTE_NAMES:
        return True

    user = getattr(request, 'user', None)
    if path == '/' and getattr(user, 'is_authenticated', False):
        return True

    if path == '/membership':
        if request.GET.get('checkout') == 'cancelled':
            return True
        if request.GET.get('checkout_error') in _CHECKOUT_ERROR_CODES:
            return True

    return False


def request_indexing_disabled(request):
    """Compose dev-wide and production page-level exclusion policies."""
    return search_indexing_disabled() or production_request_indexing_disabled(
        request,
    )
