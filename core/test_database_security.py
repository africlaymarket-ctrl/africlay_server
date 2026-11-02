from importlib import import_module
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


security_migration = import_module(
    'core.migrations.0001_secure_supabase_public_schema'
)
policy_acceptance_migration = import_module(
    'authapp.migrations.0009_secure_policy_acceptance'
)


class PublicSchemaSecurityMigrationTests(SimpleTestCase):
    def test_non_postgres_database_is_unchanged(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'sqlite'

        security_migration.secure_public_schema(None, schema_editor)

        schema_editor.connection.cursor.assert_not_called()

    def test_postgres_executes_lockdown_as_one_atomic_statement(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'postgresql'
        cursor = schema_editor.connection.cursor.return_value.__enter__.return_value

        security_migration.secure_public_schema(None, schema_editor)

        cursor.execute.assert_called_once_with(
            security_migration.SECURE_PUBLIC_SCHEMA_SQL
        )
        sql = security_migration.SECURE_PUBLIC_SCHEMA_SQL
        self.assertIn('ENABLE ROW LEVEL SECURITY', sql)
        self.assertIn("ARRAY['anon', 'authenticated']", sql)
        self.assertIn('REVOKE ALL PRIVILEGES ON TABLE', sql)
        self.assertIn('ALTER DEFAULT PRIVILEGES', sql)
        self.assertNotIn('FORCE ROW LEVEL SECURITY', sql)

    def test_policy_acceptance_migration_secures_table_created_later(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'postgresql'
        cursor = schema_editor.connection.cursor.return_value.__enter__.return_value

        policy_acceptance_migration.secure_policy_acceptance(None, schema_editor)

        cursor.execute.assert_called_once_with(
            policy_acceptance_migration.SECURE_POLICY_ACCEPTANCE_SQL
        )
        sql = policy_acceptance_migration.SECURE_POLICY_ACCEPTANCE_SQL
        self.assertIn('ALTER TABLE public.authapp_policyacceptance ENABLE ROW LEVEL SECURITY', sql)
        self.assertIn('REVOKE ALL PRIVILEGES ON TABLE public.authapp_policyacceptance', sql)
        self.assertNotIn('FORCE ROW LEVEL SECURITY', sql)

    def test_policy_acceptance_migration_skips_sqlite(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'sqlite'

        policy_acceptance_migration.secure_policy_acceptance(None, schema_editor)

        schema_editor.connection.cursor.assert_not_called()


class PublicDatabaseAccessAuditTests(SimpleTestCase):
    @patch('core.management.commands.audit_public_database_access.connection')
    def test_non_postgres_database_is_skipped(self, connection):
        connection.vendor = 'sqlite'
        output = StringIO()

        call_command('audit_public_database_access', stdout=output)

        self.assertIn('applies only to PostgreSQL', output.getvalue())
        connection.cursor.assert_not_called()

    @patch('core.management.commands.audit_public_database_access.connection')
    def test_clean_postgres_audit_succeeds(self, connection):
        connection.vendor = 'postgresql'
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], []]
        output = StringIO()

        call_command(
            'audit_public_database_access',
            '--fail-on-findings',
            stdout=output,
        )

        self.assertIn('locked down', output.getvalue())

    @patch('core.management.commands.audit_public_database_access.connection')
    def test_findings_can_fail_deployment_check(self, connection):
        connection.vendor = 'postgresql'
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [
            [('payments_payment',)],
            [('payments_payment', 'anon')],
        ]

        with self.assertRaises(CommandError):
            call_command(
                'audit_public_database_access',
                '--fail-on-findings',
                stderr=StringIO(),
            )
