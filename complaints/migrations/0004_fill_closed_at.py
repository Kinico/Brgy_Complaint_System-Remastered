from django.db import migrations
from django.db.models import F


def fill_closed_at(apps, schema_editor):
    Complaint = apps.get_model('complaints', 'Complaint')
    Complaint.objects.filter(status='resolved', closed_at__isnull=True, resolved_at__isnull=False).update(closed_at=F('resolved_at'))
    Complaint.objects.filter(status__in=['resolved', 'rejected'], closed_at__isnull=True).update(closed_at=F('updated_at'))


class Migration(migrations.Migration):
    dependencies = [('complaints', '0003_anonymouscomplaint_attachment_and_more')]
    operations = [migrations.RunPython(fill_closed_at, migrations.RunPython.noop)]
