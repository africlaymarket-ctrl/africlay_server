from django.db import migrations


SECURE_SMS_TABLE_SQL = r"""
DO $secure_sms_table$
DECLARE
    api_role text;
BEGIN
    ALTER TABLE public.notifications_smsdelivery ENABLE ROW LEVEL SECURITY;
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.notifications_smsdelivery FROM %I', api_role);
        END IF;
    END LOOP;
END
$secure_sms_table$;
"""


def secure_sms_table(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_SMS_TABLE_SQL)


class Migration(migrations.Migration):
    dependencies = [('notifications', '0005_smsdelivery')]

    operations = [migrations.RunPython(secure_sms_table, reverse_code=migrations.RunPython.noop)]
