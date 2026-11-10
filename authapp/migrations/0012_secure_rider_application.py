from django.db import migrations


SECURE_RIDER_APPLICATION_SQL = r"""
DO $secure_rider_application$
DECLARE
    api_role text;
BEGIN
    -- Riders' identity and licence records are read only through Django.
    ALTER TABLE public.authapp_riderapplication ENABLE ROW LEVEL SECURITY;
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.authapp_riderapplication FROM %I', api_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE public.authapp_riderapplication_id_seq FROM %I', api_role);
        END IF;
    END LOOP;
END
$secure_rider_application$;
"""


def secure_rider_application(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_RIDER_APPLICATION_SQL)


class Migration(migrations.Migration):
    dependencies = [('authapp', '0011_riderapplication')]
    operations = [migrations.RunPython(secure_rider_application, migrations.RunPython.noop)]
