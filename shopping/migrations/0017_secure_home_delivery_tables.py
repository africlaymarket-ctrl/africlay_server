from django.db import migrations


def secure_home_delivery_tables(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(r"""
DO $secure_home_delivery$
DECLARE
    table_name text;
    api_role text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['shopping_homedeliveryleg', 'shopping_homedeliveryevent']
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
$secure_home_delivery$;
""")


class Migration(migrations.Migration):
    dependencies = [('shopping', '0016_alter_order_delivery_method_homedeliveryleg_and_more')]
    operations = [migrations.RunPython(secure_home_delivery_tables, migrations.RunPython.noop)]
