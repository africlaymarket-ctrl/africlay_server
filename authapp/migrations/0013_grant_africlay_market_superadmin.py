from django.db import migrations
from django.db.models import F
from django.utils import timezone


TARGET_EMAIL = 'africlaymarket@gmail.com'


def grant_africlay_market_superadmin(apps, schema_editor):
    User = apps.get_model('authapp', 'User')
    AdminAuditLog = apps.get_model('authapp', 'AdminAuditLog')
    database = schema_editor.connection.alias

    matches = list(
        User.objects.using(database)
        .select_for_update()
        .filter(email__iexact=TARGET_EMAIL)[:2]
    )
    if len(matches) != 1:
        print(f'Superadmin grant skipped: expected one account for {TARGET_EMAIL}; found {len(matches)}.')
        return

    target = matches[0]
    if not target.is_active or not target.is_verified:
        print(f'Superadmin grant skipped: {TARGET_EMAIL} must be active and email-verified.')
        return
    if target.role == 'super_admin':
        print(f'Superadmin grant already present for {TARGET_EMAIL}.')
        return

    previous_role = target.role
    User.objects.using(database).filter(pk=target.pk).update(
        role='super_admin',
        auth_version=F('auth_version') + 1,
        updated_at=timezone.now(),
    )
    AdminAuditLog.objects.using(database).create(
        actor=None,
        action='superadmin_role_granted',
        target_type='user',
        target_id=str(target.pk),
        reason='One-time owner-authorized superadmin access grant.',
        metadata={
            'assignment_method': 'deployment_migration',
            'previous_role': previous_role,
            'new_role': 'super_admin',
            'email': TARGET_EMAIL,
        },
    )
    print(f'Superadmin grant applied to {TARGET_EMAIL}.')


class Migration(migrations.Migration):
    dependencies = [('authapp', '0012_secure_rider_application')]

    operations = [
        migrations.RunPython(grant_africlay_market_superadmin, migrations.RunPython.noop),
    ]
