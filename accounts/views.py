from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from .models import User, AuditLog
from .forms import RegistrationForm, VerificationCodeForm


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def log_audit(user, action, details='', request=None):
    AuditLog.objects.create(
        user=user,
        action=action,
        details=details,
        ip_address=get_client_ip(request) if request else None
    )


def send_verification_code(user, request):
    """Send 6-digit verification code via email"""
    code = user.generate_verification_code()
    
    subject = 'Verify Your Email - Barangay 11'
    html_message = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Verify Your Email</title>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 20px; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 30px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #f97316, #c2410c); padding: 20px; text-align: center; border-radius: 12px 12px 0 0; margin: -30px -30px 30px -30px; }}
            .header h1 {{ color: white; font-size: 24px; margin: 0; }}
            .code {{ font-size: 36px; font-weight: bold; letter-spacing: 8px; text-align: center; background: #fef3c7; padding: 20px; border-radius: 12px; margin: 20px 0; font-family: monospace; }}
            .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Barangay 11</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 5px 0 0;">Complaint Management System</p>
            </div>
            <h2>Hello {user.first_name} {user.last_name},</h2>
            <p>Thank you for registering! Please use the code below to verify your email address:</p>
            <div class="code">{code}</div>
            <p>This code will expire in <strong>10 minutes</strong>.</p>
            <p>If you did not create an account, please ignore this email.</p>
            <div class="footer">
                <p>&copy; 2024 Barangay 11 Complaint Management System</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    plain_message = f"""
Hello {user.first_name} {user.last_name},

Thank you for registering! Please use the code below to verify your email address:

{code}

This code will expire in 10 minutes.

If you did not create an account, please ignore this email.

Best regards,
Barangay 11 Team
"""
    
    send_mail(
        subject,
        plain_message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        html_message=html_message,
        fail_silently=False,
    )
    log_audit(user, 'verification_sent', f'Verification code sent to {user.email}', request)


def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            send_verification_code(user, request)
            messages.success(request, 
                f'Registration successful! We sent a 6-digit verification code to {user.email}')
            return redirect('verify_email', user_id=user.id)
    else:
        form = RegistrationForm()
    return render(request, 'accounts/register.html', {'form': form})


def verify_email(request, user_id):
    user = get_object_or_404(User, id=user_id)
    
    # Check if already verified
    if user.is_verified:
        messages.info(request, 'Your email is already verified. Please login.')
        return redirect('login')
    
    # Check if code expired (10 minutes)
    if user.verification_code_created_at:
        expiry = user.verification_code_created_at + timedelta(minutes=10)
        if timezone.now() > expiry:
            messages.error(request, 'Verification code has expired. Please request a new one.')
            return redirect('resend_code', user_id=user.id)
    
    if request.method == 'POST':
        form = VerificationCodeForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['code']
            if user.verification_code == code:
                user.is_verified = True
                user.is_active = True
                user.verification_code = None
                user.save()
                log_audit(user, 'email_verified', 'Email verified', request)
                messages.success(request, 'Email verified successfully! You can now login.')
                return redirect('login')
            else:
                messages.error(request, 'Invalid verification code. Please try again.')
    else:
        form = VerificationCodeForm()
    
    return render(request, 'accounts/verify_email.html', {
        'form': form,
        'user': user,
        'email': user.email
    })


def resend_code(request, user_id):
    user = get_object_or_404(User, id=user_id)
    
    if user.is_verified:
        messages.info(request, 'Your email is already verified.')
        return redirect('login')
    
    send_verification_code(user, request)
    messages.success(request, f'A new verification code has been sent to {user.email}')
    return redirect('verify_email', user_id=user.id)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        email = request.POST.get('username')
        password = request.POST.get('password')
        
        try:
            user_obj = User.objects.get(email=email)
            if not user_obj.is_active:
                messages.error(request, 'Please verify your email before logging in. Check your inbox for the verification code.')
                return redirect('verify_email', user_id=user_obj.id)
            if not user_obj.is_verified:
                messages.error(request, 'Please verify your email first.')
                return redirect('verify_email', user_id=user_obj.id)
        except User.DoesNotExist:
            pass
        
        user = authenticate(request, username=email, password=password)
        
        if user:
            login(request, user)
            log_audit(user, 'login', 'Resident logged in', request)
            messages.success(request, f'Welcome back {user.first_name}!')
            return redirect('home')
        else:
            messages.error(request, 'Invalid email or password')
    return render(request, 'accounts/login.html')


def admin_login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        try:
            user_obj = User.objects.get(email=email)
            if not user_obj.is_active:
                messages.error(request, 'Please verify your email before logging in.')
                return redirect('admin_login')
        except User.DoesNotExist:
            pass
        
        user = authenticate(request, username=email, password=password)
        
        if user and user.role in ['admin', 'captain']:
            login(request, user)
            log_audit(user, 'login', f'Admin logged in as {user.role}', request)
            messages.success(request, f'Welcome {user.first_name}!')
            
            if user.role == 'captain':
                return redirect('captain_dashboard')
            else:
                return redirect('admin_dashboard')
        else:
            messages.error(request, 'Invalid email or password')
    
    return render(request, 'accounts/admin_login.html')


def logout_view(request):
    if request.user.is_authenticated:
        log_audit(request.user, 'logout', 'User logged out', request)
    logout(request)
    messages.success(request, 'Logged out successfully')
    return redirect('home')


@login_required
@user_passes_test(lambda u: u.is_captain())
def manage_users(request):
    users = User.objects.all().order_by('-date_joined')
    
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action = request.POST.get('action')
        target_user = get_object_or_404(User, id=user_id)
        
        if action == 'change_role':
            new_role = request.POST.get('role')
            target_user.role = new_role
            target_user.save()
            log_audit(request.user, 'update_user', f'Changed {target_user.email} role to {new_role}', request)
            messages.success(request, f'Role updated for {target_user.first_name} {target_user.last_name}')
        elif action == 'delete':
            if target_user == request.user:
                messages.error(request, 'Cannot delete your own account')
            else:
                target_user.delete()
                log_audit(request.user, 'delete_user', f'Deleted user {target_user.email}', request)
                messages.success(request, f'User deleted')
        
        return redirect('manage_users')
    
    context = {
        'users': users,
        'total': users.count(),
        'residents': users.filter(role='resident').count(),
        'admins': users.filter(role='admin').count(),
        'captains': users.filter(role='captain').count(),
    }
    return render(request, 'accounts/manage_users.html', context)


@login_required
@user_passes_test(lambda u: u.is_captain())
def audit_log(request):
    logs = AuditLog.objects.all().order_by('-created_at')
    return render(request, 'accounts/audit_log.html', {'logs': logs})