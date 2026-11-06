from django.conf import settings


def barangay(request):
    """Makes the barangay name and live update interval available in every template."""
    return {
        'BARANGAY_NAME': getattr(settings, 'BARANGAY_NAME', 'Barangay 10'),
        'STATUS_POLL_SECONDS': getattr(settings, 'STATUS_POLL_SECONDS', 15),
    }


def admin_nav(request):
    """Counts shown as badges in the admin sidebar. Only computed for barangay staff."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and user.is_admin()):
        return {}
    from accounts.models import User
    from .models import Complaint
    return {
        'nav_active_count': Complaint.objects.exclude(status__in=['resolved', 'rejected']).count(),
        'nav_residents_waiting': User.objects.filter(role='resident', residency_status='pending', is_verified=True).count(),
    }
