import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from authapp.models import AdminAuditLog


grant = importlib.import_module(
    'authapp.migrations.0013_grant_africlay_market_superadmin'
).grant_africlay_market_superadmin


class SuperadminGrantMigrationTests(TestCase):
    def run_grant(self):
        grant(apps, SimpleNamespace(connection=connection))

    def test_promotes_only_the_exact_verified_account_and_audits_once(self):
        User = get_user_model()
        target = User.objects.create_user(
            'africlaymarket@gmail.com', 'TestPassword123!', is_verified=True,
        )
        other = User.objects.create_user(
            'other@example.com', 'TestPassword123!', is_verified=True,
        )

        self.run_grant()
        target.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(target.role, 'super_admin')
        self.assertEqual(target.auth_version, 1)
        self.assertTrue(target.is_super_admin)
        self.assertFalse(target.is_superuser)
        self.assertFalse(target.is_staff)
        self.assertEqual(other.role, 'buyer')
        self.assertEqual(other.auth_version, 0)
        self.assertEqual(
            AdminAuditLog.objects.filter(
                action='superadmin_role_granted', target_id=str(target.pk),
            ).count(),
            1,
        )

        self.run_grant()
        target.refresh_from_db()
        self.assertEqual(target.auth_version, 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='superadmin_role_granted').count(), 1)

    def test_does_not_promote_unverified_or_inactive_accounts(self):
        User = get_user_model()
        target = User.objects.create_user('africlaymarket@gmail.com', 'TestPassword123!')
        self.run_grant()
        target.refresh_from_db()
        self.assertEqual(target.role, 'buyer')

        target.is_verified = True
        target.is_active = False
        target.save(update_fields=['is_verified', 'is_active'])
        self.run_grant()
        target.refresh_from_db()
        self.assertEqual(target.role, 'buyer')
        self.assertFalse(AdminAuditLog.objects.filter(action='superadmin_role_granted').exists())
