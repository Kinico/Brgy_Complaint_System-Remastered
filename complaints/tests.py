"""System testing for the Barangay Complaint Management System.

Each test is linked to a Statement of the Problem item (Table 3, Testing
Procedure) or an ISO 25010 characteristic, and records its expected result,
actual result and timing. Run with:

    python manage.py test complaints -v 2

To test without touching the live Supabase database, run it with an empty
DATABASE_URL so Django uses a temporary SQLite test database:

    DATABASE_URL= python manage.py test complaints -v 2

A results table is printed at the end and saved to test_results.json.
"""
import io
import json
import os
import statistics
import time
from datetime import datetime, timedelta
from unittest import mock

from django.conf import settings
from django.core import mail
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, User
from complaints.models import Category, Complaint, ComplaintStatusHistory

RESULTS = []
TEST_MEDIA = tempfile.mkdtemp(prefix='brgy_test_media_')
LOCMEM = dict(
    MEDIA_ROOT=TEST_MEDIA,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='system.test@example.com',
    EMAIL_HOST_PASSWORD='test-only',
)
PASSWORD = 'Brgy10#Testing2026'


def record(case_id, sop, area, title, expected, actual, passed, ms=None):
    RESULTS.append({
        'id': case_id, 'sop': sop, 'area': area, 'title': title,
        'expected': expected, 'actual': actual,
        'result': 'Pass' if passed else 'Fail',
        'ms': None if ms is None else round(ms, 1),
    })


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - start) * 1000


def wait_for_mail(count, timeout=2.0):
    """Notification emails are sent from a background thread."""
    end = time.time() + timeout
    while len(mail.outbox) < count and time.time() < end:
        time.sleep(0.02)
    return len(mail.outbox) >= count


def make_user(email, role='resident', verified=True, residency='approved'):
    user = User.objects.create_user(email=email, password=PASSWORD, first_name=email.split('@')[0].title(),
                                    last_name='Tester', role=role)
    user.is_verified = verified
    user.is_active = verified
    user.residency_status = residency
    user.save()
    return user


