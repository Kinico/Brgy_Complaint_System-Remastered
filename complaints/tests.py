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
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, User
from complaints.models import AnonymousComplaint, Category, Complaint, ComplaintStatusHistory
from ml_spam.ml_models import spam_ml

RESULTS = []
LOCMEM = dict(
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


def make_user(email, role='resident', verified=True):
    user = User.objects.create_user(email=email, password=PASSWORD, first_name=email.split('@')[0].title(),
                                    last_name='Tester', role=role)
    user.is_verified = verified
    user.is_active = verified
    user.save()
    return user


def tearDownModule():
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
        response = self.client.post(reverse('register'), {
            'email': 'newresident@example.com', 'first_name': 'Juan', 'last_name': 'Dela Cruz',
            'password1': PASSWORD, 'password2': PASSWORD})
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

    def test_05_anonymous_filing(self):
        response = self.client.post(reverse('anonymous_complaint'), {
            'category': self.category.id, 'location': 'Main road near the chapel',
            'description': 'Streetlight has been broken for a week, the area is very dark at night.'})
        complaint = AnonymousComplaint.objects.first()
        ok = response.status_code == 302 and complaint is not None and len(complaint.tracking_code) == 8
        record('TC-05', 'SOP 2', 'Functional Suitability', 'Anonymous complaint without an account',
               'Saved with a tracking code, no account needed',
               f'Saved with code {complaint.tracking_code}' if complaint else 'Not saved', ok)
        self.assertTrue(ok)

    def test_06_tracking_by_code(self):
        complaint = Complaint.objects.create(submitted_by=self.resident, category=self.category,
                                             description='Uncollected garbage for three days', location='Street 7')
        anon = AnonymousComplaint.objects.create(category=self.category, description='Stray dogs biting people',
                                                 location='Corner of Street 2')
        self.client.force_login(self.resident)
        own, ms = timed(lambda: self.client.get(reverse('track_complaint'), {'code': complaint.tracking_code.lower()}))
        anon_resp = Client().get(reverse('track_complaint'), {'code': anon.tracking_code}, follow=True)
        wrong = self.client.get(reverse('track_complaint'), {'code': 'ZZZZ9999'})
        ok = (own.status_code == 200 and complaint.tracking_code in own.content.decode()
              and anon.tracking_code in anon_resp.content.decode()
              and 'No complaint found' in wrong.content.decode())
        record('TC-06', 'SOP 2', 'Functional Suitability', 'Track account and anonymous complaints by code',
               'Correct complaint shown, wrong code gives a clear message',
               'Both found (code not case sensitive), wrong code shows message' if ok else 'Tracking failed', ok, ms)
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
                                         location=f'Street {index}', is_spam=(index % 9 == 0))
            if status == 'resolved':
                Complaint.objects.filter(id=c.id).update(
                    created_at=now - timedelta(hours=10 * (index + 1)), resolved_at=now)
        cls.total = len(statuses)
        cls.spam = Complaint.objects.filter(is_spam=True).count()

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_07_dashboard_counts(self):
        response, ms = timed(lambda: self.client.get(reverse('admin_dashboard')))
        stats = response.context['stats']
        shown = {s: stats[s] for s in ['pending', 'under_review', 'in_progress', 'resolved', 'rejected']}
        ok = shown == self.expected and response.context['total'] == self.total and stats['spam'] == self.spam
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
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        codes = {r[0] for r in rows}
        db_codes = set(Complaint.objects.values_list('tracking_code', flat=True))
        statuses_ok = all(r[6] == Complaint.objects.get(tracking_code=r[0]).get_status_display() for r in rows)
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
        resolved = Complaint.objects.filter(status='resolved', is_spam=False)
        manual = sum((c.resolved_at - c.created_at).total_seconds() for c in resolved) / resolved.count() / 3600
        shown = response.context['avg_resolution_hours']
        ok = abs(shown - round(manual, 1)) < 0.11
        record('TC-13', 'SOP 3', 'Functional Suitability', 'Average resolution time is computed correctly',
               f'{manual:.1f} hours (computed by hand)', f'{shown} hours shown', ok)
        self.assertTrue(ok)

    def test_14_spam_detection(self):
        legit = [
            'Our neighbor plays loud videoke every night until 2 in the morning near Street 5.',
            'The drainage on Phase 2 is clogged and floods the road whenever it rains.',
            'Garbage has not been collected for three days and it smells very bad.',
            'A streetlight near the chapel has been broken for a week, the area is dark.',
            'Stray dogs are chasing children on the way to school along Street 3.',
            'There is a large pothole on the barangay road that already caused an accident.',
            'A vacant lot is full of tall grass and mosquitoes, please have it cleaned.',
            'Someone is burning plastic trash every morning and the smoke enters our house.',
            'Tricycles are parked on the sidewalk so pedestrians have to walk on the road.',
            'Water pipe leak in front of house 23 has been flowing for two days.',
        ]
        spam = [
            'test test test', 'asdf qwerty asdf', 'WIN BITCOIN CASINO PRIZE NOW!!!',
            'Free cash loan, click http://example.com now', 'sample complaint example only',
            'panalo ka ng premyo, libre pa', 'aaaaaaaaaaaaaaaaaa', 'LOTTERY WINNER CLAIM YOUR MONEY',
            'pautang mabilis walang collateral, cash agad', 'testing testing 123',
        ]
        tp = sum(1 for t in spam if spam_ml.predict(t)[0])
        fp = sum(1 for t in legit if spam_ml.predict(t)[0])
        accuracy = (tp + (len(legit) - fp)) / (len(legit) + len(spam)) * 100
        ok = fp == 0 and tp >= 8
        record('TC-14', 'SOP 3', 'Security', 'Spam filter on 10 real and 10 spam samples',
               'No real complaint flagged, at least 8 of 10 spam flagged',
               f'{tp} of 10 spam flagged, {fp} of 10 real complaints flagged, accuracy {accuracy:.0f}%', ok)
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

    def update(self, status, feedback=''):
        return self.admin_client.post(reverse('update_status', args=[self.complaint.id]),
                                      {'status': status, 'feedback': feedback}, HTTP_REFERER='/admin-dashboard/')

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
                       'review_spam', 'manage_categories', 'export_complaints_excel', 'export_complaints_csv',
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
