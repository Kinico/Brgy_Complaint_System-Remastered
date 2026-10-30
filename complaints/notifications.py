"""Email notifications for residents.

Emails are sent in a background thread so the page responds right away,
even when the mail server is slow. If email is not configured in .env,
nothing is sent and the system keeps working normally.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)

STATUS_MEANING = {
    'pending': 'Your complaint was received and is waiting to be reviewed by barangay staff.',
    'under_review': 'Barangay staff are reviewing the details of your complaint.',
    'in_progress': 'Your complaint was accepted and the barangay is now acting on it.',
    'resolved': 'The barangay has resolved your complaint.',
    'rejected': 'The barangay could not act on this complaint. See the reason below.',
}


def _email_enabled():
    return bool(getattr(settings, 'EMAIL_HOST_USER', '')) and bool(getattr(settings, 'EMAIL_HOST_PASSWORD', ''))


def _send(subject, body, recipient):
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    except Exception:
        logger.exception('Could not send email to %s', recipient)


def _send_in_background(subject, body, recipient):
    if not recipient or not _email_enabled():
        return False
    threading.Thread(target=_send, args=(subject, body, recipient), daemon=True).start()
    return True


def _track_link(request, tracking_code):
    path = f"{reverse('track_complaint')}?code={tracking_code}"
    return request.build_absolute_uri(path) if request else path


def notify_complaint_received(complaint, request=None):
    """Confirmation email right after a resident files a complaint."""
    user = complaint.submitted_by
    name = settings.BARANGAY_NAME
    subject = f'{name}: We received your complaint ({complaint.tracking_code})'
    body = (
        f'Hello {user.first_name},\n\n'
        f'{name} has received your complaint.\n\n'
        f'Tracking code: {complaint.tracking_code}\n'
        f'Category: {complaint.category.name if complaint.category else "Uncategorized"}\n'
        f'Location: {complaint.location}\n'
        f'Received on: {complaint.created_at:%B %d, %Y %I:%M %p}\n\n'
        'What happens next:\n'
        '1. Barangay staff review your complaint.\n'
        '2. If it is valid, it is marked In Progress while the barangay acts on it.\n'
        '3. You get an email every time the status changes.\n'
        '4. When it is resolved or rejected, you will see the barangay\'s notes.\n\n'
        f'Track your complaint anytime: {_track_link(request, complaint.tracking_code)}\n\n'
        f'Thank you,\n{name}'
    )
    return _send_in_background(subject, body, user.email)


def notify_status_changed(complaint, request=None, notes=''):
    """Email the resident whenever an admin changes the complaint status."""
    user = complaint.submitted_by
    name = settings.BARANGAY_NAME
    status = complaint.get_status_display()
    subject = f'{name}: Your complaint {complaint.tracking_code} is now {status}'
    body = (
        f'Hello {user.first_name},\n\n'
        f'The status of your complaint {complaint.tracking_code} was updated.\n\n'
        f'New status: {status}\n'
        f'{STATUS_MEANING.get(complaint.status, "")}\n'
    )
    if notes:
        body += f'\nNotes from the barangay: {notes}\n'
    body += (
        f'\nSee the full details: {_track_link(request, complaint.tracking_code)}\n\n'
        f'Thank you,\n{name}'
    )
    return _send_in_background(subject, body, user.email)
