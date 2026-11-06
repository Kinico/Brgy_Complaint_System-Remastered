from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db.models import Q, Count, Avg
from django.utils import timezone
from datetime import datetime, timedelta
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from urllib.parse import quote
import json
from .models import Complaint, Category, ComplaintStatusHistory
from .forms import ComplaintForm, WalkInComplaintForm, SatisfactionForm
from .analytics import build_recommendations, satisfaction_summary
from django.conf import settings
from accounts.models import AuditLog
from accounts.views import log_audit, get_client_ip
from .notifications import notify_complaint_received, notify_status_changed, STATUS_MEANING


def home(request):
    categories = Category.objects.all()
    total = Complaint.objects.count()
    resolved = Complaint.objects.filter(status='resolved').count()

    # Public summary for residents (no names): complaints per category and how many were acted on
    rows = (Complaint.objects.filter(category__isnull=False)
            .values('category__name')
            .annotate(total=Count('id'),
                      resolved=Count('id', filter=Q(status='resolved')),
                      rejected=Count('id', filter=Q(status='rejected')))
            .order_by('-total', 'category__name'))
    category_chart = {
        'labels': [r['category__name'] for r in rows],
        'resolved': [r['resolved'] for r in rows],
        'open': [r['total'] - r['resolved'] - r['rejected'] for r in rows],
        'rejected': [r['rejected'] for r in rows],
    }
    top = rows[0] if rows else None
    since = timezone.now() - timedelta(days=30)
    month_top = (Complaint.objects.filter(category__isnull=False, created_at__gte=since)
                 .values('category__name').annotate(n=Count('id')).order_by('-n').first())

    return render(request, 'complaints/landing.html', {
        'categories': categories,
        'total_complaints': total,
        'resolved_complaints': resolved,
        'resolution_rate': round(resolved / total * 100) if total else 0,
        'category_chart': category_chart,
        'top_category': top,
        'month_top': month_top,
        'category_count_used': len(category_chart['labels']),
    })
