from django.conf import settings


def barangay(request):
    """Makes the barangay name and live update interval available in every template."""
    return {
        'BARANGAY_NAME': getattr(settings, 'BARANGAY_NAME', 'Barangay 10'),
        'STATUS_POLL_SECONDS': getattr(settings, 'STATUS_POLL_SECONDS', 15),
    }