def sample_id_photo(name='id.png'):
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    buffer = io.BytesIO()
    Image.new('RGB', (320, 200), (40, 90, 160)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def registration_data(email):
    return {'email': email, 'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'birth_date': '1990-05-14', 'address': '123 Rizal Street, Barangay 10, Caloocan City',
            'contact_number': '09171234567', 'valid_id_type': 'national_id', 'valid_id': sample_id_photo(),
            'password1': PASSWORD, 'password2': PASSWORD}


def tearDownModule():
    shutil.rmtree(TEST_MEDIA, ignore_errors=True)
    path = os.path.join(settings.BASE_DIR, 'test_results.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(sorted(RESULTS, key=lambda r: int(r['id'].split('-')[1])), handle, indent=2)
    print('\n\nSYSTEM TEST RESULTS')
    print('-' * 100)
    for row in sorted(RESULTS, key=lambda r: int(r['id'].split('-')[1])):
        ms = f"{row['ms']} ms" if row['ms'] is not None else ''
        print(f"{row['id']:<6} {row['result']:<5} {row['area']:<24} {row['title'][:48]:<48} {ms}")
    passed = sum(1 for r in RESULTS if r['result'] == 'Pass')
    print('-' * 100)
    print(f'{passed} of {len(RESULTS)} test cases passed. Saved to {path}')


@override_settings(**LOCMEM)
class FunctionalTests(TestCase):
    """SOP 1, 2, 5 and ISO Functional Suitability."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Noise', description='Noise complaints')
        cls.resident = make_user('resident@example.com')
        cls.admin = make_user('admin@example.com', role='admin')

    def setUp(self):
        mail.outbox = []
        self.client = Client()

    def test_01_online_submission(self):
        self.client.force_login(self.resident)
        data = {'category': self.category.id, 'location': 'Block 5 Lot 12, Barangay 10',
                'description': 'Loud videoke every night near the basketball court until 2 in the morning.'}
        response, ms = timed(lambda: self.client.post(reverse('submit_complaint'), data))
        complaint = Complaint.objects.filter(submitted_by=self.resident).first()
        ok = response.status_code == 302 and complaint is not None and len(complaint.tracking_code) == 8
        record('TC-01', 'SOP 1', 'Functional Suitability', 'Resident files a complaint online',
               'Complaint saved with an 8 character tracking code',
               f'Saved with code {complaint.tracking_code}' if complaint else 'Not saved', ok, ms)
        self.assertTrue(ok)

    def test_02_record_reaches_admin_immediately(self):
        self.client.force_login(self.resident)
        self.client.post(reverse('submit_complaint'), {
            'category': self.category.id, 'location': 'Phase 2 Street 4',
            'description': 'Clogged drainage causing flooding in front of houses after rain.'})
        code = Complaint.objects.latest('id').tracking_code
        admin = Client()
        admin.force_login(self.admin)
        response, ms = timed(lambda: admin.get(reverse('admin_dashboard')))
        ok = response.status_code == 200 and code in response.content.decode()
        record('TC-02', 'SOP 1', 'Functional Suitability', 'New complaint appears on the admin dashboard',
               'Visible to officials right after submission, no manual encoding',
               'Visible on dashboard immediately' if ok else 'Not visible', ok, ms)
        self.assertTrue(ok)

    def test_03_registration_with_email_verification(self):
        response = self.client.post(reverse('register'), registration_data('newresident@example.com'))
        user = User.objects.filter(email='newresident@example.com').first()
        sent = user is not None and len(mail.outbox) == 1 and user.verification_code in mail.outbox[0].body
        blocked = Client().post(reverse('login'), {'username': 'newresident@example.com', 'password': PASSWORD})
        blocked_ok = blocked.status_code == 302 and 'verify' in blocked['Location']
        verify = self.client.post(reverse('verify_email', args=[user.id]), {'code': user.verification_code}) if user else None
        user.refresh_from_db()
        login = Client().post(reverse('login'), {'username': 'newresident@example.com', 'password': PASSWORD})
        ok = response.status_code == 302 and sent and blocked_ok and user.is_verified and login.status_code == 302 and login['Location'] == '/'
        record('TC-03', 'SOP 2', 'Functional Suitability', 'Registration with 6 digit email verification',
               'Code emailed, login blocked until verified, login works after',
               'Code emailed, unverified login redirected to verification, verified login succeeded' if ok else 'Flow failed', ok)
        self.assertTrue(ok)

    def test_04_required_fields_validation(self):
        self.client.force_login(self.resident)
        before = Complaint.objects.count()
        response = self.client.post(reverse('submit_complaint'), {'category': '', 'location': '', 'description': ''})
        ok = response.status_code == 200 and Complaint.objects.count() == before and 'This field is required' in response.content.decode()
        record('TC-04', 'SOP 2', 'Usability', 'Empty complaint form is rejected with messages',
               'Nothing saved, required field messages shown',
               'Nothing saved, required field messages shown' if ok else 'Validation missing', ok)
        self.assertTrue(ok)

    def test_05_account_required_to_file(self):
        before = Complaint.objects.count()
        data = {'category': self.category.id, 'location': 'Main road near the chapel',
                'description': 'Streetlight has been broken for a week, the area is very dark at night.'}
        visitor = Client().post(reverse('submit_complaint'), data)
        old_link = Client().get('/anonymous/')
        ok = (visitor.status_code == 302 and 'login' in visitor['Location'] and old_link.status_code == 404
              and Complaint.objects.count() == before)
        record('TC-05', 'SOP 2', 'Security', 'Complaints can only be filed with a resident account',
               'Visitor without an account is sent to log in, nothing saved, no anonymous form',
               'Visitor redirected to login, nothing saved, old anonymous link returns 404' if ok else 'Filing without an account was possible', ok)
        self.assertTrue(ok)

    def test_06_tracking_by_code(self):
        complaint = Complaint.objects.create(submitted_by=self.resident, category=self.category,
                                             description='Uncollected garbage for three days', location='Street 7')
        self.client.force_login(self.resident)
        own, ms = timed(lambda: self.client.get(reverse('track_complaint'), {'code': complaint.tracking_code.lower()}))
        visitor = Client().get(reverse('track_complaint'), {'code': complaint.tracking_code})
        wrong = self.client.get(reverse('track_complaint'), {'code': 'ZZZZ9999'})
        ok = (own.status_code == 200 and complaint.tracking_code in own.content.decode()
              and visitor.status_code == 302 and 'login' in visitor['Location']
              and 'No complaint found' in wrong.content.decode())
        record('TC-06', 'SOP 2', 'Functional Suitability', 'Track a complaint by its code',
               'Owner sees the complaint, visitor is asked to log in, wrong code gives a clear message',
               'Found (code not case sensitive), visitor sent to login, wrong code shows message' if ok else 'Tracking failed', ok, ms)
        self.assertTrue(ok)

@override_settings(**LOCMEM)
class ManagementAndReportTests(TestCase):
    """SOP 3: officials managing and monitoring, report accuracy."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin2@example.com', role='admin')
        cls.resident = make_user('resident2@example.com')
        cls.cats = [Category.objects.create(name=n) for n in ['Noise', 'Garbage', 'Drainage', 'Road']]
        statuses = ['pending'] * 7 + ['under_review'] * 5 + ['in_progress'] * 4 + ['resolved'] * 6 + ['rejected'] * 3
        cls.expected = {s: statuses.count(s) for s in set(statuses)}
        cls.expected_cat = {}
        now = timezone.now()
        for index, status in enumerate(statuses):
            cat = cls.cats[index % 4]
            cls.expected_cat[cat.name] = cls.expected_cat.get(cat.name, 0) + 1
            c = Complaint.objects.create(submitted_by=cls.resident, category=cat, status=status,
                                         description=f'Test complaint number {index} with enough detail',
                                         location=f'Street {index}')
            if status == 'resolved':
                Complaint.objects.filter(id=c.id).update(
                    created_at=now - timedelta(hours=10 * (index + 1)), resolved_at=now)
        cls.total = len(statuses)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_07_dashboard_counts(self):
        response, ms = timed(lambda: self.client.get(reverse('admin_dashboard')))
        stats = response.context['stats']
        shown = {s: stats[s] for s in ['pending', 'under_review', 'in_progress', 'resolved', 'rejected']}
        ok = shown == self.expected and response.context['total'] == self.total
        record('TC-07', 'SOP 3', 'Functional Suitability', 'Dashboard status counts match the records',
               f'Total {self.total}, by status {dict(sorted(self.expected.items()))}',
               f'Total {response.context["total"]}, by status {dict(sorted(shown.items()))}', ok, ms)
        self.assertTrue(ok)

    def test_08_category_chart(self):
        response = self.client.get(reverse('admin_dashboard'))
        chart = {row['name']: row['count'] for row in json.loads(response.context['category_data'])}
        ok = chart == self.expected_cat
        record('TC-08', 'SOP 3', 'Functional Suitability', 'Complaints per category chart is accurate',
               str(dict(sorted(self.expected_cat.items()))), str(dict(sorted(chart.items()))), ok)
        self.assertTrue(ok)

    def test_09_monthly_chart_months(self):
        problems = []
        for day in [datetime(2026, 3, 1, 10), datetime(2026, 3, 31, 10), datetime(2026, 5, 31, 10), datetime(2026, 11, 5, 10)]:
            fake_now = timezone.make_aware(day)
            with mock.patch('complaints.views.timezone.now', return_value=fake_now):
                response = self.client.get(reverse('admin_dashboard'))
            months = [row['month'] for row in json.loads(response.context['monthly_data'])]
            expected = []
            year, month = day.year, day.month
            for _ in range(6):
                expected.insert(0, datetime(year, month, 1).strftime('%b'))
                month -= 1
                if month == 0:
                    year, month = year - 1, 12
            if months != expected:
                problems.append(f'{day:%b %d}: got {months}, expected {expected}')
        ok = not problems
        record('TC-09', 'SOP 3', 'Functional Suitability', 'Monthly trend chart shows 6 consecutive months',
               'Each of the last 6 months appears once, in order',
               'Correct on all tested dates' if ok else '; '.join(problems), ok)
        self.assertTrue(ok, problems)

    def test_10_excel_export(self):
        import openpyxl
        response, ms = timed(lambda: self.client.get(reverse('export_complaints_excel')))
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        header = [c.value for c in sheet[1]]
        status_col = header.index('Status')
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        codes = {r[0] for r in rows}
        db_codes = set(Complaint.objects.values_list('tracking_code', flat=True))
        statuses_ok = all(r[status_col] == Complaint.objects.get(tracking_code=r[0]).get_status_display() for r in rows)
        ok = len(rows) == self.total and codes == db_codes and statuses_ok
        record('TC-10', 'SOP 3', 'Functional Suitability', 'Excel report matches the database',
               f'{self.total} rows, same codes and statuses', f'{len(rows)} rows, codes and statuses match' if ok else f'{len(rows)} rows, mismatch', ok, ms)
        self.assertTrue(ok)

    def test_11_csv_export(self):
        import csv
        response, ms = timed(lambda: self.client.get(reverse('export_complaints_csv')))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        data_rows = [r for r in rows[1:] if r]
        ok = len(data_rows) == self.total
        record('TC-11', 'SOP 3', 'Functional Suitability', 'CSV report matches the database',
               f'{self.total} rows', f'{len(data_rows)} rows', ok, ms)
        self.assertTrue(ok)

    def test_12_pdf_export(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest('pip install pypdf to read the PDF contents')
        response, ms = timed(lambda: self.client.get(reverse('export_complaints_pdf')))
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
        listed = sum(1 for code in Complaint.objects.values_list('tracking_code', flat=True) if code in text)
        summary_ok = f'Total Complaints {self.total}' in ' '.join(text.split())
        ok = response.status_code == 200 and summary_ok and listed == self.total
        record('TC-12', 'SOP 3', 'Functional Suitability', 'PDF report summary and list are complete',
               f'Summary total {self.total}, all {self.total} complaints listed',
               f'Summary total {"correct" if summary_ok else "wrong"}, {listed} of {self.total} complaints listed', ok, ms)
        self.assertTrue(ok)

    def test_13_average_resolution_time(self):
        response = self.client.get(reverse('resolved_complaints'))
        resolved = Complaint.objects.filter(status='resolved')
        manual = sum((c.resolved_at - c.created_at).total_seconds() for c in resolved) / resolved.count() / 3600
        shown = response.context['avg_resolution_hours']
        ok = abs(shown - round(manual, 1)) < 0.11
        record('TC-13', 'SOP 3', 'Functional Suitability', 'Average resolution time is computed correctly',
               f'{manual:.1f} hours (computed by hand)', f'{shown} hours shown', ok)
        self.assertTrue(ok)

    def test_14_rejected_id_cannot_file(self):
        mail.outbox = []
        user = make_user('fakeid@example.com')
        User.objects.filter(id=user.id).update(residency_status='pending')
        no_note = self.client.post(reverse('review_resident', args=[user.id]), {'decision': 'rejected'})
        user.refresh_from_db()
        still_pending = user.residency_status == 'pending'
        self.client.post(reverse('review_resident', args=[user.id]),
                         {'decision': 'rejected', 'note': 'The ID photo is blurred, please upload a clear copy.'})
        user.refresh_from_db()
        emailed = any('blurred' in m.body and m.to == ['fakeid@example.com'] for m in mail.outbox)
        resident = Client()
        resident.force_login(user)
        before = Complaint.objects.count()
        attempt = resident.post(reverse('submit_complaint'), {'category': self.cats[0].id, 'location': 'Street 1',
                                                              'description': 'Trying to file with a rejected account.'})
        ok = (no_note.status_code == 302 and still_pending and user.residency_status == 'rejected' and emailed
              and attempt.status_code == 403 and Complaint.objects.count() == before)
        record('TC-14', 'SOP 3', 'Security', 'Resident with a rejected ID cannot file complaints',
               'Rejecting needs a reason, the resident is emailed the reason, filing is refused',
               'Reason required, email with reason sent, filing refused (403), nothing saved' if ok else 'Rejected resident could still file', ok)
        self.assertTrue(ok)


@override_settings(**LOCMEM)
class RecordsAndSpeedTests(TestCase):
    """SOP 4: proper recording and quick access (within 3 seconds)."""

    N = 5000

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin3@example.com', role='admin')
        cls.resident = make_user('resident3@example.com')
        cls.cat = Category.objects.create(name='Noise')
        start = time.perf_counter()
        for index in range(cls.N):
            Complaint.objects.create(submitted_by=cls.resident, category=cls.cat,
                                     description=f'Sample complaint {index} about noise and garbage in the area',
                                     location=f'Block {index % 50} Lot {index}')
        cls.seed_seconds = time.perf_counter() - start

    def test_15_unique_tracking_codes(self):
        codes = list(Complaint.objects.values_list('tracking_code', flat=True))
        well_formed = all(len(c) == 8 and c.isalnum() and c.upper() == c for c in codes)
        ok = len(codes) == len(set(codes)) == self.N and well_formed
        record('TC-15', 'SOP 4', 'Reliability', f'Tracking codes are unique across {self.N:,} complaints',
               f'{self.N:,} unique 8 character codes', f'{len(set(codes)):,} unique codes, all well formed' if ok else 'Duplicates or bad format', ok)
        self.assertTrue(ok)

    def test_16_retrieval_by_code_speed(self):
        client = Client()
        client.force_login(self.admin)
        codes = list(Complaint.objects.order_by('?').values_list('tracking_code', flat=True)[:100])
        times = []
        for code in codes:
            response, ms = timed(lambda: client.get(reverse('track_complaint'), {'code': code}))
            assert response.status_code == 200 and code in response.content.decode()
            times.append(ms)
        worst = max(times)
        ok = worst < 3000
        record('TC-16', 'SOP 4', 'Performance Efficiency', f'Find a complaint by tracking code ({self.N:,} records)',
               'Each lookup under 3 seconds', f'100 lookups: average {statistics.mean(times):.0f} ms, slowest {worst:.0f} ms', ok, statistics.mean(times))
        self.assertTrue(ok)

    def test_17_admin_search_speed(self):
        client = Client()
        client.force_login(self.admin)
        target = Complaint.objects.get(location='Block 21 Lot 4321')
        response, ms = timed(lambda: client.get(reverse('pending_complaints'), {'search': 'Lot 4321'}))
        listed = [c.tracking_code for c in response.context['complaints']]
        ok = response.status_code == 200 and ms < 3000 and target.tracking_code in listed
        record('TC-17', 'SOP 4', 'Performance Efficiency', f'Admin search across {self.N:,} records',
               'Matching record found in under 3 seconds', f'Found in {ms:.0f} ms', ok, ms)
        self.assertTrue(ok)

    def test_18_dashboard_load_speed(self):
        client = Client()
        client.force_login(self.admin)
        response, ms = timed(lambda: client.get(reverse('admin_dashboard')))
        ok = response.status_code == 200 and ms < 3000
        record('TC-18', 'SOP 4', 'Performance Efficiency', f'Admin dashboard load with {self.N:,} records',
               'Loads in under 3 seconds', f'Loaded in {ms:.0f} ms', ok, ms)
        self.assertTrue(ok)

    def test_19_export_speed(self):
        client = Client()
        client.force_login(self.admin)
        response, ms = timed(lambda: client.get(reverse('export_complaints_excel')))
        ok = response.status_code == 200 and ms < 10000
        record('TC-19', 'SOP 4', 'Performance Efficiency', f'Excel export of {self.N:,} records',
               'Report generated in under 10 seconds', f'Generated in {ms / 1000:.1f} s', ok, ms)
        self.assertTrue(ok)


@override_settings(**LOCMEM)
class CommunicationTests(TestCase):
    """SOP 5: status updates and feedback reach the resident."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin4@example.com', role='admin')
        cls.resident = make_user('resident4@example.com')
        cls.cat = Category.objects.create(name='Garbage')

    def setUp(self):
        mail.outbox = []
        self.resident_client = Client()
        self.resident_client.force_login(self.resident)
        self.resident_client.post(reverse('submit_complaint'), {
            'category': self.cat.id, 'location': 'Street 9',
            'description': 'Garbage has piled up beside the covered court for a week now.'})
        self.complaint = Complaint.objects.latest('id')
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)

    def update(self, status, feedback='', reason='outside_jurisdiction'):
        data = {'status': status, 'feedback': feedback}
        if status == 'rejected':
            data['rejection_reason'] = reason
        return self.admin_client.post(reverse('update_status', args=[self.complaint.id]),
                                      data, HTTP_REFERER='/admin-dashboard/')

    def test_20_status_history_timeline(self):
        for status in ['under_review', 'in_progress']:
            self.update(status)
        self.update('resolved', 'Garbage collected on Saturday morning.')
        history = list(self.complaint.status_history.order_by('created_at').values_list('status', flat=True))
        stamped = all(h.created_at and h.changed_by for h in self.complaint.status_history.all())
        ok = history == ['pending', 'under_review', 'in_progress', 'resolved'] and stamped
        record('TC-20', 'SOP 5', 'Reliability', 'Every status change is kept in the history with a timestamp',
               'pending, under review, in progress, resolved, each with time and user',
               ', '.join(history) + (', all timestamped' if stamped else ', missing timestamps'), ok)
        self.assertTrue(ok)

    def test_21_email_notifications(self):
        got_confirmation = wait_for_mail(1)
        self.update('in_progress')
        got_update = wait_for_mail(2)
        subjects = [m.subject for m in mail.outbox]
        ok = got_confirmation and got_update and any('In Progress' in s for s in subjects) \
            and all(m.to == [self.resident.email] for m in mail.outbox)
        record('TC-21', 'SOP 5', 'Functional Suitability', 'Resident is emailed on submission and status change',
               '1 confirmation email and 1 status update email to the resident',
               f'{len(mail.outbox)} emails sent: ' + ' | '.join(subjects), ok)
        self.assertTrue(ok)

    def test_22_live_status_update(self):
        url = reverse('complaint_status_api', args=[self.complaint.tracking_code])
        before = self.resident_client.get(url).json()
        self.update('under_review')
        after, ms = timed(lambda: self.resident_client.get(url).json())
        ok = before['status'] == 'pending' and after['status'] == 'under_review' and before['version'] != after['version']
        record('TC-22', 'SOP 5', 'Functional Suitability', "Resident's open tracking page detects the change",
               f'Change detected on the next check (every {settings.STATUS_POLL_SECONDS} s)',
               f"Status changed from {before['status_display']} to {after['status_display']} on the next check", ok, ms)
        self.assertTrue(ok)

    def test_23_feedback_visible(self):
        self.update('rejected', 'Location is outside Barangay 10, please report to Barangay 11.')
        page = self.resident_client.get(reverse('track_complaint'), {'code': self.complaint.tracking_code}).content.decode()
        ok = 'Reason for Rejection' in page and 'outside Barangay 10' in page
        record('TC-23', 'SOP 5', 'Usability', 'Rejection reason and resolution notes are shown to the resident',
               'Barangay feedback visible on the tracking page',
               'Rejection reason shown on tracking page' if ok else 'Feedback not shown', ok)
        self.assertTrue(ok)


@override_settings(**LOCMEM)
class SecurityTests(TestCase):
    """ISO 25010 Security: confidentiality, integrity, authenticity."""

    @classmethod
    def setUpTestData(cls):
        cls.resident = make_user('resident5@example.com')
        cls.other = make_user('other5@example.com')
        cls.admin = make_user('admin5@example.com', role='admin')
        cls.captain = make_user('captain5@example.com', role='captain')
        cls.cat = Category.objects.create(name='Noise')
        cls.complaint = Complaint.objects.create(submitted_by=cls.resident, category=cls.cat,
                                                 description='Private complaint details', location='House 5')

    def test_24_role_based_access(self):
        admin_pages = ['admin_dashboard', 'pending_complaints', 'resolved_complaints', 'rejected_complaints',
                       'resident_master_list', 'manage_categories', 'export_complaints_excel', 'export_complaints_csv',
                       'export_complaints_pdf']
        captain_pages = ['captain_dashboard', 'manage_users', 'audit_log', 'create_admin']
        blocked = []
        anon, resident, admin, captain = Client(), Client(), Client(), Client()
        resident.force_login(self.resident)
        admin.force_login(self.admin)
        captain.force_login(self.captain)
        for name in admin_pages + captain_pages:
            for who, client in [('visitor', anon), ('resident', resident)]:
                if client.get(reverse(name)).status_code == 200:
                    blocked.append(f'{who} opened {name}')
        for name in captain_pages:
            if admin.get(reverse(name)).status_code == 200:
                blocked.append(f'admin opened {name}')
        allowed = all(admin.get(reverse(n)).status_code == 200 for n in admin_pages) and \
            all(captain.get(reverse(n)).status_code == 200 for n in captain_pages)
        ok = not blocked and allowed
        record('TC-24', 'Security', 'Security', 'Role based access (Resident, Admin, Captain)',
               f'{len(admin_pages)} admin and {len(captain_pages)} captain pages blocked for others, open for the right role',
               'All restricted pages blocked for other roles, open for the right role' if ok else '; '.join(blocked) or 'Right role blocked', ok)
        self.assertTrue(ok)

    def test_25_residents_cannot_see_each_other(self):
        other = Client()
        other.force_login(self.other)
        page = other.get(reverse('track_complaint'), {'code': self.complaint.tracking_code}).content.decode()
        api = other.get(reverse('complaint_status_api', args=[self.complaint.tracking_code]))
        ok = 'Private complaint details' not in page and api.status_code == 403
        record('TC-25', 'Security', 'Security', "A resident cannot open another resident's complaint",
               'Details hidden, status check refused', 'Details hidden, status check returned 403' if ok else 'Data exposed', ok)
        self.assertTrue(ok)

    def test_26_unverified_cannot_login(self):
        make_user('unverified@example.com', verified=False)
        response = Client().post(reverse('login'), {'username': 'unverified@example.com', 'password': PASSWORD})
        ok = response.status_code == 302 and 'verify' in response['Location']
        record('TC-26', 'Security', 'Security', 'Unverified accounts cannot log in',
               'Sent to email verification instead of logging in', 'Redirected to verification page' if ok else 'Logged in', ok)
        self.assertTrue(ok)

    def test_27_passwords_hashed(self):
        stored = User.objects.get(email='resident5@example.com').password
        ok = stored.startswith('pbkdf2_sha256$') and PASSWORD not in stored
        record('TC-27', 'Security', 'Security', 'Passwords are stored hashed, not as plain text',
               'Only a PBKDF2 hash is stored', f"Stored as {stored.split('$')[0]} hash" if ok else 'Plain text found', ok)
        self.assertTrue(ok)

    def test_28_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse('update_status', args=[self.complaint.id]), {'status': 'resolved'})
        self.complaint.refresh_from_db()
        ok = response.status_code == 403 and self.complaint.status == 'pending'
        record('TC-28', 'Security', 'Security', 'Forged status change request is refused (CSRF)',
               'Request without a security token refused, record unchanged', f'Refused with {response.status_code}, status unchanged' if ok else 'Accepted', ok)
        self.assertTrue(ok)

    def test_29_audit_log(self):
        admin = Client()
        admin.force_login(self.admin)
        admin.post(reverse('update_status', args=[self.complaint.id]), {'status': 'in_progress'}, HTTP_REFERER='/')
        entry = AuditLog.objects.filter(user=self.admin, action='update_status').first()
        ok = entry is not None and self.complaint.tracking_code in entry.details
        record('TC-29', 'Security', 'Security', 'Status changes are written to the audit log',
               'Log entry with the admin, the action and the tracking code',
               f'Logged: {entry.details}' if entry else 'No log entry', ok)
        self.assertTrue(ok)


@override_settings(**LOCMEM)
class MaintainabilityTests(TestCase):
    """SOP Maintainability: changes without editing code."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('admin6@example.com', role='admin')
        cls.resident = make_user('resident6@example.com')

    def test_30_add_category_without_code(self):
        admin = Client()
        admin.force_login(self.admin)
        admin.post(reverse('manage_categories'), {'action': 'add', 'name': 'Illegal Parking', 'description': 'New'})
        resident = Client()
        resident.force_login(self.resident)
        form = resident.get(reverse('submit_complaint')).content.decode()
        ok = 'Illegal Parking' in form
        record('TC-30', 'Maintainability', 'Maintainability', 'New complaint category added from the admin page',
               'Category appears in the complaint form with no code change',
               'Category available to residents right away' if ok else 'Not available', ok)
        self.assertTrue(ok)

    def test_31_barangay_name_setting(self):
        with self.settings(BARANGAY_NAME='Barangay 99'):
            page = Client().get(reverse('home')).content.decode()
        ok = 'Barangay 99' in page and 'Barangay 10' not in page
        record('TC-31', 'Maintainability', 'Maintainability', 'Barangay name changed from one setting',
               'Whole site shows the new name', 'All pages use the setting' if ok else 'Name hardcoded somewhere', ok)
        self.assertTrue(ok)


@override_settings(**LOCMEM)
class PanelRevisionTests(TestCase):
    """Features added from the panel revisions (Gonzales, Deuna, Cabug)."""

    @classmethod
    def setUpTestData(cls):
        cls.noise = Category.objects.create(name='Noise', estimated_days=3,
                                            recommended_action='Schedule tanod patrols at night.')
        cls.garbage = Category.objects.create(name='Garbage', estimated_days=5)
        cls.admin = make_user('admin9@example.com', role='admin')
        cls.resident = make_user('resident9@example.com')

    def setUp(self):
        mail.outbox = []
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)
        self.resident_client = Client()
        self.resident_client.force_login(self.resident)

    def file_complaint(self, client=None, **extra):
        data = {'category': self.noise.id, 'location': 'Street 4 near the chapel',
                'description': 'Loud karaoke from the bar on Street 4 every night until 3 in the morning.'}
        data.update(extra)
        (client or self.resident_client).post(reverse('submit_complaint'), data)
        return Complaint.objects.latest('id')

    def test_32_resident_id_verification(self):
        missing = Client().post(reverse('register'), {
            'email': 'noid@example.com', 'first_name': 'Ana', 'last_name': 'Santos',
            'password1': PASSWORD, 'password2': PASSWORD})
        missing_ok = missing.status_code == 200 and not User.objects.filter(email='noid@example.com').exists()
        Client().post(reverse('register'), registration_data('withid@example.com'))
        user = User.objects.get(email='withid@example.com')
        user.is_verified = user.is_active = True
        user.save()
        client = Client()
        client.force_login(user)
        before = Complaint.objects.count()
        blocked = client.post(reverse('submit_complaint'), {'category': self.noise.id, 'location': 'Street 1',
                                                            'description': 'Dog barking all night beside our house.'})
        blocked_ok = blocked.status_code == 403 and Complaint.objects.count() == before
        listed = self.admin_client.get(reverse('resident_master_list'), {'status': 'pending'})
        id_page = self.admin_client.get(reverse('view_valid_id', args=[user.id]))
        resident_cannot_see_id = self.resident_client.get(reverse('view_valid_id', args=[user.id])).status_code == 302
        self.admin_client.post(reverse('review_resident', args=[user.id]), {'decision': 'approved'})
        user.refresh_from_db()
        allowed = client.post(reverse('submit_complaint'), {'category': self.noise.id, 'location': 'Street 1',
                                                            'description': 'Dog barking all night beside our house.'})
        ok = (missing_ok and user.birth_date is not None and bool(user.valid_id) and blocked_ok
              and 'withid@example.com' in listed.content.decode() and id_page.status_code == 200
              and resident_cannot_see_id and user.residency_status == 'approved' and user.residency_reviewed_by == self.admin
              and allowed.status_code == 302 and Complaint.objects.count() == before + 1)
        record('TC-32', 'SOP 2', 'Security', 'Registration needs birthday, address and valid ID; staff verify residency',
               'No ID, no account; unverified resident cannot file; staff verify from the master list, then filing works',
               'Missing ID rejected, pending resident blocked (403), ID visible to staff only, filing allowed after verification'
               if ok else 'Verification flow failed', ok)
        self.assertTrue(ok)

    def test_33_video_and_audio_evidence(self):
        video = SimpleUploadedFile('noise.mp4', b'\x00\x00\x00\x18ftypmp42' + bytes(2048), content_type='video/mp4')
        complaint = self.file_complaint(attachment=video)
        audio = SimpleUploadedFile('noise.m4a', bytes(1024), content_type='audio/mp4')
        voice = self.file_complaint(attachment=audio, description='Loud motorcycle mufflers every midnight.')
        before = Complaint.objects.count()
        bad = SimpleUploadedFile('virus.exe', b'MZ' + bytes(100), content_type='application/octet-stream')
        rejected = self.resident_client.post(reverse('submit_complaint'), {
            'category': self.noise.id, 'location': 'Street 4', 'description': 'Testing a file that is not allowed.',
            'attachment': bad})
        page = self.resident_client.get(reverse('track_complaint'), {'code': complaint.tracking_code}).content.decode()
        ok = (complaint.attachment_kind == 'video' and '<video' in page
              and voice.attachment_kind == 'audio' and voice.id != complaint.id
              and rejected.status_code == 200 and Complaint.objects.count() == before)
        record('TC-33', 'SOP 2', 'Functional Suitability', 'Complaints accept video and audio evidence, not other files',
               'Video and audio saved and playable; other file types refused',
               'mp4 and m4a saved, video player shown, exe refused with a message' if ok else 'Attachment handling failed', ok)
        self.assertTrue(ok)

    def test_34_walk_in_complaint(self):
        response, ms = timed(lambda: self.admin_client.post(reverse('walk_in_complaint'), {
            'complainant_name': 'Lola Remedios Cruz', 'complainant_contact': '45 Mabini St.',
            'category': self.garbage.id, 'location': 'Mabini Street corner Rizal',
            'description': 'Garbage not collected for one week, smell reaches the houses.'}))
        complaint = Complaint.objects.filter(source='walk_in').first()
        public = Client().get(reverse('track_complaint'), {'code': complaint.tracking_code}) if complaint else None
        ok = (response.status_code == 302 and complaint is not None and complaint.complainant_name == 'Lola Remedios Cruz'
              and complaint.status_history.count() == 1 and public.status_code == 200
              and 'Lola Remedios Cruz' in public.content.decode()
              and AuditLog.objects.filter(action='walk_in', user=self.admin).exists())
        record('TC-34', 'SOP 1', 'Functional Suitability', 'Staff encode a walk-in complaint for a resident without internet',
               'Saved with a tracking code the complainant can use without an account',
               f'Saved as {complaint.tracking_code}, trackable without login, logged in audit trail' if ok else 'Walk-in failed', ok, ms)
        self.assertTrue(ok)

    def test_35_rejection_reason_and_response_days(self):
        complaint = self.file_complaint()
        no_reason = self.admin_client.post(reverse('update_status', args=[complaint.id]), {'status': 'rejected'},
                                           HTTP_REFERER='/admin-dashboard/')
        complaint.refresh_from_db()
        still_pending = complaint.status == 'pending'
        Complaint.objects.filter(id=complaint.id).update(created_at=timezone.now() - timedelta(days=2))
        self.admin_client.post(reverse('update_status', args=[complaint.id]), {
            'status': 'rejected', 'rejection_reason': 'outside_jurisdiction',
            'feedback': 'The bar is in Barangay 11. Please report it there.'}, HTTP_REFERER='/admin-dashboard/')
        complaint.refresh_from_db()
        page = self.resident_client.get(reverse('track_complaint'), {'code': complaint.tracking_code}).content.decode()
        ok = (still_pending and no_reason.status_code == 302 and complaint.status == 'rejected'
              and complaint.rejection_reason == 'outside_jurisdiction' and complaint.response_days == 2.0
              and 'Outside Barangay 10 jurisdiction' in page and '2.0 days' in page)
        record('TC-35', 'SOP 5', 'Functional Suitability', 'Rejection needs a reason; response time in days is shown',
               'Rejecting without a reason is refused; reason and days shown to the resident',
               f'Refused without reason; saved with reason, answered in {complaint.response_days} days' if ok else 'Reason or days missing', ok)
        self.assertTrue(ok)

    def test_36_satisfaction_feedback(self):
        complaint = self.file_complaint()
        self.admin_client.post(reverse('update_status', args=[complaint.id]),
                               {'status': 'resolved', 'feedback': 'Tanod talked to the owner.'}, HTTP_REFERER='/x/')
        other = make_user('other9@example.com')
        intruder = Client()
        intruder.force_login(other)
        intruder.post(reverse('submit_feedback', args=[complaint.tracking_code]), {'satisfaction': 'not_satisfied'})
        complaint.refresh_from_db()
        intruder_blocked = complaint.satisfaction == ''
        self.resident_client.post(reverse('submit_feedback', args=[complaint.tracking_code]),
                                  {'satisfaction': 'satisfied', 'satisfaction_comment': 'Quiet now, thank you.'})
        self.resident_client.post(reverse('submit_feedback', args=[complaint.tracking_code]), {'satisfaction': 'not_satisfied'})
        complaint.refresh_from_db()
        dashboard = self.admin_client.get(reverse('admin_dashboard'))
        ok = (intruder_blocked and complaint.satisfaction == 'satisfied' and complaint.satisfaction_comment
              and dashboard.context['satisfaction']['rate'] == 100)
        record('TC-36', 'SOP 5', 'Functional Suitability', 'Complainant rates the result as satisfied or not satisfied',
               'One rating by the complainant only, summarized on the dashboard',
               'Rating saved once, other users blocked, dashboard shows 100% satisfied' if ok else 'Feedback failed', ok)
        self.assertTrue(ok)

    def test_37_estimated_resolution_time(self):
        complaint = self.file_complaint()
        success = self.resident_client.get(reverse('complaint_success', args=[complaint.tracking_code])).content.decode()
        page = self.resident_client.get(reverse('track_complaint'), {'code': complaint.tracking_code}).content.decode()
        expected = timezone.localtime(complaint.expected_by).strftime('%B %d, %Y')
        Complaint.objects.filter(id=complaint.id).update(created_at=timezone.now() - timedelta(days=5))
        complaint.refresh_from_db()
        ok = (complaint.estimated_days == 3 and '3 days' in success and expected in page and complaint.is_overdue)
        record('TC-37', 'SOP 5', 'Usability', 'Complainant sees the estimated time to act on the complaint',
               'Estimate from the category shown after filing and on tracking; late complaints flagged',
               f'Within 3 days, by {expected} shown; flagged as past the usual time after 5 days' if ok else 'Estimate missing', ok)
        self.assertTrue(ok)

    def test_38_recommendations_from_top_complaints(self):
        for _ in range(3):
            self.file_complaint()
        self.file_complaint(category=self.garbage.id, location='Market area',
                            description='Garbage piling up near the public market stalls.')
        response, ms = timed(lambda: self.admin_client.get(reverse('admin_dashboard')))
        recs = response.context['recommendations']
        ok = (response.status_code == 200 and recs and recs[0]['category'] == 'Noise' and recs[0]['count'] == 3
              and recs[0]['share'] == 75 and 'tanod patrols' in recs[0]['action']
              and 'Street 4 near the chapel' in recs[0]['top_location'] and 'Recommended Actions' in response.content.decode())
        record('TC-38', 'SOP 3', 'Functional Suitability', 'Dashboard recommends actions for the most reported complaints',
               'Top category ranked with its share, top location and suggested action',
               f"Noise ranked first (3 of 4, 75%), top location and action shown" if ok else 'Recommendation wrong', ok, ms)
        self.assertTrue(ok)

    def test_39_report_signatories_and_master_list_extract(self):
        from pypdf import PdfReader
        import openpyxl
        self.file_complaint()
        pdf = self.admin_client.get(reverse('export_complaints_pdf'))
        text = ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
        xlsx = self.admin_client.get(reverse('export_residents_excel'))
        sheet = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
        emails = [row[7] for row in sheet.iter_rows(min_row=5, values_only=True)]
        ok = ('Prepared by' in text and 'Noted by' in text and 'Punong Barangay' in text
              and 'Recommended Actions' in text and self.resident.email in emails)
        record('TC-39', 'SOP 3', 'Functional Suitability', 'Report has signatories; resident master list can be extracted',
               'PDF shows Prepared by and Noted by lines; Excel extract lists the residents',
               'Signature lines and recommendations in the PDF, residents listed in the Excel extract' if ok else 'Missing', ok)
        self.assertTrue(ok)

    def test_40_complaint_background_and_history(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        before = Complaint.objects.count()
        refused = self.resident_client.post(reverse('submit_complaint'), {
            'category': self.noise.id, 'location': 'Street 4', 'incident_date': future,
            'description': 'Loud karaoke from the bar on Street 4 every night until 3 in the morning.'})
        future_refused = refused.status_code == 200 and Complaint.objects.count() == before
        first = self.file_complaint(incident_date='2026-03-01', respondent_name='Owner of Kanto Bar')
        second = self.file_complaint(category=self.garbage.id, description='Garbage dumped on the vacant lot again.')
        detail = self.admin_client.get(reverse('complaint_detail', args=[second.id])).content.decode()
        ok = (future_refused and str(first.incident_date) == '2026-03-01' and first.respondent_name == 'Owner of Kanto Bar'
              and first.tracking_code in detail and "Complainant's History" in detail)
        record('TC-40', 'SOP 1', 'Functional Suitability', 'Complaint records the incident date and person involved; staff see past complaints',
               'Background saved (future dates refused); complainant history listed on the record',
               'Incident date and respondent saved, future date refused, earlier complaint listed in history' if ok else 'Failed', ok)
        self.assertTrue(ok)
