from django.db import models
from django.conf import settings
import random
import string

User = settings.AUTH_USER_MODEL

def generate_tracking_code():
    """Generate a unique 8-character tracking code"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not Complaint.objects.filter(tracking_code=code).exists():
            return code


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
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
    
    tracking_code = models.CharField(max_length=8, unique=True, default=generate_tracking_code)
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='complaints')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
    description = models.TextField()
    location = models.CharField(max_length=200)
    image = models.ImageField(upload_to='complaints/', blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    status_updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_complaints')
    admin_notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    # Spam detection
    is_spam = models.BooleanField(default=False)
    spam_confidence = models.FloatField(default=0.0)
    reviewed_by_admin = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.tracking_code} - {self.category or 'No Category'}"
    
    def save(self, *args, **kwargs):
        if self.status == 'resolved' and not self.resolved_at:
            from django.utils import timezone
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)


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


class AnonymousComplaint(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
    ]
    
    tracking_code = models.CharField(max_length=8, unique=True, default=generate_tracking_code, editable=False)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField()
    location = models.CharField(max_length=200)
    image = models.ImageField(upload_to='anonymous_complaints/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Spam detection
    is_spam = models.BooleanField(default=False)
    spam_confidence = models.FloatField(default=0.0)
    reviewed_by_admin = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Anonymous - {self.tracking_code}"
    
    def get_status_display(self):
        """Return the display name for the status"""
        return dict(self.STATUS_CHOICES).get(self.status, self.status)
    
    class Meta:
        verbose_name_plural = "Anonymous Complaints"
        ordering = ['-created_at']