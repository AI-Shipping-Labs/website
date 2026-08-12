"""Read-only Studio log of event emissions (issue #1070)."""

from django.shortcuts import render

from studio.decorators import staff_required
from studio.utils import studio_pagination_context
from triggers.models import EventEmission


@staff_required
def emission_list(request):
    emissions = EventEmission.objects.select_related("user")
    pager = studio_pagination_context(request, emissions)
    return render(
        request,
        "studio/triggers/emission_list.html",
        {
            "emissions": pager["page"].object_list,
            **pager,
        },
    )
