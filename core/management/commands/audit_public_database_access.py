from django.core.management.base import BaseCommand, CommandError
from django.db import connection


RLS_AUDIT_SQL = """
SELECT relation.relname
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p')
  AND NOT relation.relrowsecurity
ORDER BY relation.relname;
"""


API_GRANT_AUDIT_SQL = """
SELECT relation.relname, api_role.role_name
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
CROSS JOIN (VALUES ('anon'), ('authenticated')) AS api_role(role_name)
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p')
  AND EXISTS (
      SELECT 1 FROM pg_roles WHERE rolname = api_role.role_name
  )
  AND (
      has_table_privilege(api_role.role_name, relation.oid, 'SELECT')
      OR has_table_privilege(api_role.role_name, relation.oid, 'INSERT')
      OR has_table_privilege(api_role.role_name, relation.oid, 'UPDATE')
      OR has_table_privilege(api_role.role_name, relation.oid, 'DELETE')
      OR has_table_privilege(api_role.role_name, relation.oid, 'TRUNCATE')
      OR has_table_privilege(api_role.role_name, relation.oid, 'REFERENCES')
      OR has_table_privilege(api_role.role_name, relation.oid, 'TRIGGER')
  )
ORDER BY relation.relname, api_role.role_name;
"""


class Command(BaseCommand):
    help = (
        'Audit public Postgres tables for disabled RLS or direct anon/authenticated grants. '
        'This command does not change the database.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fail-on-findings',
            action='store_true',
            help='Exit non-zero if unsafe public table access is found.',
        )

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write('Skipped: this audit applies only to PostgreSQL.')
            return

        with connection.cursor() as cursor:
            cursor.execute(RLS_AUDIT_SQL)
            rls_disabled = [row[0] for row in cursor.fetchall()]
            cursor.execute(API_GRANT_AUDIT_SQL)
            api_grants = [f'{table} ({role})' for table, role in cursor.fetchall()]

        if not rls_disabled and not api_grants:
            self.stdout.write(self.style.SUCCESS(
                'Public database access is locked down: RLS is enabled and no '
                'anon/authenticated table grants remain.'
            ))
            return

        if rls_disabled:
            self.stderr.write(
                'RLS disabled: ' + ', '.join(rls_disabled)
            )
        if api_grants:
            self.stderr.write(
                'Direct Data API grants remain: ' + ', '.join(api_grants)
            )

        if options['fail_on_findings']:
            raise CommandError('Unsafe public database access remains.')
