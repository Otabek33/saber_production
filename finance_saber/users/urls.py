from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "users"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.SignUpView.as_view(), name="register"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("users/", views.UserListView.as_view(), name="list"),
    path("users/<int:pk>/access/", views.user_access, name="access"),
    path("users/add/", views.user_create, name="add"),
    path("users/<int:pk>/", views.user_edit, name="edit"),
    path("roles/", views.roles, name="roles"),
    path("roles/<str:role>/", views.role_edit, name="role_edit"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password_change"),
    # Password reset by Telegram code: username -> code -> new password.
    path("password/reset/", views.ResetRequestView.as_view(), name="password_reset"),
    path("password/reset/code/", views.ResetCodeView.as_view(), name="password_reset_code"),
    path("password/reset/new/", views.ResetPasswordView.as_view(), name="password_reset_new"),
    path("telegram/link/", views.telegram_link, name="telegram_link"),
    path("telegram/unlink/", views.telegram_unlink, name="telegram_unlink"),
    path("telegram/report/", views.telegram_report, name="telegram_report"),
    path("telegram/webhook/<str:secret>/", views.telegram_webhook, name="telegram_webhook"),
]