@login_required
@user_passes_test(lambda u: u.is_admin())
def rejected_complaints(request):
    """View all rejected complaints"""
    complaints = Complaint.objects.filter(status='rejected').order_by('-updated_at')
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search) |
            Q(submitted_by__email__icontains=search)
        )
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(updated_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(updated_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(updated_at__date__gte=start_date)
    elif date_range == 'year':
        start_date = today - timedelta(days=365)
        complaints = complaints.filter(updated_at__date__gte=start_date)
    
    total_rejected = complaints.count()
    rejected_this_month = complaints.filter(updated_at__month=today.month).count()
    
    paginator = Paginator(complaints, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'complaints': page_obj,
        'total_rejected': total_rejected,
        'rejected_this_month': rejected_this_month,
        'search': search,
        'date_range': date_range,
    }
    return render(request, 'complaints/rejected_complaints.html', context)

@login_required
def submit_complaint(request):
    if not request.user.can_file_complaints():
        return render(request, 'complaints/verification_required.html', status=403)
    if request.method == 'POST':
        form = ComplaintForm(request.POST, request.FILES)
        if form.is_valid():
            complaint = form.save(commit=False)
            complaint.submitted_by = request.user
            complaint.save()
            
            ComplaintStatusHistory.objects.create(
                complaint=complaint,
                status='pending',
                changed_by=request.user,
                notes='Complaint submitted'
            )
            
            log_audit(request.user, 'create_complaint', f'Created complaint {complaint.tracking_code}', request)
            
            if notify_complaint_received(complaint, request):
                request.session['confirmation_email_sent'] = complaint.tracking_code
            
            messages.success(request, f'Complaint submitted! Code: {complaint.tracking_code}')
            
            return redirect('complaint_success', tracking_code=complaint.tracking_code)
    else:
        form = ComplaintForm()
    
    return render(request, 'complaints/submit_complaint.html', {'form': form})


def complaint_success(request, tracking_code):
    complaint = get_object_or_404(Complaint, tracking_code=tracking_code)
    email_sent = request.session.pop('confirmation_email_sent', None) == complaint.tracking_code
    return render(request, 'complaints/complaint_success.html', {
        'complaint': complaint,
        'email_sent': email_sent,
    })


STATUS_STEPS = [
    ('pending', 'Received'),
    ('under_review', 'Under Review'),
    ('in_progress', 'In Progress'),
    ('resolved', 'Resolved'),
]


def _status_steps(status):
    """Progress steps for the tracking page, marking which ones are done."""
    order = [key for key, _ in STATUS_STEPS]
    current = order.index(status) if status in order else -1
    steps = []
    for index, (key, label) in enumerate(STATUS_STEPS):
        steps.append({
            'key': key,
            'label': label,
            'done': status != 'rejected' and index <= current,
            'current': index == current,
        })
    return steps


def user_guide(request):
    """Step by step guide for first time users."""
    return render(request, 'complaints/user_guide.html')


def _is_walk_in_without_account(complaint):
    return complaint.source == 'walk_in' and complaint.submitted_by.role != 'resident'


def _is_owner(user, complaint):
    if _is_walk_in_without_account(complaint):
        return True  # the tracking code given at the barangay hall is the key
    return user.is_authenticated and complaint.submitted_by_id == user.id


def _can_view(user, complaint):
    return _is_owner(user, complaint) or (user.is_authenticated and user.is_admin())


def track_complaint(request):
    """One tracking box for every complaint filed by a verified resident or at the hall.

    Form posts are redirected to ?code=... so the result page can be
    refreshed (and auto updated) without the browser asking to resubmit.
    """
    if request.method == 'POST':
        code = request.POST.get('tracking_code', '').upper().strip()
        if code:
            return redirect(f"{reverse('track_complaint')}?code={code}")
        messages.error(request, 'Please enter a tracking code.')
        return redirect('track_complaint')

    code = request.GET.get('code', '').upper().strip()
    if code:
        complaint = Complaint.objects.filter(tracking_code=code).first()
        if complaint:
            if _can_view(request.user, complaint):
                return render(request, 'complaints/track_result.html', {
                    'complaint': complaint,
                    'steps': _status_steps(complaint.status),
                    'status_meaning': STATUS_MEANING.get(complaint.status, ''),
                    'feedback_form': SatisfactionForm() if complaint.can_give_feedback else None,
                    'is_owner': _is_owner(request.user, complaint),
                })
            elif request.user.is_authenticated:
                messages.error(request, 'You can only view your own complaints.')
            else:
                messages.error(request, 'Please log in to view this complaint. It was filed with an account.')
                next_url = quote(f"{reverse('track_complaint')}?code={code}")
                return redirect(f"{reverse('login')}?next={next_url}")
        else:
            messages.error(request, 'No complaint found with that tracking code. Please check the code and try again.')

    return render(request, 'complaints/track_complaint.html', {'code': code})


def complaint_status_api(request, tracking_code):
    """Small JSON endpoint the tracking pages check every few seconds."""
    code = tracking_code.upper().strip()
    complaint = Complaint.objects.filter(tracking_code=code).first()
    if not complaint:
        return JsonResponse({'error': 'not found'}, status=404)
    if not _can_view(request.user, complaint):
        return JsonResponse({'error': 'not allowed'}, status=403)
    history_count = complaint.status_history.count()

    return JsonResponse({
        'status': complaint.status,
        'status_display': complaint.get_status_display(),
        'version': f"{complaint.status}|{complaint.updated_at.isoformat()}|{history_count}",
    })


@login_required
def my_complaints_status_api(request):
    """Lets the My Complaints page notice status changes without a manual refresh."""
    rows = Complaint.objects.filter(submitted_by=request.user).order_by('id').values_list(
        'tracking_code', 'status', 'updated_at'
    )
    version = ';'.join(f"{code}:{status}:{updated.isoformat()}" for code, status, updated in rows)
    return JsonResponse({'version': version})


@login_required
def my_complaints(request):
    complaints = Complaint.objects.filter(submitted_by=request.user).select_related('category').order_by('-created_at')
    return render(request, 'complaints/my_complaints.html', {'complaints': complaints})


@login_required
@user_passes_test(lambda u: u.is_admin())
def admin_dashboard(request):
    complaints = Complaint.objects.all().order_by('-created_at')
    
    date_range = request.GET.get('date_range', 'all')
    if date_range == 'today':
        complaints = complaints.filter(created_at__date=timezone.now().date())
    elif date_range == 'week':
        complaints = complaints.filter(created_at__gte=timezone.now() - timedelta(days=7))
    elif date_range == 'month':
        complaints = complaints.filter(created_at__gte=timezone.now() - timedelta(days=30))
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search)
        )
    
    total = complaints.count()
    stats = {
        'pending': complaints.filter(status='pending').count(),
        'under_review': complaints.filter(status='under_review').count(),
        'in_progress': complaints.filter(status='in_progress').count(),
        'resolved': complaints.filter(status='resolved').count(),
        'rejected': complaints.filter(status='rejected').count(),
    }
    
    category_data = [
        {'name': r['category__name'], 'count': r['total'], 'resolved': r['resolved'], 'rejected': r['rejected'],
         'open': r['total'] - r['resolved'] - r['rejected']}
        for r in complaints.filter(category__isnull=False).values('category__name')
        .annotate(total=Count('id'), resolved=Count('id', filter=Q(status='resolved')),
                  rejected=Count('id', filter=Q(status='rejected')))
        .order_by('-total', 'category__name')
    ]
    
    # Last 6 calendar months, oldest first (counting back by month, not by 30 days,
    # so no month is skipped or repeated)
    monthly_data = []
    today = timezone.localdate(timezone.now())
    year, month = today.year, today.month
    months = []
    for _ in range(6):
        months.insert(0, (year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    for year, month in months:
        count = complaints.filter(created_at__year=year, created_at__month=month).count()
        monthly_data.append({
            'month': datetime(year, month, 1).strftime('%b'),
            'count': count
        })
    
    pending_complaints_count = Complaint.objects.exclude(status__in=['resolved', 'rejected']).count()
    
    # The table shows the 50 newest active complaints; the full list is on the Pending page
    active = complaints.exclude(status__in=['resolved', 'rejected']).select_related('submitted_by', 'category')
    
    context = {
        'complaints': complaints,
        'active_complaints': active[:50],
        'active_total': active.count(),
        'stats': stats,
        'total': total,
        'category_data': json.dumps(category_data),
        'monthly_data': json.dumps(monthly_data),
        'search': search,
        'date_range': date_range,
        'pending_complaints_count': pending_complaints_count,
        'recommendations': build_recommendations(),
        'satisfaction': satisfaction_summary(Complaint.objects.all()),
        'overdue_count': sum(1 for c in Complaint.objects.exclude(status__in=['resolved', 'rejected']).select_related('category') if c.is_overdue),
        'residents_waiting': _residents_waiting(),
        'avg_response_days': _average_response_days(),
        'now': timezone.localtime(),
    }
    return render(request, 'complaints/admin_dashboard.html', context)


def _average_response_days():
    closed = Complaint.objects.filter(closed_at__isnull=False).values_list('created_at', 'closed_at')
    days = [(end - start).total_seconds() / 86400 for start, end in closed]
    return round(sum(days) / len(days), 1) if days else None


def _residents_waiting():
    from accounts.models import User
    return User.objects.filter(role='resident', residency_status='pending', is_verified=True).count()


@login_required
@user_passes_test(lambda u: u.is_admin())
def update_status(request, complaint_id):
    complaint = get_object_or_404(Complaint, id=complaint_id)
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        feedback = request.POST.get('feedback', '').strip()
        reason = request.POST.get('rejection_reason', '')
        
        if new_status not in dict(Complaint.STATUS_CHOICES):
            messages.error(request, 'Please choose a valid status.')
            return redirect(request.META.get('HTTP_REFERER', 'admin_dashboard'))
        if new_status == 'rejected' and reason not in dict(Complaint.REJECTION_REASONS):
            messages.error(request, f'Please choose the reason for rejecting {complaint.tracking_code}.')
            return redirect(request.META.get('HTTP_REFERER', 'admin_dashboard'))
        
        old_status = complaint.status
        complaint.status = new_status
        complaint.status_updated_by = request.user
        complaint.rejection_reason = reason if new_status == 'rejected' else ''
        
        if new_status in ['resolved', 'rejected']:
            complaint.resolution_feedback = feedback
        else:
            complaint.resolution_feedback = ''
        
        complaint.save()
        
        notes = ''
        if new_status == 'rejected':
            notes = complaint.get_rejection_reason_display() + (f': {feedback}' if feedback else '')
        elif new_status == 'resolved':
            notes = feedback
        ComplaintStatusHistory.objects.create(
            complaint=complaint,
            status=new_status,
            changed_by=request.user,
            notes=notes
        )
        
        log_audit(request.user, 'update_status', 
                 f'Changed {complaint.tracking_code} from {old_status} to {new_status}', request)
        
        if old_status != new_status:
            notify_status_changed(complaint, request, feedback if new_status in ['resolved', 'rejected'] else '')
        
        if feedback and new_status in ['resolved', 'rejected']:
            messages.success(request, f'Status updated to {new_status} with feedback added.')
        else:
            messages.success(request, f'Status updated to {new_status}')
    
    return redirect(request.META.get('HTTP_REFERER', 'admin_dashboard'))


def _positive_int(value, default):
    try:
        number = int(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default


@login_required
@user_passes_test(lambda u: u.is_admin())
def manage_categories(request):
    categories = Category.objects.all()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            name = request.POST.get('name')
            desc = request.POST.get('description')
            Category.objects.create(name=name, description=desc,
                                    estimated_days=_positive_int(request.POST.get('estimated_days'), 7),
                                    recommended_action=request.POST.get('recommended_action', '').strip())
            messages.success(request, f'Category "{name}" added')
            log_audit(request.user, 'create_complaint', f'Added category {name}', request)
        
        elif action == 'edit':
            cat_id = request.POST.get('category_id')
            cat = get_object_or_404(Category, id=cat_id)
            cat.name = request.POST.get('name')
            cat.description = request.POST.get('description')
            cat.estimated_days = _positive_int(request.POST.get('estimated_days'), cat.estimated_days)
            cat.recommended_action = request.POST.get('recommended_action', cat.recommended_action).strip()
            cat.save()
            messages.success(request, 'Category updated')
        
        elif action == 'delete':
            cat_id = request.POST.get('category_id')
            Category.objects.filter(id=cat_id).delete()
            messages.success(request, 'Category deleted')
        
        return redirect('manage_categories')
    
    return render(request, 'complaints/manage_categories.html', {'categories': categories})


@login_required
@user_passes_test(lambda u: u.is_captain())
def captain_dashboard(request):
    from accounts.models import User
    
    residents = User.objects.filter(role='resident')
    admins = User.objects.filter(role='admin')
    captains = User.objects.filter(role='captain')
    
    total_complaints = Complaint.objects.count()
    resolved_complaints = Complaint.objects.filter(status='resolved').count()
    
    context = {
        'residents': residents,
        'admins': admins,
        'captains': captains,
        'total_residents': residents.count(),
        'total_admins': admins.count(),
        'total_captains': captains.count(),
        'total_users': User.objects.count(),
        'total_complaints': total_complaints,
        'resolved_complaints': resolved_complaints,
    }
    return render(request, 'complaints/captain_dashboard.html', context)


@login_required
@user_passes_test(lambda u: u.is_admin())
def resolved_complaints(request):
    complaints = Complaint.objects.filter(status='resolved').order_by('-resolved_at')
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search) |
            Q(submitted_by__email__icontains=search)
        )
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(resolved_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(resolved_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(resolved_at__date__gte=start_date)
    elif date_range == 'year':
        start_date = today - timedelta(days=365)
        complaints = complaints.filter(resolved_at__date__gte=start_date)
    
    total_resolved = complaints.count()
    resolved_this_month = complaints.filter(resolved_at__year=today.year, resolved_at__month=today.month).count()
    
    avg_seconds = 0
    for complaint in complaints:
        if complaint.resolved_at and complaint.created_at:
            diff = complaint.resolved_at - complaint.created_at
            avg_seconds += diff.total_seconds()
    
    if complaints.count() > 0:
        avg_seconds = avg_seconds / complaints.count()
        avg_hours = avg_seconds / 3600
        avg_days = avg_hours / 24
    else:
        avg_hours = 0
        avg_days = 0
    
    total_complaints = Complaint.objects.count()
    if total_complaints > 0:
        resolution_rate = (total_resolved / total_complaints) * 100
    else:
        resolution_rate = 0
    
    paginator = Paginator(complaints, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'complaints': page_obj,
        'total_resolved': total_resolved,
        'resolved_this_month': resolved_this_month,
        'avg_resolution_hours': round(avg_hours, 1),
        'avg_resolution_days': round(avg_days, 1),
        'resolution_rate': round(resolution_rate, 1),
        'search': search,
        'date_range': date_range,
    }
    return render(request, 'complaints/resolved_complaints.html', context)


@login_required
@user_passes_test(lambda u: u.is_admin())
def pending_complaints(request):
    # Every active complaint
    complaints = Complaint.objects.select_related('submitted_by', 'category').exclude(
        status__in=['resolved', 'rejected']
    ).order_by('-created_at')
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search) |
            Q(submitted_by__email__icontains=search)
        )
    
    status_filter = request.GET.get('status_filter', 'all')
    if status_filter != 'all':
        complaints = complaints.filter(status=status_filter)
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(created_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(created_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(created_at__date__gte=start_date)
    
    total_active = complaints.count()
    pending_count = complaints.filter(status='pending').count()
    under_review_count = complaints.filter(status='under_review').count()
    in_progress_count = complaints.filter(status='in_progress').count()
    
    paginator = Paginator(complaints, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'complaints': page_obj,
        'total_active': total_active,
        'pending_count': pending_count,
        'under_review_count': under_review_count,
        'in_progress_count': in_progress_count,
        'search': search,
        'status_filter': status_filter,
        'date_range': date_range,
    }
    return render(request, 'complaints/pending_complaints.html', context)


def _export_row(complaint):
    return [
        complaint.tracking_code,
        complaint.complainant_display,
        complaint.complainant_email,
        complaint.get_source_display(),
        complaint.category.name if complaint.category else 'Uncategorized',
        complaint.description,
        complaint.location,
        complaint.incident_date.strftime('%Y-%m-%d') if complaint.incident_date else '',
        complaint.respondent_name,
        complaint.get_status_display(),
        complaint.get_rejection_reason_display() if complaint.rejection_reason else '',
        complaint.resolution_feedback or '',
        complaint.created_at.strftime('%Y-%m-%d %H:%M'),
        complaint.resolved_at.strftime('%Y-%m-%d %H:%M') if complaint.resolved_at else '',
        complaint.response_days if complaint.response_days is not None else '',
        complaint.get_satisfaction_display() if complaint.satisfaction else '',
    ]


@login_required
@user_passes_test(lambda u: u.is_admin())
def export_complaints_excel(request):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    
    complaints = Complaint.objects.all().order_by('-created_at')
    
    status_filter = request.GET.get('status', '')
    if status_filter:
        complaints = complaints.filter(status=status_filter)
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(created_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(created_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(created_at__date__gte=start_date)
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search)
        )
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Complaints"
    
    headers = ['Tracking Code', 'Complainant', 'Email', 'Source', 'Category', 'Description', 
               'Location', 'Incident Date', 'Person Involved', 'Status', 'Rejection Reason', 'Feedback',
               'Created Date', 'Resolved Date', 'Response Days', 'Complainant Satisfaction']
    
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4E73DF", end_color="4E73DF", fill_type="solid")
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
    
    for row, complaint in enumerate(complaints.select_related('submitted_by', 'category'), 2):
        for col, value in enumerate(_export_row(complaint), 1):
            ws.cell(row=row, column=col, value=value)
    
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f"complaints_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    wb.save(response)
    return response


@login_required
@user_passes_test(lambda u: u.is_admin())
def export_complaints_csv(request):
    import csv
    
    complaints = Complaint.objects.all().order_by('-created_at')
    
    status_filter = request.GET.get('status', '')
    if status_filter:
        complaints = complaints.filter(status=status_filter)
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(created_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(created_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(created_at__date__gte=start_date)
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search)
        )
    
    response = HttpResponse(content_type='text/csv')
    filename = f"complaints_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    writer = csv.writer(response)
    
    writer.writerow(['Tracking Code', 'Complainant', 'Email', 'Source', 'Category', 'Description', 
                     'Location', 'Incident Date', 'Person Involved', 'Status', 'Rejection Reason', 'Feedback',
                     'Created Date', 'Resolved Date', 'Response Days', 'Complainant Satisfaction'])
    
    for complaint in complaints.select_related('submitted_by', 'category'):
        writer.writerow(_export_row(complaint))
    
    return response


@login_required
@user_passes_test(lambda u: u.is_admin())
def export_complaints_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    
    complaints = Complaint.objects.all().order_by('-created_at')
    
    status_filter = request.GET.get('status', '')
    if status_filter:
        complaints = complaints.filter(status=status_filter)
    
    date_range = request.GET.get('date_range', 'all')
    today = timezone.now().date()
    if date_range == 'today':
        complaints = complaints.filter(created_at__date=today)
    elif date_range == 'week':
        start_date = today - timedelta(days=7)
        complaints = complaints.filter(created_at__date__gte=start_date)
    elif date_range == 'month':
        start_date = today - timedelta(days=30)
        complaints = complaints.filter(created_at__date__gte=start_date)
    
    search = request.GET.get('search', '')
    if search:
        complaints = complaints.filter(
            Q(tracking_code__icontains=search) |
            Q(description__icontains=search) |
            Q(location__icontains=search) |
            Q(submitted_by__first_name__icontains=search) |
            Q(submitted_by__last_name__icontains=search)
        )
    
    response = HttpResponse(content_type='application/pdf')
    filename = f"complaints_{timezone.now().strftime('%Y%m%d_%H%M')}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    doc = SimpleDocTemplate(response, pagesize=landscape(letter))
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    title_style.alignment = 1
    
    elements.append(Paragraph("Barangay Complaint System Report", title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    date_style = styles['Normal']
    date_style.alignment = 1
    elements.append(Paragraph(f"Generated: {timezone.now().strftime('%B %d, %Y %I:%M %p')}", date_style))
    elements.append(Spacer(1, 0.3*inch))
    
    elements.append(Paragraph("Summary Statistics", styles['Heading2']))
    elements.append(Spacer(1, 0.1*inch))
    
    stats_data = [
        ['Metric', 'Count'],
        ['Total Complaints', str(complaints.count())],
        ['Pending', str(complaints.filter(status='pending').count())],
        ['Under Review', str(complaints.filter(status='under_review').count())],
        ['In Progress', str(complaints.filter(status='in_progress').count())],
        ['Resolved', str(complaints.filter(status='resolved').count())],
        ['Rejected', str(complaints.filter(status='rejected').count())],
    ]
    
    stats_table = Table(stats_data, colWidths=[2*inch, 1.5*inch])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4E73DF')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(stats_table)
    elements.append(Spacer(1, 0.3*inch))
    
    elements.append(Paragraph("Complaint Details", styles['Heading2']))
    elements.append(Spacer(1, 0.1*inch))
    
    table_data = [['Tracking', 'Complainant', 'Category', 'Status', 'Feedback', 'Date']]
    
    for complaint in complaints.select_related('submitted_by', 'category'):
        feedback = complaint.resolution_feedback or ''
        if len(feedback) > 50:
            feedback = feedback[:47] + '...'
        table_data.append([
            complaint.tracking_code,
            complaint.complainant_display,
            complaint.category.name if complaint.category else 'Uncategorized',
            complaint.get_status_display(),
            feedback,
            complaint.created_at.strftime('%Y-%m-%d')
        ])
    
    complaint_table = Table(table_data, colWidths=[1.2*inch, 1.5*inch, 1.2*inch, 1*inch, 1.5*inch, 1.2*inch])
    complaint_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4E73DF')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(complaint_table)

    recommendations = build_recommendations()
    if recommendations:
        elements.append(Spacer(1, 0.3*inch))
        elements.append(Paragraph("Recommended Actions Based on the Most Reported Complaints (Last 30 Days)", styles['Heading2']))
        cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=8, leading=10, alignment=0)
        rec_data = [['Category', 'Complaints', 'Trend', 'Most Reported Location', 'Recommended Action']]
        for rec in recommendations:
            rec_data.append([rec['category'], f"{rec['count']} ({rec['share']}%)", rec['trend'],
                             Paragraph(rec['top_location'] or '-', cell), Paragraph(rec['action'], cell)])
        rec_table = Table(rec_data, colWidths=[1.2*inch, 0.9*inch, 1*inch, 1.8*inch, 3.6*inch])
        rec_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4E73DF')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(rec_table)

    # Signatories
    elements.append(Spacer(1, 0.6*inch))
    preparer = f"{request.user.first_name} {request.user.last_name}".strip()
    preparer_title = 'Barangay Captain' if request.user.is_captain() else 'Barangay Administrator'
    captain = settings.BARANGAY_CAPTAIN_NAME or ' '
    sign = ParagraphStyle('sign', parent=styles['Normal'], fontSize=10, leading=13, alignment=1)
    sign_table = Table([
        [Paragraph('Prepared by:', styles['Normal']), '', Paragraph('Noted by:', styles['Normal'])],
        ['', '', ''],
        [Paragraph(f'<b>{preparer.upper()}</b>', sign), '', Paragraph(f'<b>{captain.upper()}</b>', sign)],
        [Paragraph(preparer_title, sign), '', Paragraph('Punong Barangay', sign)],
    ], colWidths=[3*inch, 1.5*inch, 3*inch], rowHeights=[0.25*inch, 0.45*inch, 0.25*inch, 0.25*inch])
    sign_table.setStyle(TableStyle([
        ('LINEABOVE', (0, 2), (0, 2), 0.8, colors.black),
        ('LINEABOVE', (2, 2), (2, 2), 0.8, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    elements.append(sign_table)
    
    doc.build(elements)
    return response

@login_required
@user_passes_test(lambda u: u.is_captain())
def create_admin(request):
    """Create a new admin account (Captain only)"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        
        # Check if passwords match
        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return redirect('create_admin')
        
        # Check password length
        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters long.')
            return redirect('create_admin')
        
        # Check if email already exists
        if User.objects.filter(email=email).exists():
            messages.error(request, f'Email {email} is already registered.')
            return redirect('create_admin')
        
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

@login_required
@user_passes_test(lambda u: u.is_admin())
def complaint_detail(request, complaint_id):
    """Full record of one complaint for barangay staff, with the complainant's history."""
    complaint = get_object_or_404(Complaint.objects.select_related('submitted_by', 'category', 'status_updated_by'),
                                  id=complaint_id)
    if _is_walk_in_without_account(complaint):
        history = Complaint.objects.filter(source='walk_in', complainant_name__iexact=complaint.complainant_name)
    else:
        history = Complaint.objects.filter(submitted_by=complaint.submitted_by)
    history = history.exclude(id=complaint.id).select_related('category').order_by('-created_at')
    return render(request, 'complaints/complaint_detail.html', {
        'complaint': complaint,
        'complainant_history': history,
        'rejection_reasons': Complaint.REJECTION_REASONS,
        'resident': None if _is_walk_in_without_account(complaint) else complaint.submitted_by,
    })


@login_required
@user_passes_test(lambda u: u.is_admin())
def walk_in_complaint(request):
    """Staff encode a complaint brought to the barangay hall, for example during an
    emergency or when the resident has no internet connection."""
    if request.method == 'POST':
        form = WalkInComplaintForm(request.POST, request.FILES)
        if form.is_valid():
            complaint = form.save(commit=False)
            resident = form.cleaned_data.get('resident_email')
            complaint.submitted_by = resident or request.user
            complaint.source = 'walk_in'
            complaint.save()
            ComplaintStatusHistory.objects.create(
                complaint=complaint, status='pending', changed_by=request.user,
                notes=f'Walk-in complaint encoded by {request.user.first_name} {request.user.last_name}')
            log_audit(request.user, 'walk_in', f'Encoded walk-in complaint {complaint.tracking_code}', request)
            if resident:
                notify_complaint_received(complaint, request)
            messages.success(request, f'Walk-in complaint saved. Give the complainant this tracking code: {complaint.tracking_code}')
            return redirect('complaint_detail', complaint_id=complaint.id)
    else:
        form = WalkInComplaintForm()
    return render(request, 'complaints/walk_in_complaint.html', {'form': form})


def submit_feedback(request, tracking_code):
    """The complainant says whether they are satisfied with how the complaint was handled."""
    complaint = get_object_or_404(Complaint, tracking_code=tracking_code.upper())
    track_url = f"{reverse('track_complaint')}?code={complaint.tracking_code}"
    if request.method != 'POST':
        return redirect(track_url)
    if not _is_owner(request.user, complaint):
        messages.error(request, 'Only the complainant can give feedback on this complaint.')
        return redirect(track_url)
    if not complaint.can_give_feedback:
        messages.info(request, 'Feedback can be given once, after the complaint is resolved or rejected.')
        return redirect(track_url)
    form = SatisfactionForm(request.POST)
    if form.is_valid():
        complaint.satisfaction = form.cleaned_data['satisfaction']
        complaint.satisfaction_comment = form.cleaned_data['satisfaction_comment']
        complaint.satisfaction_at = timezone.now()
        complaint.save(update_fields=['satisfaction', 'satisfaction_comment', 'satisfaction_at', 'updated_at'])
        messages.success(request, 'Thank you! Your feedback was sent to the barangay.')
    else:
        messages.error(request, 'Please choose Satisfied or Not Satisfied.')
    return redirect(track_url)
