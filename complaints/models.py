from datetime import timedelta

from django.db import models
from django.conf import settings
import random
import string

from .validators import media_kind, validate_media_attachment

User = settings.AUTH_USER_MODEL

def generate_tracking_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not Complaint.objects.filter(tracking_code=code).exists():
            return code


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    estimated_days = models.PositiveIntegerField(
        default=7, help_text='Usual number of days the barangay needs to act on this kind of complaint')
    recommended_action = models.TextField(
        blank=True, help_text='Suggested barangay action or policy when this category becomes the most reported')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name_plural = "Categories"


class Complaint(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
    ]
    REJECTION_REASONS = [
        ('outside_jurisdiction', 'Outside Barangay 10 jurisdiction'),
        ('insufficient_details', 'Insufficient details or evidence'),
        ('duplicate', 'Duplicate of an existing complaint'),
        ('not_barangay_matter', 'Not a barangay matter, referred to the proper office'),
        ('false_report', 'False or prank report'),
        ('withdrawn', 'Withdrawn by the complainant'),
        ('other', 'Other reason'),
    ]
    SOURCE_CHOICES = [
        ('online', 'Filed online'),
        ('walk_in', 'Walk-in, encoded by barangay staff'),
    ]
    SATISFACTION_CHOICES = [
        ('satisfied', 'Satisfied'),
        ('not_satisfied', 'Not Satisfied'),
    ]
    
    tracking_code = models.CharField(max_length=8, unique=True, default=generate_tracking_code)
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='complaints')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
    description = models.TextField()
    location = models.CharField(max_length=200)
    image = models.ImageField(upload_to='complaints/', blank=True, null=True)
    attachment = models.FileField(upload_to='complaints/media/', blank=True, null=True,
                                  validators=[validate_media_attachment],
                                  help_text='Optional video or audio evidence')

    # Background of the complaint, as recorded in the barangay blotter
    incident_date = models.DateField(blank=True, null=True)
    respondent_name = models.CharField(max_length=150, blank=True,
                                       help_text='Person or establishment being complained about, if known')

    # Walk-in complaints encoded by staff for residents without internet access
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='online')
    complainant_name = models.CharField(max_length=150, blank=True)
    complainant_contact = models.CharField(max_length=50, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    status_updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_complaints')
    admin_notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True, help_text='When the complaint was resolved or rejected')
    
    # Feedback for resolved/rejected complaints
    resolution_feedback = models.TextField(blank=True, null=True, help_text="Feedback for resolved or rejected complaints")
    rejection_reason = models.CharField(max_length=30, choices=REJECTION_REASONS, blank=True)

    # Complainant's feedback on how the barangay handled the complaint
    satisfaction = models.CharField(max_length=15, choices=SATISFACTION_CHOICES, blank=True)
    satisfaction_comment = models.TextField(blank=True)
    satisfaction_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.tracking_code} - {self.category or 'No Category'}"
    
    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.status == 'resolved' and not self.resolved_at:
            self.resolved_at = timezone.now()
        if self.status in ('resolved', 'rejected'):
            if not self.closed_at:
                self.closed_at = timezone.now()
        else:
            self.closed_at = None
        if self.status != 'rejected':
            self.rejection_reason = ''
        super().save(*args, **kwargs)

    @property
    def complainant_display(self):
        if self.source == 'walk_in' and self.complainant_name:
            return self.complainant_name
        return f"{self.submitted_by.first_name} {self.submitted_by.last_name}".strip()

    @property
    def complainant_email(self):
        return '' if self.source == 'walk_in' and self.complainant_name else self.submitted_by.email

    @property
    def estimated_days(self):
        return self.category.estimated_days if self.category else 7

    @property
    def expected_by(self):
        return self.created_at + timedelta(days=self.estimated_days) if self.created_at else None

    @property
    def response_days(self):
        """Days the barangay took to resolve or reject the complaint."""
        if not self.closed_at or not self.created_at:
            return None
        return round((self.closed_at - self.created_at).total_seconds() / 86400, 1)

    @property
    def is_overdue(self):
        from django.utils import timezone
        return self.status not in ('resolved', 'rejected') and self.expected_by and timezone.now() > self.expected_by

    @property
    def can_give_feedback(self):
        return self.status in ('resolved', 'rejected') and not self.satisfaction

    @property
    def attachment_kind(self):
        return media_kind(self.attachment.name) if self.attachment else ''


class ComplaintStatusHistory(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name='status_history')
    status = models.CharField(max_length=20, choices=Complaint.STATUS_CHOICES)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.complaint.tracking_code} - {self.status}"
    
    class Meta:
        ordering = ['created_at']
        verbose_name_plural = "Status histories"

