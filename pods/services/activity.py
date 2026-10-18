"""The community activity a pod belongs to (issue #1921).

A pod lives inside exactly one activity: a dated course ``Cohort`` or a
community ``Sprint``. Every decision that used to read ``pod.cohort``
directly goes through :class:`PodActivity`:

- who is a participant (cohort: enrolled with course access; sprint: a
  ``SprintEnrollment`` row exists),
- who may view the member pages (participants plus staff as operators),
- whether member pages are on (cohort: ``PODS_COURSE_SLUGS``; sprint:
  ``Sprint.pods_enabled``),
- the member URLs (list, new, availability, pod page and its sub-actions),
- the display label and the noun used in member copy.

Cohort URLs and copy are unchanged; sprint URLs live under
``/sprints/<slug>/pods`` with names prefixed ``sprint_``.
"""

from urllib.parse import urlencode

from django.urls import reverse

from content.models import Cohort, CohortEnrollment
from plans.models import Sprint, SprintEnrollment
from pods.services import config as pods_config
from pods.services.people import can_view_cohort_pods, is_cohort_participant, is_dated_cohort

KIND_COHORT = 'cohort'
KIND_SPRINT = 'sprint'

# Sprint statuses whose pod pages are hidden from non-staff (the sprint
# detail page hides drafts; a cancelled sprint has no member surface).
HIDDEN_SPRINT_STATUSES = ('draft', 'cancelled')

# Course route name -> sprint route name for the list page; every other
# ``pod_*`` route maps to ``sprint_pod_*``.
_COURSE_LIST_ROUTE = 'course_pods'
_SPRINT_LIST_ROUTE = 'sprint_pods'


def _with_query(url, params):
    params = [(key, value) for key, value in params if value]
    return f'{url}?{urlencode(params)}' if params else url


