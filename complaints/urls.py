from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('submit/', views.submit_complaint, name='submit_complaint'),
    path('success/<str:tracking_code>/', views.complaint_success, name='complaint_success'),
    path('track/', views.track_complaint, name='track_complaint'),
    path('my-complaints/', views.my_complaints, name='my_complaints'),
    path('my-complaints/status/', views.my_complaints_status_api, name='my_complaints_status_api'),
    path('track/<str:tracking_code>/status/', views.complaint_status_api, name='complaint_status_api'),
    path('guide/', views.user_guide, name='user_guide'),
    
    # Admin URLs
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('update-status/<int:complaint_id>/', views.update_status, name='update_status'),
    path('categories/', views.manage_categories, name='manage_categories'),
    path('complaint/<int:complaint_id>/', views.complaint_detail, name='complaint_detail'),
    path('walk-in/', views.walk_in_complaint, name='walk_in_complaint'),
    path('track/<str:tracking_code>/feedback/', views.submit_feedback, name='submit_feedback'),
    
    # New URLs
    path('resolved-complaints/', views.resolved_complaints, name='resolved_complaints'),
    path('pending-complaints/', views.pending_complaints, name='pending_complaints'),
    
    # Export URLs
    path('export/excel/', views.export_complaints_excel, name='export_complaints_excel'),
    path('export/csv/', views.export_complaints_csv, name='export_complaints_csv'),
    path('export/pdf/', views.export_complaints_pdf, name='export_complaints_pdf'),
    
    # Captain URLs
    path('captain-dashboard/', views.captain_dashboard, name='captain_dashboard'),

        # Complaint Status Pages
    path('resolved-complaints/', views.resolved_complaints, name='resolved_complaints'),
    path('pending-complaints/', views.pending_complaints, name='pending_complaints'),
    path('rejected-complaints/', views.rejected_complaints, name='rejected_complaints'),

]