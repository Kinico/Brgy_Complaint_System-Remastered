from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from .models import User, AuditLog
from .forms import RegistrationForm

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

def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.role = 'resident'
            user.save()
            login(request, user)
            log_audit(user, 'login', 'User registered', request)
            messages.success(request, f'Welcome {user.first_name}!')
            return redirect('home')
    else:
        form = RegistrationForm()
    return render(request, 'accounts/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('username')
        password = request.POST.get('password')
        
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