class PodActivity:
    """A dated cohort or a sprint, seen through the pods lens."""

    def __init__(self, *, cohort=None, sprint=None):
        if (cohort is None) == (sprint is None):
            raise ValueError('A pod activity is exactly one cohort or one sprint.')
        self.cohort = cohort
        self.sprint = sprint

    # --- Identity ------------------------------------------------------------

    @property
    def kind(self):
        return KIND_SPRINT if self.sprint is not None else KIND_COHORT

    @property
    def is_sprint(self):
        return self.sprint is not None

    @property
    def obj(self):
        return self.sprint if self.is_sprint else self.cohort

    @property
    def noun(self):
        """``cohort`` or ``sprint``, for member and staff copy."""
        return self.kind

    @property
    def label(self):
        """``Course - Cohort`` or the sprint name."""
        if self.is_sprint:
            return self.sprint.name
        return f'{self.cohort.course.title} - {self.cohort.name}'

    def __eq__(self, other):
        return isinstance(other, PodActivity) and self.kind == other.kind and self.obj.pk == other.obj.pk

    def __hash__(self):
        return hash((self.kind, self.obj.pk))

    def pod_filter(self, prefix=''):
        """Queryset kwargs selecting this activity's pods (``prefix='pod__'``
        for related lookups)."""
        return {f'{prefix}{self.kind}': self.obj}

    # --- Rules ---------------------------------------------------------------

    def can_have_pods(self):
        """Sprints always can; a cohort must be dated (not self-paced)."""
        return self.is_sprint or is_dated_cohort(self.cohort)

    def is_participant(self, user):
        """May start pods and ask to join them."""
        if not getattr(user, 'is_authenticated', False):
            return False
        if self.is_sprint:
            return SprintEnrollment.objects.filter(sprint=self.sprint, user=user).exists()
        return is_cohort_participant(user, self.cohort)

    def is_visible_to_members(self):
        """A sprint in draft or cancelled has no member pod pages."""
        return not (self.is_sprint and self.sprint.status in HIDDEN_SPRINT_STATUSES)

    def can_view(self, user):
        """Participants and staff (as operators) see the member pages."""
        if not getattr(user, 'is_authenticated', False):
            return False
        if not self.is_sprint:
            return can_view_cohort_pods(user, self.cohort)
        if user.is_staff:
            return True
        return self.is_visible_to_members() and self.is_participant(user)

    def member_pages_enabled(self):
        """Cohort: course in ``PODS_COURSE_SLUGS``; sprint: ``pods_enabled``."""
        if self.is_sprint:
            return bool(self.sprint.pods_enabled)
        return pods_config.pods_enabled_for_course(self.cohort.course)

    def enrolled_user_ids(self, user_ids):
        """The ids among ``user_ids`` enrolled in the activity (one query).

        Staff adds check enrollment only (no course-access check), the same
        rule cohort pods always used.
        """
        user_ids = list(user_ids)
        if not user_ids:
            return set()
        if self.is_sprint:
            rows = SprintEnrollment.objects.filter(sprint=self.sprint, user_id__in=user_ids)
        else:
            rows = CohortEnrollment.objects.filter(cohort=self.cohort, user_id__in=user_ids)
        return set(rows.values_list('user_id', flat=True))

    def is_enrolled(self, user):
        return user is not None and user.pk in self.enrolled_user_ids([user.pk])

    # --- URLs ----------------------------------------------------------------

    def _route(self, name):
        if not self.is_sprint:
            return name
        if name == _COURSE_LIST_ROUTE:
            return _SPRINT_LIST_ROUTE
        return f'sprint_{name}'

    def _base_kwargs(self):
        if self.is_sprint:
            return {'sprint_slug': self.sprint.slug}
        return {'slug': self.cohort.course.slug}

    def _cohort_query(self):
        return [] if self.is_sprint else [('cohort', self.cohort.external_key)]

    def list_url(self):
        url = reverse(self._route(_COURSE_LIST_ROUTE), kwargs=self._base_kwargs())
        return _with_query(url, self._cohort_query())

    def new_url(self):
        url = reverse(self._route('pod_new'), kwargs=self._base_kwargs())
        return _with_query(url, self._cohort_query())

    def availability_url(self, next_url=''):
        url = reverse(self._route('pod_availability'), kwargs=self._base_kwargs())
        return _with_query(url, self._cohort_query() + [('next', next_url)])

    def action_url(self, pod, name, **kwargs):
        """Any per-pod route by its course name, e.g. ``pod_request``."""
        return reverse(self._route(name), kwargs={**self._base_kwargs(), 'pod_id': pod.pk, **kwargs})

    def pod_url(self, pod):
        return self.action_url(pod, 'pod_detail')

    def home_url(self):
        """The sprint page, or the course Home (header back-links)."""
        if self.is_sprint:
            return reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug})
        return reverse('course_home', kwargs={'slug': self.cohort.course.slug})

    # --- Copy ----------------------------------------------------------------

    @property
    def copy(self):
        """Member copy that names the activity. Cohort strings are the
        original #1918 copy, byte for byte."""
        if self.is_sprint:
            return {
                'list_subtitle': 'Small groups in this sprint that meet to work on one thing together.',
                'empty_body': 'Start a study group with a purpose and others in this sprint can join.',
                'new_subtitle': 'You become the owner and first member. Others in this sprint can join.',
                'open_label': 'Open: members of the sprint can join',
            }
        return {
            'list_subtitle': 'Small groups in your cohort that meet to work on one thing together.',
            'empty_body': 'Start a study group with a purpose and others in your cohort can join.',
            'new_subtitle': 'You become the owner and first member. Others in your cohort can join.',
            'open_label': 'Open: members of the cohort can join',
        }


def cohort_activity(cohort):
    return PodActivity(cohort=cohort)


def sprint_activity(sprint):
    return PodActivity(sprint=sprint)


def as_activity(value):
    """Accept a ``PodActivity``, ``Cohort`` or ``Sprint``."""
    if isinstance(value, PodActivity):
        return value
    if isinstance(value, Sprint):
        return sprint_activity(value)
    if isinstance(value, Cohort):
        return cohort_activity(value)
    raise TypeError(f'Not a pod activity: {value!r}')


def activity_for_pod(pod):
    """The activity of ``pod`` (exactly one link is set, DB-enforced)."""
    if pod.sprint_id:
        return sprint_activity(pod.sprint)
    return cohort_activity(pod.cohort)


def activity_label(pod):
    return activity_for_pod(pod).label


__all__ = [
    'HIDDEN_SPRINT_STATUSES',
    'KIND_COHORT',
    'KIND_SPRINT',
    'PodActivity',
    'activity_for_pod',
    'activity_label',
    'as_activity',
    'cohort_activity',
    'sprint_activity',
]
