from django.contrib import admin
from .models import Category, Complaint, ComplaintStatusHistory

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']

@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ['tracking_code', 'submitted_by', 'category', 'status', 'is_spam', 'created_at']
    list_filter = ['status', 'category', 'is_spam', 'created_at']
    search_fields = ['tracking_code', 'description', 'location']
    readonly_fields = ['tracking_code', 'created_at', 'updated_at']

@admin.register(ComplaintStatusHistory)
class ComplaintStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ['complaint', 'status', 'changed_by', 'created_at']
    list_filter = ['status', 'created_at']