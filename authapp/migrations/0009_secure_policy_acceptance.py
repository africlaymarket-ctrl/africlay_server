from django.db import migrations


SECURE_POLICY_ACCEPTANCE_SQL = r"""
DO $secure_policy_acceptance$
DECLARE
    api_role text;
BEGIN
    ALTER TABLE public.authapp_policyacceptance ENABLE ROW LEVEL SECURITY;

    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE public.authapp_policyacceptance FROM %I',
                api_role
            );
        END IF;
    END LOOP;
END
$secure_policy_acceptance$;
"""


def secure_policy_acceptance(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_POLICY_ACCEPTANCE_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ('authapp', '0008_secure_otp_codes'),
    ]

    operations = [
        migrations.RunPython(
            secure_policy_acceptance,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
