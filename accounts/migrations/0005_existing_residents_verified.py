from django.db import migrations


def mark_existing_residents(apps, schema_editor):
    """Accounts made before ID verification existed stay able to file complaints.
    Staff can still review them in the Resident Master List."""
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='resident', is_verified=True).update(residency_status='approved')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0004_user_address_user_birth_date_user_contact_number_and_more')]
    operations = [migrations.RunPython(mark_existing_residents, migrations.RunPython.noop)]
