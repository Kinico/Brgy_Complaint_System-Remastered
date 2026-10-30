from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
import random
import string

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
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = UserManager()
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    def is_admin(self):
        return self.role in ['admin', 'captain']
    
    def is_captain(self):
        return self.role == 'captain'
    
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
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='audit_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']