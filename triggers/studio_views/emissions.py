"""Read-only Studio log of event emissions (issue #1070)."""

from django.shortcuts import render

from studio.decorators import staff_required
from triggers.models import EventEmission


@staff_required
def emission_list(request):
    emissions = EventEmission.objects.select_related("user")[:200]
    return render(
        request,
        "studio/triggers/emission_list.html",
        {"emissions": emissions},
    )
