from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("register/", views.register_view, name="register"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("collect/", views.collect_query, name="collect_query"),
    path("run/<str:event>/<path:filename>", views.run_detail, name="run_detail"),
]
