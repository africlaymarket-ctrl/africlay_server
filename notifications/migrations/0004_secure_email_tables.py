from django.db import migrations


SECURE_EMAIL_TABLES_SQL = r"""
DO $secure_email_tables$
DECLARE
    api_role text;
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['notifications_emaildelivery', 'notifications_emailconsentevent']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I', table_name, api_role);
            END IF;
        END LOOP;
    END LOOP;
END
$secure_email_tables$;
"""


def secure_email_tables(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_EMAIL_TABLES_SQL)


class Migration(migrations.Migration):
    dependencies = [('notifications', '0003_emailconsentevent')]

    operations = [migrations.RunPython(secure_email_tables, reverse_code=migrations.RunPython.noop)]
