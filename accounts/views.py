from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import FileResponse, Http404, HttpResponse
from django.db.models import Q, Count
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
    
    subject = f'Verify Your Email - {settings.BARANGAY_NAME}'
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
                <h1>{settings.BARANGAY_NAME}</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 5px 0 0;">Complaint Management System</p>
            </div>
            <h2>Hello {user.first_name} {user.last_name},</h2>
            <p>Thank you for registering! Please use the code below to verify your email address:</p>
            <div class="code">{code}</div>
            <p>This code will expire in <strong>10 minutes</strong>.</p>
            <p>If you did not create an account, please ignore this email.</p>
            <div class="footer">
                <p>&copy; {timezone.now().year} {settings.BARANGAY_NAME} Complaint Management System</p>
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
{settings.BARANGAY_NAME} Team
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
        form = RegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            send_verification_code(user, request)
            messages.success(request, 
                f'Registration successful! We sent a 6-digit verification code to {user.email}. '
                'Barangay staff will also check your valid ID before you can file complaints.')
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
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
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

@login_required
@user_passes_test(lambda u: u.is_captain())
def create_admin(request):
    """Create a new admin account (Captain only)"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        
        # Check if email already exists
        if User.objects.filter(email=email).exists():
            messages.error(request, f'Email {email} is already registered.')
            return redirect('manage_users')
        
        # Create admin user
        admin_user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role='admin',
            is_staff=True,
            is_active=True,
            is_verified=True
        )
        
        log_audit(request.user, 'create_user', f'Created admin account: {email}', request)
        messages.success(request, f'Admin account created for {first_name} {last_name} ({email})')
        return redirect('manage_users')
    
    return render(request, 'accounts/create_admin.html')


def _is_staff_user(user):
    return user.is_authenticated and user.is_admin()


@login_required
@user_passes_test(_is_staff_user)
def resident_master_list(request):
    """Master list of registered residents, with ID verification (proof of residency)."""
    residents = User.objects.filter(role='resident').order_by('last_name', 'first_name')
    status = request.GET.get('status', 'all')
    if status in dict(User.RESIDENCY_CHOICES):
        residents = residents.filter(residency_status=status)
    search = request.GET.get('search', '').strip()
    if search:
        residents = residents.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search) |
                                     Q(email__icontains=search) | Q(address__icontains=search))
    all_residents = User.objects.filter(role='resident')
    context = {
        'residents': residents,
        'status': status,
        'search': search,
        'counts': {
            'all': all_residents.count(),
            'pending': all_residents.filter(residency_status='pending').count(),
            'approved': all_residents.filter(residency_status='approved').count(),
            'rejected': all_residents.filter(residency_status='rejected').count(),
        },
        'id_types': dict(User.ID_TYPE_CHOICES),
    }
    return render(request, 'accounts/resident_master_list.html', context)


@login_required
@user_passes_test(_is_staff_user)
def review_resident(request, user_id):
    resident = get_object_or_404(User, id=user_id, role='resident')
    if request.method == 'POST':
        decision = request.POST.get('decision')
        if decision not in ('approved', 'rejected'):
            messages.error(request, 'Please choose to verify or reject the resident.')
            return redirect('resident_master_list')
        note = request.POST.get('note', '').strip()
        if decision == 'rejected' and not note:
            messages.error(request, 'Please give the reason the ID was not accepted, so the resident knows what to do.')
            return redirect('resident_master_list')
        resident.residency_status = decision
        resident.residency_note = note
        resident.residency_reviewed_by = request.user
        resident.residency_reviewed_at = timezone.now()
        resident.save()
        log_audit(request.user, 'review_resident', f'{resident.email} marked {decision}', request)
        from complaints.notifications import notify_residency_reviewed
        notify_residency_reviewed(resident, request)
        label = 'verified' if decision == 'approved' else 'not verified'
        messages.success(request, f'{resident.first_name} {resident.last_name} is now {label}.')
    return redirect(request.META.get('HTTP_REFERER') or 'resident_master_list')


@login_required
@user_passes_test(_is_staff_user)
def view_valid_id(request, user_id):
    """IDs are personal data, so they are only shown to barangay staff through this page."""
    resident = get_object_or_404(User, id=user_id)
    if not resident.valid_id:
        raise Http404('No ID uploaded')
    try:
        return FileResponse(resident.valid_id.open('rb'))
    except (FileNotFoundError, OSError):
        raise Http404('ID file is missing')


@login_required
@user_passes_test(_is_staff_user)
def export_residents_excel(request):
    """Extract of the resident master list."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from complaints.models import Complaint

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Resident Master List'
    ws.append([f'{settings.BARANGAY_NAME} Resident Master List'])
    ws.append([f'Extracted {timezone.localtime():%B %d, %Y %I:%M %p} by {request.user.first_name} {request.user.last_name}'])
    ws.append([])
    headers = ['No.', 'Last Name', 'First Name', 'Birthday', 'Age', 'Address', 'Mobile Number', 'Email',
               'Valid ID', 'Status', 'Verified By', 'Date Registered', 'Complaints Filed']
    ws.append(headers)
    for cell in ws[4]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='F97316', end_color='F97316', fill_type='solid')
        cell.alignment = Alignment(horizontal='center')
    ws['A1'].font = Font(bold=True, size=14)
    counts = dict(Complaint.objects.filter(source='online').values_list('submitted_by').annotate(n=Count('id')).values_list('submitted_by', 'n'))
    residents = User.objects.filter(role='resident').order_by('last_name', 'first_name')
    status = request.GET.get('status')
    if status in dict(User.RESIDENCY_CHOICES):
        residents = residents.filter(residency_status=status)
    for number, r in enumerate(residents, 1):
        ws.append([
            number, r.last_name, r.first_name,
            r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '',
            r.age if r.age is not None else '',
            r.address, r.contact_number, r.email,
            r.get_valid_id_type_display() if r.valid_id_type else '',
            r.get_residency_status_display(),
            f'{r.residency_reviewed_by.first_name} {r.residency_reviewed_by.last_name}' if r.residency_reviewed_by else '',
            timezone.localtime(r.date_joined).strftime('%Y-%m-%d'),
            counts.get(r.id, 0),
        ])
    widths = [6, 18, 18, 12, 6, 40, 15, 30, 28, 22, 22, 15, 10]
    for index, width in enumerate(widths):
        ws.column_dimensions[openpyxl.utils.get_column_letter(index + 1)].width = width
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="resident_master_list_{timezone.now():%Y%m%d}.xlsx"'
    wb.save(response)
    return response
