from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
import random
import string

def valid_id_path(instance, filename):
    """Store IDs under a random name so the file address cannot be guessed."""
    import os
    import uuid
    return f'valid_ids/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()}'


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    
    email = models.EmailField(unique=True)
    ROLE_CHOICES = [
        ('resident', 'Resident'),
        ('admin', 'Admin'),
        ('captain', 'Captain'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='resident')
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=6, blank=True, null=True)
    verification_code_created_at = models.DateTimeField(blank=True, null=True)

    # Proof of residency, checked by barangay staff before the resident can file complaints
    ID_TYPE_CHOICES = [
        ('national_id', 'Philippine National ID'),
        ('drivers_license', "Driver's License"),
        ('voters_id', "Voter's ID or Certificate"),
        ('postal_id', 'Postal ID'),
        ('student_id', 'School ID'),
        ('barangay_id', 'Barangay ID or Certificate of Residency'),
        ('other', 'Other Government ID'),
    ]
    RESIDENCY_CHOICES = [
        ('pending', 'Waiting for Verification'),
        ('approved', 'Verified Resident'),
        ('rejected', 'Not Verified'),
    ]
    birth_date = models.DateField(blank=True, null=True)
    address = models.CharField(max_length=255, blank=True)
    contact_number = models.CharField(max_length=20, blank=True)
    valid_id_type = models.CharField(max_length=30, choices=ID_TYPE_CHOICES, blank=True)
    valid_id = models.ImageField(upload_to=valid_id_path, blank=True, null=True)
    residency_status = models.CharField(max_length=20, choices=RESIDENCY_CHOICES, default='pending')
    residency_note = models.TextField(blank=True)
    residency_reviewed_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name='reviewed_residents')
    residency_reviewed_at = models.DateTimeField(blank=True, null=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = UserManager()
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    def is_admin(self):
        return self.role in ['admin', 'captain']
    
    def is_captain(self):
        return self.role == 'captain'

    def can_file_complaints(self):
        """Staff can always file; residents only after their ID is verified."""
        return self.role != 'resident' or self.residency_status == 'approved'

    @property
    def age(self):
        if not self.birth_date:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.birth_date.year - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
    
    def generate_verification_code(self):
        """Generate a 6-digit verification code"""
        import random
        from django.utils import timezone
        self.verification_code = ''.join(random.choices(string.digits, k=6))
        self.verification_code_created_at = timezone.now()
        self.save()
        return self.verification_code


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('create_complaint', 'Create Complaint'),
        ('update_status', 'Update Status'),
        ('create_user', 'Create User'),
        ('update_user', 'Update User'),
        ('delete_user', 'Delete User'),
        ('email_verified', 'Email Verified'),
        ('verification_sent', 'Verification Code Sent'),
        ('review_resident', 'Review Resident'),
        ('review_spam', 'Review Spam'),
        ('walk_in', 'Encode Walk-in Complaint'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']