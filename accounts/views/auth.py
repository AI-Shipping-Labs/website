from django.contrib.auth import logout
from django.shortcuts import redirect, render


def login_view(request):
    """Render the login page with Google and GitHub OAuth buttons."""
    if request.user.is_authenticated:
        return redirect("/")
    return render(request, "accounts/login.html")


def logout_view(request):
    """Log out the user and redirect to homepage."""
    logout(request)
    return redirect("/")
