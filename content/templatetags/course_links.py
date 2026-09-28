"""Template access to the shared course entry-link rule.

``{% course_entry_url course cohort=reader_cohort_param %}`` renders course
Home for an enrolled learner with access and the public landing otherwise.
Use it (optionally with ``as var``) for every in-app "open course" or
"back to course" link. SEO surfaces (canonical, structured data, share
links) keep ``course.get_absolute_url`` on purpose.
"""

from django import template

from content.services.course_navigation import course_entry_url as _course_entry_url

register = template.Library()


@register.simple_tag(takes_context=True)
def course_entry_url(context, course, cohort='', section='home', enrolled_course_ids=None):
    request = context.get('request')
    user = getattr(request, 'user', None) if request is not None else context.get('user')
    return _course_entry_url(
        user, course, cohort=cohort or '', section=section,
        enrolled_course_ids=enrolled_course_ids,
    )
