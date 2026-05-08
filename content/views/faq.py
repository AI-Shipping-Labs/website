from django.shortcuts import redirect


def faq(request):
    """Permanent compatibility redirect for the legacy FAQ URL."""
    return redirect("/about#faq", permanent=True)
