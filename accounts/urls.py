from django.urls import path, include

from accounts.views import login_view, logout_view

urlpatterns = [
    path('login/', login_view, name='account_login'),
    path('logout/', logout_view, name='account_logout'),
    path('', include('allauth.urls')),
]
