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


def _recipient(complaint):
    """Walk-in complaints without a resident account have no email to notify."""
    email = complaint.complainant_email
    first_name = complaint.complainant_display.split(' ')[0] if complaint.complainant_display else ''
    return email, first_name


def notify_complaint_received(complaint, request=None):
    """Confirmation email right after a resident files a complaint."""
    email, first_name = _recipient(complaint)
    name = settings.BARANGAY_NAME
    subject = f'{name}: We received your complaint ({complaint.tracking_code})'
    body = (
        f'Hello {first_name},\n\n'
        f'{name} has received your complaint.\n\n'
        f'Tracking code: {complaint.tracking_code}\n'
        f'Category: {complaint.category.name if complaint.category else "Uncategorized"}\n'
        f'Location: {complaint.location}\n'
        f'Received on: {complaint.created_at:%B %d, %Y %I:%M %p}\n'
        f'Expected action: within {complaint.estimated_days} days, by {complaint.expected_by:%B %d, %Y}\n\n'
        'What happens next:\n'
        '1. Barangay staff review your complaint.\n'
        '2. If it is valid, it is marked In Progress while the barangay acts on it.\n'
        '3. You get an email every time the status changes.\n'
        '4. When it is resolved or rejected, you will see the barangay\'s notes.\n\n'
        f'Track your complaint anytime: {_track_link(request, complaint.tracking_code)}\n\n'
        f'Thank you,\n{name}'
    )
    return _send_in_background(subject, body, email)


def notify_status_changed(complaint, request=None, notes=''):
    """Email the resident whenever an admin changes the complaint status."""
    email, first_name = _recipient(complaint)
    name = settings.BARANGAY_NAME
    status = complaint.get_status_display()
    subject = f'{name}: Your complaint {complaint.tracking_code} is now {status}'
    body = (
        f'Hello {first_name},\n\n'
        f'The status of your complaint {complaint.tracking_code} was updated.\n\n'
        f'New status: {status}\n'
        f'{STATUS_MEANING.get(complaint.status, "")}\n'
    )
    if complaint.status == 'rejected' and complaint.rejection_reason:
        body += f'\nReason: {complaint.get_rejection_reason_display()}\n'
    if notes:
        body += f'\nNotes from the barangay: {notes}\n'
    if complaint.status in ('resolved', 'rejected'):
        body += '\nPlease tell us if you are satisfied with how your complaint was handled. Open the link below and answer the short feedback form.\n'
    body += (
        f'\nSee the full details: {_track_link(request, complaint.tracking_code)}\n\n'
        f'Thank you,\n{name}'
    )
    return _send_in_background(subject, body, email)


def notify_residency_reviewed(user, request=None):
    """Tell a resident whether their valid ID was accepted."""
    name = settings.BARANGAY_NAME
    if user.residency_status == 'approved':
        subject = f'{name}: Your account is verified'
        body = (f'Hello {user.first_name},\n\n'
                f'Barangay staff checked your valid ID and verified you as a resident of {name}. '
                'You can now file complaints using your account.\n\n')
    else:
        subject = f'{name}: We could not verify your account'
        body = (f'Hello {user.first_name},\n\n'
                'Barangay staff could not verify your account from the ID you uploaded.\n')
        if user.residency_note:
            body += f'Reason: {user.residency_note}\n'
        body += ('\nYou may upload a clearer ID, or visit the barangay hall with a valid ID so staff can file the complaint for you as a walk-in.\n\n')
    login_link = request.build_absolute_uri(reverse('login')) if request else reverse('login')
    body += f'Log in: {login_link}\n\nThank you,\n{name}'
    return _send_in_background(subject, body, user.email)
