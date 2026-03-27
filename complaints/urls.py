from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('submit/', views.submit_complaint, name='submit_complaint'),
    path('success/<str:tracking_code>/', views.complaint_success, name='complaint_success'),
    path('track/', views.track_complaint, name='track_complaint'),
    path('my-complaints/', views.my_complaints, name='my_complaints'),
    
    # Admin URLs
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('update-status/<int:complaint_id>/', views.update_status, name='update_status'),
    path('categories/', views.manage_categories, name='manage_categories'),
    path('review-spam/', views.review_spam, name='review_spam'),
    
    # New URLs
    path('resolved-complaints/', views.resolved_complaints, name='resolved_complaints'),
    path('pending-complaints/', views.pending_complaints, name='pending_complaints'),
    path('flag-spam/<int:complaint_id>/', views.flag_as_spam, name='flag_as_spam'),
    path('mark-not-spam/<int:complaint_id>/', views.mark_as_not_spam, name='mark_not_spam'),
    
    # Export URLs
    path('export/excel/', views.export_complaints_excel, name='export_complaints_excel'),
    path('export/csv/', views.export_complaints_csv, name='export_complaints_csv'),
    path('export/pdf/', views.export_complaints_pdf, name='export_complaints_pdf'),
    
    # Captain URLs
    path('captain-dashboard/', views.captain_dashboard, name='captain_dashboard'),

    #Anonymous complaint URLs
    path('anonymous/', views.anonymous_complaint, name='anonymous_complaint'),
    path('anonymous/success/<str:tracking_code>/', views.anonymous_success, name='anonymous_success'),
    path('anonymous/track/', views.track_anonymous, name='track_anonymous'),
    path('anonymous/track/<str:tracking_code>/', views.anonymous_track_result, name='anonymous_track_result'),

        # Complaint Status Pages
    path('resolved-complaints/', views.resolved_complaints, name='resolved_complaints'),
    path('pending-complaints/', views.pending_complaints, name='pending_complaints'),
    path('rejected-complaints/', views.rejected_complaints, name='rejected_complaints'),

]