from django.db import migrations


SECURE_DELIVERY_TABLES_SQL = r"""
DO $secure_delivery_tables$
DECLARE
    table_name text;
    api_role text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['shopping_pickuppoint', 'shopping_shipmentevent']
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
$secure_delivery_tables$;
"""


def secure_delivery_tables(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_DELIVERY_TABLES_SQL)


class Migration(migrations.Migration):
    dependencies = [('shopping', '0011_shipment_tracking')]
    operations = [migrations.RunPython(secure_delivery_tables, reverse_code=migrations.RunPython.noop)]
