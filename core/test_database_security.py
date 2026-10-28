from importlib import import_module
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


security_migration = import_module(
    'core.migrations.0001_secure_supabase_public_schema'
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
