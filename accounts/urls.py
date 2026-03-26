from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('admin-login/', views.admin_login_view, name='admin_login'),
    path('logout/', views.logout_view, name='logout'),
    path('manage-users/', views.manage_users, name='manage_users'),
    path('audit-log/', views.audit_log, name='audit_log'),
    path('verify/<int:user_id>/', views.verify_email, name='verify_email'),
    path('resend-code/<int:user_id>/', views.resend_code, name='resend_code'),
]